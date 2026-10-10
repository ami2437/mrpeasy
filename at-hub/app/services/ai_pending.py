"""AI reads that weren't sure of every item: the order / PO is created anyway, in Validation, with the lines it could
match; the rest wait here ("lines to match", stored as JSON on the record) until someone picks the item or drops the
line. Validate is refused while any are left.

    pending(rec)                                   -> the waiting lines
    create_for_validation(db, kind, draft, ...)    -> the new order / PO (status "validation", ai_source = the file)
    match(db, kind, rec_id, idx, item_id, qty, price, by) -> the line added with that item (and the PO's wording learned)
    discard(db, kind, rec_id, idx)                 -> dropped
"""
import json
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import CustomerOrder, PurchaseOrder


def pending(rec) -> List[dict]:
    try:
        return json.loads(rec.ai_pending or "[]")
    except (TypeError, ValueError):
        return []


def _set(rec, rows: List[dict]) -> None:
    rec.ai_pending = json.dumps(rows) if rows else None


def _keep(l: dict, kind: str) -> dict:
    """What the waiting line keeps from the read: enough to show it and to add it later."""
    return {"item_code": l.get("vendor_item_code") if kind == "vendor" else (l.get("item_code") or l.get("customer_item_code")),
            "description": l.get("description") or "", "line_note": l.get("line_note"),
            "quantity": l.get("quantity") or 1, "unit_price": l.get("unit_price") or 0, "delivery_date": l.get("delivery_date"),
            "candidates": [{k: c.get(k) for k in ("item_id", "code", "title", "score")} for c in (l.get("candidates") or [])[:4]],
            "new_item": l.get("new_item")}


def create_for_validation(db: Session, kind: str, d: dict, file_name: str, by: str, allow_duplicate: bool = False):
    """The read as a Validation record: header + every line it matched; the others wait in ai_pending."""
    from app import schemas
    from app.services.crud import CustomerOrderService, PurchaseOrderService
    sure = [l for l in d.get("lines") or [] if l.get("item_id")]
    unsure = [_keep(l, kind) for l in d.get("lines") or [] if not l.get("item_id") and not l.get("companion_of")]
    if kind == "customer":
        cid = d["customer"]["customer_id"]
        if sure:
            rec = CustomerOrderService.create(db, schemas.CustomerOrderCreate(
                allow_duplicate=allow_duplicate, customer_id=cid, po_number=d.get("po_number"), customer_po_date=d.get("order_date"), delivery_date=d.get("delivery_date"),
                job_number=d.get("job_number"), notes=d.get("notes"), ship_to_address=d.get("ship_to_address"),
                lines=[schemas.CustomerOrderLineCreate(item_id=l["item_id"], quantity=l.get("quantity") or 1, unit_price=l.get("unit_price") or 0,
                                                       delivery_date=l.get("delivery_date"), notes=l.get("line_note"),
                                                       source_code=l.get("customer_item_code") or l.get("item_code"),
                                                       source_description=l.get("description")) for l in sure]), by)
            rec.status = "validation"
        else:
            rec = CustomerOrderService.capture(db, cid, d.get("po_number"), by)
            rec.customer_po_date, rec.delivery_date, rec.job_number = d.get("order_date"), d.get("delivery_date"), d.get("job_number")
            rec.notes = d.get("notes")
            if d.get("ship_to_address"):
                rec.ship_to_address = d["ship_to_address"]
    else:
        vid = d["vendor"]["vendor_id"]
        if sure:
            rec = PurchaseOrderService.create(db, schemas.PurchaseOrderCreate(
                allow_duplicate=allow_duplicate, vendor_id=vid, vendor_so_number=d.get("document_number"), expected_date=d.get("expected_date"), notes=d.get("notes"),
                lines=[schemas.PurchaseOrderLineCreate(item_id=l["item_id"], quantity=l.get("quantity") or 1, unit_cost=l.get("unit_price") or 0,
                                                       vendor_item_code=l.get("vendor_item_code"), vendor_description=l.get("description"))
                       for l in sure]), by)
            rec.status = "validation"
        else:
            rec = PurchaseOrderService.capture(db, vid, d.get("document_number"), by, allow_duplicate=allow_duplicate)
            rec.expected_date, rec.notes = d.get("expected_date"), d.get("notes")
    rec.ai_source = file_name
    _set(rec, unsure)
    db.flush()
    return rec


def _record(db: Session, kind: str, rec_id: int):
    model = CustomerOrder if kind == "customer" else PurchaseOrder
    rec = db.get(model, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Not found")
    return rec


def match(db: Session, kind: str, rec_id: int, idx: int, item_id: int, quantity: Optional[float], price: Optional[float], by: str):
    from app import schemas
    from app.services.crud import CustomerOrderService, PurchaseOrderService
    rec = _record(db, kind, rec_id)
    rows = pending(rec)
    if not 0 <= idx < len(rows):
        raise HTTPException(status_code=400, detail="That line was already handled -- reload")
    row = rows[idx]
    qty = quantity if quantity is not None else row.get("quantity") or 1
    amt = price if price is not None else row.get("unit_price") or 0
    if kind == "customer":
        CustomerOrderService.add_line(db, rec.id, schemas.CustomerOrderLineAdd(item_id=item_id, quantity=qty, unit_price=amt,
                                                                               delivery_date=row.get("delivery_date"), notes=row.get("line_note")))
        from app.services import item_alias  # what their PO called it -> this item, for the next read
        item_alias.learn(db, "customer", rec.customer_id, item_id, row.get("item_code"), row.get("description"))
    else:
        PurchaseOrderService.add_line(db, rec.id, schemas.PurchaseOrderLineAdd(item_id=item_id, quantity=qty, unit_cost=amt,
                                                                               vendor_item_code=row.get("item_code"), vendor_description=row.get("description")))
    rec = _record(db, kind, rec_id)
    rows.pop(idx)
    _set(rec, rows)
    db.commit()
    db.refresh(rec)
    return rec


def discard(db: Session, kind: str, rec_id: int, idx: int):
    rec = _record(db, kind, rec_id)
    rows = pending(rec)
    if not 0 <= idx < len(rows):
        raise HTTPException(status_code=400, detail="That line was already handled -- reload")
    rows.pop(idx)
    _set(rec, rows)
    db.commit()
    db.refresh(rec)
    return rec
