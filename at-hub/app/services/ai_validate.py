"""Validate a saved order against its own document with several independent readers.

Customer order <- the customer's PO PDF attached to it, read by:
    the exact layout reader (Chart / Hudson), the local AI model, and Claude (redacted text, on click only).
Purchase order <- the vendor's quote / order confirmation (or invoice) attached to it, read by the local AI and Claude.

Each reader's result is compared with what's saved. A difference several readers agree on is a real
discrepancy; one only a single reader sees (when others read the same thing as the order) is flagged
as a possible misread. Nothing is changed -- this only reports.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.models import Attachment, CustomerOrder, PurchaseOrder, StockItem

PRICE_TOL = 0.00005


def _code_key(code: Optional[str]) -> str:
    """'53552', '53552-HPC', ' 53552 ' -> '53552': Chart prints our code without our -HPC suffix."""
    c = (code or "").strip().upper()
    return c[:-4] if c.endswith("-HPC") else c


def _latest_file(db: Session, entity_type: str, entity_id: int, categories: List[str]) -> Attachment:
    for cat in categories:
        att = (db.query(Attachment).filter(Attachment.entity_type == entity_type, Attachment.entity_id == entity_id,
                                           Attachment.category == cat).order_by(Attachment.id.desc()).first())
        if att:
            return att
    raise HTTPException(status_code=400, detail="There's no document attached to check against -- attach the PDF first")


def _file_bytes(att: Attachment) -> bytes:
    root = Path(settings.upload_dir).resolve()
    path = (root / att.stored_name).resolve()
    if root not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="The attached file is missing from the server")
    return path.read_bytes()


def _run(readers: Dict[str, Callable[[], Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Run the readers side by side; each returns {"po_number", "lines": [{code|item_id, qty, price}], "total"}."""
    def one(name, fn):
        t = time.time()
        try:
            return {"name": name, "ok": True, "data": fn(), "seconds": round(time.time() - t, 1)}
        except HTTPException as exc:
            return {"name": name, "ok": False, "error": exc.detail, "seconds": round(time.time() - t, 1)}
        except Exception as exc:  # a reader failing never stops the others
            return {"name": name, "ok": False, "error": str(exc)[:200], "seconds": round(time.time() - t, 1)}
    with ThreadPoolExecutor(max_workers=len(readers)) as pool:
        return list(pool.map(lambda kv: one(*kv), readers.items()))


def _compare(saved: Dict[str, Any], sources: List[Dict[str, Any]], key_label: Callable[[Any], str]) -> Dict[str, Any]:
    """saved / each source: {"ref", "lines": {key: {"qty", "price", "label"}}, "total"}.
    Returns findings with what each reader saw and how many of them flag it."""
    ran = [s for s in sources if s["ok"]]
    findings: Dict[Tuple, Dict[str, Any]] = {}

    def flag(kind, key, field, saved_val, src, val, text):
        f = findings.setdefault((kind, key, field), {"kind": kind, "line": key_label(key) if key is not None else None, "field": field,
                                                      "saved": saved_val, "seen": {}, "flagged_by": [], "message": text})
        f["seen"][src] = val
        f["flagged_by"].append(src)

    for s in ran:
        d, name = s["data"], s["name"]
        if d.get("ref") and saved.get("ref") and d["ref"].strip().upper() != saved["ref"].strip().upper():
            flag("header", None, "PO #", saved["ref"], name, d["ref"], "PO # differs")
        for key, dl in d["lines"].items():
            sl = saved["lines"].get(key)
            if not sl:
                flag("missing", key, "line", None, name, f'{dl["qty"]:g} × {dl["price"]}', "On the document but not on the order")
                continue
            if dl.get("qty") is not None and abs(dl["qty"] - sl["qty"]) > 1e-6:
                flag("diff", key, "quantity", sl["qty"], name, dl["qty"], "Quantity differs")
            if dl.get("price") is not None and abs(dl["price"] - sl["price"]) > PRICE_TOL:
                flag("diff", key, "price", sl["price"], name, dl["price"], "Price differs")
        for key, sl in saved["lines"].items():
            if key not in d["lines"] and not sl.get("companion"):
                flag("extra", key, "line", f'{sl["qty"]:g} × {sl["price"]}', name, None, "On the order but not on the document")
        if d.get("total") is not None and saved.get("total") is not None and abs(d["total"] - saved["total"]) > 0.02:
            flag("header", None, "total", round(saved["total"], 2), name, d["total"], "Document total differs from the order's billable lines")

    out = []
    for f in findings.values():
        # readers that ran but saw nothing wrong here agree with the order
        agree_with_order = [s["name"] for s in ran if s["name"] not in f["flagged_by"]]
        n = len(f["flagged_by"])
        f["severity"] = "confirmed" if n >= 2 or (n == 1 and len(ran) == 1) else "check"
        f["agrees_with_order"] = agree_with_order
        out.append(f)
    out.sort(key=lambda f: (f["severity"] != "confirmed", f["kind"] != "header", f["line"] or ""))
    return {"findings": out, "readers": [{k: v for k, v in s.items() if k != "data"} | ({"lines": len(s["data"]["lines"])} if s["ok"] else {})
                                         for s in sources],
            "clean": not out, "readers_ok": len(ran)}


# ---------------- customer orders ----------------
def validate_order(db: Session, order_id: int, use_claude: bool = True) -> Dict[str, Any]:
    from app.services import ai_orders, ai_cloud, customer_po_templates
    order = db.query(CustomerOrder).filter(CustomerOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    att = _latest_file(db, "customer_order", order.id, ["customer_po"])
    text = ai_orders.pdf_text(_file_bytes(att))
    items = {i.id: i for i in db.query(StockItem).all()}

    saved_lines = {}
    for l in order.lines:
        code = items[l.item_id].code if l.item_id in items else str(l.item_id)
        key = _code_key(code)
        if key in saved_lines:  # same item twice: add up
            saved_lines[key]["qty"] += l.quantity
            continue
        saved_lines[key] = {"qty": l.quantity, "price": l.unit_price, "label": code, "companion": l.unit_price == 0}
    saved = {"ref": order.po_number, "lines": saved_lines,
             "total": sum(l.quantity * l.unit_price for l in order.lines)}

    def shape(data: Dict[str, Any]) -> Dict[str, Any]:
        lines = {}
        for raw in data.get("lines") or []:
            code = raw.get("item_code") or raw.get("customer_item_code")
            qty, price = ai_orders._num(raw.get("quantity")), ai_orders._num(raw.get("unit_price"))
            if not code or qty is None:
                continue
            if price is not None and price <= 0.011 and qty <= 1.01:  # a kit header (job #, 1 x $0.01) -- never an order line
                continue
            key = _code_key(code)
            if key in lines:
                lines[key]["qty"] += qty
            else:
                lines[key] = {"qty": qty, "price": price}
        total = sum(l["qty"] * (l["price"] or 0) for l in lines.values()) if lines else None
        return {"ref": data.get("po_number"), "lines": lines, "total": total}

    readers: Dict[str, Callable] = {}
    if customer_po_templates.parse(text):
        readers["Exact reader"] = lambda: shape(customer_po_templates.parse(text))
    else:
        # the local model misreads layouts we have an exact reader for (Chart's run-together columns: it takes the
        # extended $ for the quantity), so it only reads the others
        readers["Local AI"] = lambda: shape(ai_orders._ask_model(text))
    if use_claude:
        def claude():
            safe, _removed, _cust = ai_cloud.redact_customer_po(text, db)
            prompt = ai_orders.EXTRACTION_PROMPT.replace("Purchase order text:", "Names, addresses and contact details were replaced "
                                                         "with [CUSTOMER], [PERSON], [EMAIL] etc. before you saw this; ignore them.\nPurchase order text:")
            return shape(ai_cloud.ask_claude(prompt, safe, ai_cloud.SCHEMAS["customer_po"])["data"])
        readers["Claude"] = claude
    result = _compare(saved, _run(readers), lambda k: saved_lines.get(k, {}).get("label", k))
    result.update({"document": att.filename, "attachment_id": att.id, "order": order.code})
    return result


# ---------------- purchase orders ----------------
def validate_po(db: Session, po_id: int, use_claude: bool = True) -> Dict[str, Any]:
    from app.services import ai_docs
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    att = _latest_file(db, "purchase_order", po.id, ["vendor_quote", "vendor_invoice", "purchase_order"])
    data = _file_bytes(att)
    kind = "vendor_invoice" if att.category == "vendor_invoice" else "vendor_order"
    items = {i.id: i for i in db.query(StockItem).all()}

    saved_lines = {}
    for l in po.lines:
        k = l.item_id
        if k in saved_lines:
            saved_lines[k]["qty"] += l.quantity
        else:
            saved_lines[k] = {"qty": l.quantity, "price": l.unit_cost, "label": items[k].code if k in items else str(k)}
    saved = {"ref": po.vendor_so_number, "lines": saved_lines, "total": sum(l.quantity * l.unit_cost for l in po.lines)}

    # the PO's own vendor part #s decide first: a document can name the wrong vendor (our letterhead on a quote)
    norm = lambda c: "".join(ch for ch in (c or "").upper() if ch.isalnum())
    by_vcode = {norm(l.vendor_item_code): l.item_id for l in po.lines if l.vendor_item_code}

    def shape(out: Dict[str, Any]) -> Dict[str, Any]:
        docs = out.get("invoices") or [out]
        lines = {}
        for d in docs:
            for l in d.get("lines") or []:
                if l.get("quantity") is None:
                    continue
                key = by_vcode.get(norm(l.get("vendor_item_code"))) or l.get("item_id") or f'? {l.get("vendor_item_code") or l.get("description") or "line"}'
                cur = lines.setdefault(key, {"qty": 0, "price": l.get("unit_price")})
                cur["qty"] += l["quantity"]
        ref = out.get("document_number") if kind == "vendor_order" else None
        goods = sum(l["qty"] * (l["price"] or 0) for l in lines.values()) if lines else None
        return {"ref": ref, "lines": lines, "total": goods}

    readers = {"Local AI": lambda: shape(ai_docs.extract(db, kind, data, att.filename, po_id=po.id, engine="local"))}
    if use_claude:
        readers["Claude"] = lambda: shape(ai_docs.extract(db, kind, data, att.filename, po_id=po.id, engine="claude"))
    result = _compare(saved, _run(readers), lambda k: saved_lines.get(k, {}).get("label") or (items[k].code if k in items else str(k)))
    for f in result["findings"]:  # vendor docs number differently -- a ref mismatch is only a hint
        if f["field"] == "PO #":
            f["field"], f["message"] = "Vendor SO #", "Document number differs from the Vendor SO # on the PO"
    result.update({"document": att.filename, "attachment_id": att.id, "order": po.code})
    return result
