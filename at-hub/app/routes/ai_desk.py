"""AI Desk: drop in any documents (one or many), optionally say what they are or what to do, and
AT-HUB works out for each one what it is, which record it belongs to and what to do with it:

  customer PO      -> attach to the order with that PO #, or create a draft order from it
  vendor invoice   -> add it to its PO as a vendor invoice (number, dates, total) with the PDF attached
  vendor SO/quote  -> attach to its PO, or create a draft PO from it
  MTR              -> attach to its PO as a test report
  proof of delivery-> attach to the shipment and mark it delivered

Nothing is saved by /analyze; /apply does exactly the action shown, re-checked on the server.
Uses the local AI model (private) plus the exact readers AT-HUB already has."""
import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_role
from app.models import Attachment, CustomerOrder, PurchaseOrder, Shipment, User, VendorBill
from app.routes.attachments import MAX_BYTES, store_file
from app.routes.file_matcher import _create_bytes, _name_keys, _norm, _read_bytes, _record_keys

router = APIRouter(prefix="/api/ai-desk", tags=["ai-desk"], dependencies=[Depends(require_role("manager"))])

KINDS = {"customer_po": "Customer PO", "vendor_invoice": "Vendor invoice", "vendor_order": "Vendor SO / quote / confirmation",
         "mtr": "Material test report (MTR)", "pod": "Proof of delivery", "other": "Other document"}
HINTS = [("customer_po", r"customer po|sales order|create (the )?orders?|customer order"),
         ("vendor_invoice", r"vendor invoice|supplier invoice|\bbill\b|invoice"),
         ("vendor_order", r"\bso\b|order confirmation|quote|proforma|\bpi\b|vendor order"),
         ("mtr", r"\bmtr\b|mill test|test report|cert"), ("pod", r"\bpod\b|proof of delivery|signed|delivery receipt|\bbol\b")]
CLASSIFY = """You sort business documents for AMERICAN TRADERS / ATIND SUPPLIES, a fastener distributor.
Answer JSON {"kind": one of "customer_po" (a customer ordering FROM us: their purchase order to us),
"vendor_invoice" (a supplier billing US), "vendor_order" (a supplier's quote, proforma or order confirmation to us),
"mtr" (mill / material test report, certificate), "pod" (signed bill of lading or delivery receipt), "other"}.
Document:
---
"""


def _blob(f: UploadFile) -> bytes:
    data = f.file.read()
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(status_code=400, detail=f"{f.filename}: empty or larger than 25 MB")
    return data


def _text(data: bytes, name: str) -> str:
    if not name.lower().endswith(".pdf"):
        return ""
    from app.services.ai_orders import pdf_text
    try:
        return pdf_text(data)
    except HTTPException:
        return ""


def _classify(db: Session, data: bytes, name: str, text: str, instruction: str) -> tuple:
    """(kind, how we know)."""
    for kind, pat in HINTS:
        if instruction and re.search(pat, instruction, re.I):
            return kind, "you said so"
    from app.services import customer_po_templates
    if text and customer_po_templates.parse(text):
        return "customer_po", "known customer PO layout"
    low = (text + " " + name).lower()
    if re.search(r"mill test|test report|heat (no|number|#)|certificate of (conformance|compliance)", low):
        return "mtr", "reads like a test report"
    try:
        from app.services.ai_orders import _ask_model
        from app.services.ai_docs import _read
        doc = _read(data, name)
        kind = (_ask_model(doc["text"] or name, prompt=CLASSIFY, images=doc["images"][:1] or None) or {}).get("kind")
        if kind in KINDS:
            return kind, "AI"
    except Exception:
        pass
    if "invoice" in low:
        return "vendor_invoice", "the word invoice"
    return "other", "couldn't tell"


def _find(db: Session, kind_of_record: str, text: str, name: str, extra=()) -> list:
    """Records whose number is a whole word of the document (or its name). [(id, code, number)]"""
    keys = _record_keys(db, kind_of_record)
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9/\-]*", f"{name} {text[:20000]}")
    found = {}
    for k in _name_keys(" ".join(words)) | {_norm(x) for x in extra if x}:
        for rid in keys.get(k, []):
            found.setdefault(rid, k)
    model = CustomerOrder if kind_of_record == "customer" else PurchaseOrder
    return [(rid, db.query(model.code).filter(model.id == rid).scalar(), k) for rid, k in found.items()]


@router.post("/analyze")
def analyze(file: UploadFile = File(...), instruction: str = Form(""), kind: str = Form(""), db: Session = Depends(get_db)):
    try:
        return _analyze(file, instruction, kind, db)
    except HTTPException as e:  # AI offline, unreadable PDF...: report it on the file's card
        return {"file": file.filename, "kind": kind or "other", "kind_label": KINDS.get(kind, "Other document"), "kind_why": "",
                "summary": "", "target": None, "actions": [], "problems": [str(e.detail)]}


def _analyze(file: UploadFile, instruction: str, kind: str, db: Session):
    data, name = _blob(file), file.filename or "document"
    text = _text(data, name)
    if kind not in KINDS:
        kind, why = _classify(db, data, name, text, instruction)
    else:
        why = "chosen"
    out = {"file": name, "kind": kind, "kind_label": KINDS[kind], "kind_why": why, "summary": "", "target": None, "actions": [], "problems": []}

    def target(rec_type, rid, code, how):
        out["target"] = {"type": rec_type, "id": rid, "code": code, "why": how}

    if kind == "customer_po":
        hits = _find(db, "customer", text, name)
        if len(hits) == 1:
            target("customer_order", hits[0][0], hits[0][1], f"PO # {hits[0][2]} is on {hits[0][1]}")
            out["actions"].append({"id": "attach", "label": f"Attach to {hits[0][1]} as its customer PO", "ready": True})
        else:
            r = _read_bytes(db, "customer", data, name, name)
            d = r["draft"]
            out["summary"] = f"{d.get('customer_name') or '?'} · PO {d.get('po_number') or '?'} · {len(d.get('lines') or [])} lines"
            out["actions"].append({"id": "create_order", "label": "Create a draft sales order with this PDF attached", "ready": r["ready"]})
            out["problems"] += r["problems"]
    elif kind in ("vendor_invoice", "vendor_order", "mtr"):
        po_hint = None
        if kind == "vendor_invoice":
            from app.services import ai_docs
            inv = ai_docs.extract(db, "vendor_invoice", data, name)
            po_hint = inv.get("po_number")
            out["bill"] = {"bill_number": inv.get("invoice_number"), "bill_date": inv.get("invoice_date"),
                           "due_date": inv.get("due_date"), "amount": inv.get("total")}
            out["summary"] = f"{(inv.get('vendor') or {}).get('suggested_name') or inv.get('vendor_name') or '?'} · invoice {inv.get('invoice_number') or '?'} · " \
                             f"total {('$%s' % format(inv['total'], ',.2f')) if inv.get('total') is not None else '?'}"
        hits = _find(db, "vendor", text, name, extra=[po_hint])
        if len(hits) == 1:
            target("purchase_order", hits[0][0], hits[0][1], f"{hits[0][2]} found in the document")
        elif len(hits) > 1:
            out["problems"].append("mentions several POs: " + ", ".join(h[1] for h in hits))
        else:
            out["problems"].append("no PO # or vendor SO # of ours found in it")
        t = out["target"]
        if kind == "vendor_invoice":
            b = out["bill"]
            ok = bool(t and b["bill_number"] and b["amount"])
            if t and b["bill_number"] and db.query(VendorBill).filter(VendorBill.po_id == t["id"], VendorBill.bill_number == b["bill_number"]).first():
                ok = False
                out["problems"].append(f"invoice {b['bill_number']} is already on {t['code']}")
            out["actions"].append({"id": "add_bill", "label": f"Add as vendor invoice on {t['code'] if t else 'its PO'} and attach", "ready": ok})
            out["actions"].append({"id": "attach", "label": "Only attach the file", "ready": bool(t)})
        elif kind == "vendor_order":
            out["actions"].append({"id": "attach", "label": f"Attach to {t['code']} as the vendor SO / quote" if t else "Attach to its PO", "ready": bool(t)})
            if not t:
                r = _read_bytes(db, "vendor", data, name, name)
                out["summary"] = f"{(r['draft'].get('vendor') or {}).get('suggested_name') or '?'} · SO {r['draft'].get('document_number') or '?'} · {len(r['draft'].get('lines') or [])} lines"
                out["actions"].append({"id": "create_po", "label": "Create a draft PO with this PDF attached", "ready": r["ready"]})
                out["problems"] += r["problems"]
        else:
            out["actions"].append({"id": "attach", "label": f"Attach to {t['code']} as an MTR" if t else "Attach to its PO as an MTR", "ready": bool(t)})
    elif kind == "pod":
        from app.services import ai_docs
        r = ai_docs.extract(db, "pod", data, name)
        m = r.get("shipment_matches") or []
        if len(m) == 1:
            target("shipment", m[0]["shipment_id"], m[0]["code"], "reference on the document: " + ", ".join(m[0].get("matched_on") or []))
        elif len(m) > 1:
            out["problems"].append("matches several shipments: " + ", ".join(x["code"] for x in m))
        else:
            out["problems"].append("no shipment / order / PO # of ours found on it")
        out["actions"].append({"id": "attach", "label": f"Attach to {out['target']['code']} as proof of delivery (marks it delivered)" if out["target"] else "Attach to its shipment", "ready": bool(out["target"])})
    else:
        for rec, hits in (("customer_order", _find(db, "customer", text, name)), ("purchase_order", _find(db, "vendor", text, name))):
            if len(hits) == 1 and not out["target"]:
                target(rec, hits[0][0], hits[0][1], f"{hits[0][2]} found in the document")
        out["actions"].append({"id": "attach", "label": f"Attach to {out['target']['code']}" if out["target"] else "Attach (pick the record)", "ready": bool(out["target"])})
    t = out["target"]
    if t and db.query(Attachment).filter(Attachment.entity_type == t["type"], Attachment.entity_id == t["id"],
                                         Attachment.filename == name).first():
        out["problems"].append(f"this file is already attached to {t['code']}")
        for a in out["actions"]:
            if a["id"] in ("attach", "add_bill"):
                a["ready"] = False
    return out


ATTACH_CATEGORY = {"customer_po": "customer_po", "vendor_invoice": "vendor_invoice", "vendor_order": "vendor_quote", "mtr": "mtr", "pod": "pod", "other": "other"}


def _resolve(db: Session, rec_type: str, code: str):
    model = {"customer_order": CustomerOrder, "purchase_order": PurchaseOrder, "shipment": Shipment}.get(rec_type)
    rec = model and db.query(model).filter(model.code == (code or "").strip()).first()
    if not rec:
        raise HTTPException(status_code=400, detail=f"No {rec_type.replace('_', ' ')} {code}")
    return rec


@router.post("/apply")
def apply(file: UploadFile = File(...), action: str = Form(...), kind: str = Form(...), target_type: str = Form(""),
                target_code: str = Form(""), bill: str = Form("{}"), db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    data, name = _blob(file), file.filename or "document"
    if action == "create_order":
        return {"done": "created", **_create_bytes(db, "customer", data, name, name, user)}
    if action == "create_po":
        return {"done": "created", **_create_bytes(db, "vendor", data, name, name, user)}
    rec = _resolve(db, target_type, target_code)
    if action == "add_bill":
        if target_type != "purchase_order":
            raise HTTPException(status_code=400, detail="A vendor invoice goes on a PO")
        b = json.loads(bill or "{}")
        if not b.get("bill_number") or not b.get("amount"):
            raise HTTPException(status_code=400, detail="Invoice # and total are needed")
        att = store_file(db, "purchase_order", rec.id, "vendor_invoice", name, None, data, "Added by AI Desk", user.username)
        db.flush()
        from app import schemas
        from app.services.crud import VendorBillService
        VendorBillService.create(db, rec.id, schemas.VendorBillInput(bill_number=b["bill_number"], amount=float(b["amount"]), bill_date=b.get("bill_date"),
                                                                      due_date=b.get("due_date"), attachment_id=att.id, note="Added by AI Desk"), created_by=user.username)
        return {"done": "bill", "record": rec.code, "record_id": rec.id}
    if action == "attach":
        category = ATTACH_CATEGORY.get(kind, "other")
        allowed = {"customer_order": {"customer_po", "mtr", "other"}, "purchase_order": {"vendor_invoice", "vendor_quote", "mtr", "other"}, "shipment": {"pod", "other"}}
        if category not in allowed[target_type]:
            category = "other"
        store_file(db, target_type, rec.id, category, name, None, data, "Added by AI Desk", user.username)
        if target_type == "shipment" and category == "pod" and not rec.delivered_at:
            from app.services.crud import ShipmentService
            if rec.status in ShipmentService.SHIPPED_STATUSES:
                ShipmentService.mark_delivered(db, rec, None, user.username, commit=False)
        db.commit()
        return {"done": "attached", "record": rec.code, "record_id": rec.id, "category": category}
    raise HTTPException(status_code=400, detail="Unknown action")
