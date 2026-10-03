"""AI scanning for vendor documents and delivery proofs, on the same private (local) model
as customer-order import. Each scan only *suggests*: the page fills its form and shows what
matched or didn't, and nothing is saved until the user accepts.

Kinds:
  vendor_invoice  vendor's invoice -> bill fields (#, dates, total, S&H) + check against a PO's lines
  vendor_order    vendor quote / order confirmation -> draft purchase order
  pod             signed delivery receipt (photo or PDF) -> received-by, date, matching shipments
"""
import base64
import io
import re
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.models import CustomerOrder, PurchaseOrder, Shipment, StockItem, Vendor, VendorItem
from app.services.ai_orders import _ask_model, _date, _norm, _num, pdf_text
from app.services.item_match import ItemMatcher, pick
import difflib

LINES_SCHEMA = """  "lines": [
    {
      "vendor_item_code": string|null,   // the vendor's part / item number exactly as printed
      "description": string|null,
      "quantity": number|null,
      "unit_price": number|null          // price per single unit; convert per-100 / per-M pricing to per unit
    }
  ],"""

PROMPTS = {
    "vendor_invoice": """You read invoices that vendors send to our company (we are the buyer) and turn them into JSON.
One file can hold SEVERAL invoices (e.g. one per shipment of the same order), often after an order
acknowledgement or packing list. Return one entry per INVOICE -- an order acknowledgement, quote, packing list
or statement is not an invoice, skip it. An invoice continues across its pages ("1 of 2", "2 of 2").
Return ONLY a JSON object: {"invoices": [ <one object per invoice> ]}, each object with exactly these keys:
{
  "vendor_name": string|null,        // the company that ISSUED the invoice
  "invoice_number": string|null,
  "invoice_date": "YYYY-MM-DD"|null,
  "due_date": "YYYY-MM-DD"|null,     // if only terms like "Net 30" are given, leave null and put the terms in notes
  "po_number": string|null,          // OUR purchase order number the invoice references
""" + LINES_SCHEMA + """
  "subtotal": number|null,           // goods only
  "shipping_handling": number|null,  // freight + shipping + handling + delivery charges added together
  "tax": number|null,
  "total": number|null,              // the invoice grand total / amount due
  "notes": string|null
}
Rules: never invent values -- null when something isn't on the document; numbers without $ or commas;
freight, shipping, handling and delivery rows go into shipping_handling, NOT into lines;
each invoice's lines, subtotal and total are only its own -- never the order acknowledgement's.

Invoice text:
---
""",
    "vendor_order": """You read quotes and order confirmations that vendors send to our company (we are the buyer) and turn them into JSON.
Return ONLY a JSON object with exactly these keys:
{
  "vendor_name": string|null,        // the company that ISSUED the document (the seller)
  "document_number": string|null,    // quote # / confirmation # / sales order #
  "document_date": "YYYY-MM-DD"|null,
  "expected_date": "YYYY-MM-DD"|null, // promised ship or delivery date
""" + LINES_SCHEMA + """
  "shipping_handling": number|null,  // freight + shipping + handling quoted, if any
  "total": number|null,
  "notes": string|null               // terms, lead time, validity worth keeping
}
Rules: never invent values -- null when something isn't on the document; numbers without $ or commas;
freight, shipping and handling rows go into shipping_handling, NOT into lines.

Document text:
---
""",
    "pod": """You read proof-of-delivery documents (signed bills of lading, delivery receipts, packing slips with a signature)
and turn them into JSON. Return ONLY a JSON object with exactly these keys:
{
  "received_by": string|null,        // printed name of the person who signed for the delivery
  "signed": boolean,                 // true if a signature is visible
  "delivery_date": "YYYY-MM-DD"|null,
  "references": [string],            // every PO #, order #, shipment #, BOL # or packing-slip # printed on it
  "pieces": string|null,             // pallets / cartons received, as written
  "exceptions": string|null,         // damage, shortage or other notes written on it; null if clean
  "notes": string|null
}
Rules: never invent values -- null when something isn't legible.

Document text (may be empty when only an image is attached -- then read the image):
---
""",
}

IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic")


def _pdf_images(file_bytes: bytes, limit: int = 4) -> List[str]:
    """Embedded page images of a scanned PDF (a scan is one image per page), base64 for the vision model."""
    from pypdf import PdfReader
    out = []
    try:
        for page in PdfReader(io.BytesIO(file_bytes)).pages:
            for img in page.images:
                out.append(base64.b64encode(img.data).decode())
                break
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out


def _read(file_bytes: bytes, filename: str) -> Dict[str, Any]:
    """(text, images) for the model: text from a digital PDF, images for photos and scans."""
    name = (filename or "").lower()
    if name.endswith(IMAGE_TYPES):
        return {"text": "", "images": [base64.b64encode(file_bytes).decode()]}
    if not name.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Upload a PDF or a photo")
    try:
        return {"text": pdf_text(file_bytes), "images": None}
    except HTTPException:
        images = _pdf_images(file_bytes)
        if not images:
            raise HTTPException(status_code=400, detail="Couldn't read any text or page images from this PDF")
        return {"text": "", "images": images}


def _match_vendor(db: Session, name: Optional[str], text: str = "") -> Dict[str, Any]:
    """Rank our vendors: the name the AI read, plus any of a vendor's phone numbers, email
    domains or website found anywhere in the document (letterheads are often misread)."""
    from app.models import CompanyProfile
    from app.services.ai_cloud import REDACT_TERMS
    vendors = db.query(Vendor).filter(Vendor.is_active == True).all()  # noqa: E712
    # the AI often reads OUR name off the bill-to / ship-to block: that's never the vendor
    company = db.query(CompanyProfile).first()
    ours = {_norm(t) for t in REDACT_TERMS + [company.name if company else ""] if t}
    target = _norm(name)
    if target and any(o and (o in target or target in o) for o in ours):
        target = ""
    digits = re.sub(r"\D", "", text or "")
    low = (text or "").lower()
    flat = _norm(text or "")
    # part #s each vendor uses (learned from past POs): a vendor's own codes on the document point to it,
    # even when the name is only in a logo picture (Ziegler's sales orders)
    tokens = {t.upper() for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9/.\-]{4,}", text or "")}
    part_hits = {}
    for vi in db.query(VendorItem).all():
        code = (vi.vendor_item_code or "").strip().upper()
        if len(code) >= 5 and not code.isdigit() and code in tokens:
            part_hits.setdefault(vi.vendor_id, set()).add(code)
    ranked = []
    for v in vendors:
        vn = _norm(v.name)
        s, why = 0.0, ""
        if target and vn:
            s = 1.0 if vn == target else (0.92 if (vn in target or target in vn) else difflib.SequenceMatcher(None, vn, target).ratio())
            why = "name"
        # the vendor's own name printed on the document (letterhead, remit-to, footer)
        words = [w for w in re.split(r"[^a-z0-9]+", (v.name or "").lower()) if len(w) >= 3 and w not in ("inc", "llc", "ltd", "corp", "company", "the", "and", "co")]
        if vn and len(vn) >= 5 and vn in flat:
            s, why = max(s, 0.95), "its name is on the document"
        elif words and len(words[0]) >= 5 and re.search(rf"\b{re.escape(words[0])}\b", low) and (len(words) == 1 or re.search(rf"\b{re.escape(words[1])}\b", low)):
            s, why = max(s, 0.9), "its name is on the document"
        if part_hits.get(v.id):
            hits = sorted(part_hits[v.id])
            s, why = max(s, 0.9 if len(hits) == 1 else 0.95), f"its part # {', '.join(hits[:3])} is on the document"
        card = v.details  # every phone / email / website on the vendor's contact card
        phones = re.split(r"[,;/]", v.phone or "") + [r.get("value") or "" for r in card["phones"]] + [p.get("phone") or "" for p in card["people"]]
        emails = re.split(r"[,;\s]+", v.email or "") + [r.get("value") or "" for r in card["emails"]] + [p.get("email") or "" for p in card["people"]]
        sites = [r.get("value") or "" for r in card["websites"]]
        for ph in phones:
            d = re.sub(r"\D", "", ph)[-10:]
            if len(d) >= 7 and d in digits:
                s, why = max(s, 0.97), "phone number on the document"
        for em in emails:
            dom = em.split("@")[-1].lower() if "@" in em else ""
            if dom and dom not in ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com") and dom in low:
                s, why = max(s, 0.97), "email domain on the document"
        web = re.search(r"web:\s*(?:https?://)?(?:www\.)?([^\s/]+)", (v.address or "").lower())
        for site in ([web.group(1)] if web else []) + [re.sub(r"^(https?://)?(www\.)?", "", x.lower()).split("/")[0] for x in sites]:
            if site and site in low:
                s, why = max(s, 0.97), "website on the document"
        if s >= 0.45:
            ranked.append({"vendor_id": v.id, "name": v.name, "code": v.code, "score": round(s, 2), "why": why})
    ranked.sort(key=lambda r: -r["score"])
    top = ranked[0] if ranked else None
    sure = top and top["score"] >= 0.8 and (len(ranked) == 1 or top["score"] - ranked[1]["score"] >= 0.1)
    return {"vendor_id": top["vendor_id"] if sure else None, "confidence": top["score"] if top else 0,
            "suggested_name": top["name"] if top else None, "candidates": ranked[:5]}


def _vendor_lines(db: Session, vendor_id: Optional[int], raw_lines: Any) -> List[Dict[str, Any]]:
    """Clean the model's lines and map each to our item: the vendor's part # first (learned
    cross-reference), then our own codes, then the description."""
    items = db.query(StockItem).filter(StockItem.is_active == True).all()  # noqa: E712
    xref = {}
    if vendor_id:
        xref = {m.vendor_item_code: m.item_id for m in db.query(VendorItem).filter(VendorItem.vendor_id == vendor_id).all()}
    from app.services import item_alias
    matcher = ItemMatcher(items, xref, item_alias.for_party(db, "vendor", vendor_id))
    lines = []
    for raw in raw_lines or []:
        if not isinstance(raw, dict):
            continue
        qty = _num(raw.get("quantity"))
        code = raw.get("vendor_item_code")
        if not qty and not code and not raw.get("description"):
            continue
        candidates = matcher.rank([code], raw.get("description"))
        item_id = pick(candidates)
        lines.append({"vendor_item_code": code, "description": raw.get("description"), "quantity": qty,
                      "unit_price": _num(raw.get("unit_price")), "item_id": item_id,
                      "match": candidates[0]["why"] if item_id else None,
                      "confidence": candidates[0]["score"] if candidates else 0, "candidates": candidates})
    return lines


def _check_invoice_against_po(po: PurchaseOrder, lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pair each invoice line with a PO line and flag quantity / price differences. A line with no
    certain item is matched by description against this PO's own lines only -- a far smaller,
    surer set than the whole catalogue."""
    checks, used = [], set()
    from sqlalchemy.orm import object_session
    ids = {l.item_id for l in po.lines}
    po_items = ItemMatcher(object_session(po).query(StockItem).filter(StockItem.id.in_(ids)).all() if ids else [])
    for ln in lines:
        same = [l for l in po.lines if l.id not in used and (
            (ln.get("item_id") and l.item_id == ln["item_id"]) or
            (ln.get("vendor_item_code") and _norm(l.vendor_item_code) == _norm(ln["vendor_item_code"])))]
        # the same item on several PO lines (split deliveries): the line with this quantity, else the first
        po_line = next((l for l in same if ln.get("quantity") is not None and abs(l.quantity - ln["quantity"]) < 1e-6), same[0] if same else None)
        if not po_line and ln.get("description"):
            open_ids = {l.item_id for l in po.lines if l.id not in used}
            best = po_items.rank([ln.get("vendor_item_code")], ln["description"], top=3, only_ids=open_ids)
            if best and best[0]["score"] >= 0.5:
                po_line = next(l for l in po.lines if l.id not in used and l.item_id == best[0]["item_id"])
                ln = {**ln, "item_id": po_line.item_id, "match": f"PO line by description ({best[0]['why']})"}
        issues = []
        if not po_line:
            issues.append("not on this PO")
        else:
            used.add(po_line.id)
            if ln.get("quantity") is not None and abs(ln["quantity"] - po_line.quantity) > 1e-6:
                issues.append(f"qty {ln['quantity']:g} vs PO {po_line.quantity:g}")
            if ln.get("unit_price") is not None and abs(ln["unit_price"] - po_line.unit_cost) > 0.00005:
                issues.append(f"price {ln['unit_price']:g} vs PO {po_line.unit_cost:g}")
        checks.append({**ln, "po_line_id": po_line.id if po_line else None, "issues": issues})
    return checks


def _invoice_out(db: Session, po, doc: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    """One invoice read from the document: bill fields, its lines checked against the PO, and anything that doesn't add up."""
    vendor = _match_vendor(db, data.get("vendor_name"), doc["text"])
    lines = _vendor_lines(db, po.vendor_id if po else vendor.get("vendor_id"), data.get("lines"))
    total, sh = _num(data.get("total")), _num(data.get("shipping_handling")) or 0
    subtotal = _num(data.get("subtotal"))
    lines_sum = round(sum((l["quantity"] or 0) * (l["unit_price"] or 0) for l in lines), 2)
    inv = {
        "vendor_name": data.get("vendor_name"), "vendor": vendor,
        "invoice_number": data.get("invoice_number"), "invoice_date": _date(data.get("invoice_date")),
        "due_date": _date(data.get("due_date")), "po_number": data.get("po_number"),
        "subtotal": subtotal, "shipping_handling": sh, "tax": _num(data.get("tax")), "total": total,
        "lines_sum": lines_sum, "notes": data.get("notes"),
        "lines": _check_invoice_against_po(po, lines) if po else lines,
    }
    warnings = []
    if po:
        if data.get("po_number") and _norm(data["po_number"]) != _norm(po.code):
            warnings.append(f"Invoice references PO {data['po_number']}, this is {po.code}")
        if vendor.get("vendor_id") and vendor["vendor_id"] != po.vendor_id:
            warnings.append(f"Invoice looks like it's from {vendor.get('suggested_name')}, not this PO's vendor")
    goods = subtotal if subtotal is not None else lines_sum
    if total is not None and goods and abs(goods + sh + (_num(data.get("tax")) or 0) - total) > 0.02:
        warnings.append(f"Goods {goods:,.2f} + S&H {sh:,.2f}{' + tax' if data.get('tax') else ''} doesn't add up to the total {total:,.2f}")
    inv["warnings"] = warnings
    return inv


def extract(db: Session, kind: str, file_bytes: bytes, filename: str, po_id: Optional[int] = None,
            engine: str = "local") -> Dict[str, Any]:
    if kind not in PROMPTS:
        raise HTTPException(status_code=400, detail=f"Unknown document kind: {kind}")
    doc = _read(file_bytes, filename)
    out: Dict[str, Any] = {"kind": kind, "scanned_image": bool(doc["images"]), "text_preview": doc["text"][:3000]}
    if engine == "claude":
        # Cloud, on an explicit click only: our details and bank numbers are redacted locally first,
        # and only that text is sent (never the PDF). A scan is an image we can't redact, so it stays here.
        from app.services import ai_cloud
        if kind not in ai_cloud.SCHEMAS:
            raise HTTPException(status_code=400, detail="Ask Claude isn't available for this kind of document")
        if doc["images"]:
            raise HTTPException(status_code=400, detail="This is a scanned image: it can't be redacted, so it isn't sent to Claude. Use the local AI scan.")
        safe, removed = ai_cloud.redact(doc["text"], db)
        result = ai_cloud.ask_claude(PROMPTS[kind], safe, ai_cloud.SCHEMAS[kind])
        data = result["data"]
        out.update({"model": f"Claude ({result['model']})", "cloud": True, "redacted": removed, "tokens": result["tokens"],
                    "text_preview": safe[:3000]})
    else:
        data = _ask_model(doc["text"], prompt=PROMPTS[kind], images=doc["images"])
        out["model"] = settings.ai_vision_model if doc["images"] else settings.ai_model

    if kind == "vendor_invoice":
        # one file may hold several invoices (one per shipment): each is checked on its own
        po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first() if po_id else None
        found = data.get("invoices") if isinstance(data.get("invoices"), list) else [data]
        invoices = [_invoice_out(db, po, doc, inv) for inv in found if isinstance(inv, dict)] or [_invoice_out(db, po, doc, {})]
        out.update(invoices[0])
        out["invoices"] = invoices

    elif kind == "vendor_order":
        vendor = _match_vendor(db, data.get("vendor_name"), doc["text"])
        out.update({
            "vendor_name": data.get("vendor_name"), "vendor": vendor,
            "document_number": data.get("document_number"), "document_date": _date(data.get("document_date")),
            "expected_date": _date(data.get("expected_date")), "shipping_handling": _num(data.get("shipping_handling")),
            "total": _num(data.get("total")), "notes": data.get("notes"),
            "lines": _vendor_lines(db, vendor.get("vendor_id"), data.get("lines")),
        })

    else:  # pod
        refs = [r for r in (data.get("references") or []) if isinstance(r, str) and r.strip()]
        keys = {_norm(r) for r in refs}
        matches = []
        if keys:
            for s in db.query(Shipment).filter(Shipment.status.in_(("shipped", "delivered", "invoiced"))).all():
                order = db.query(CustomerOrder).filter(CustomerOrder.id == s.order_id).first()
                hits = [v for v in (s.code, order and order.code, order and order.po_number, s.tracking_number) if v and _norm(v) in keys]
                if hits:
                    matches.append({"shipment_id": s.id, "code": s.code, "status": s.status, "matched_on": hits})
        out.update({
            "received_by": data.get("received_by"), "signed": bool(data.get("signed")),
            "delivery_date": _date(data.get("delivery_date")), "references": refs,
            "pieces": data.get("pieces"), "exceptions": data.get("exceptions"), "notes": data.get("notes"),
            "shipment_matches": matches,
        })
    return out
