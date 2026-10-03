"""Recycle bin: everything deleted is kept, and can be put back.

A session hook (before_flush) sees every row about to be deleted. When the deletion includes a
record a person deletes on purpose (an order, a PO, an item, a line, a vendor invoice, a file...),
all the rows going in that one action -- the record and everything deleted with it (lines,
cancelled shipments, links) -- are saved as one bin entry. Restoring re-inserts them with the same
ids, parents first. Deleted files are moved to uploads/.trash and moved back on restore.

Housekeeping deletions (re-packing boxes, merging invoices) never involve those records, so they
don't fill the bin.
"""
import contextvars
import json
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

from fastapi import HTTPException
from sqlalchemy import DateTime, Date, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.settings import settings

# who is making the request (set by a middleware in main.py)
current_user: contextvars.ContextVar = contextvars.ContextVar("current_user", default=None)

# records people delete on purpose -> how the bin names them
BIN_KINDS = {
    "CustomerOrder": "Customer order", "PurchaseOrder": "Purchase order", "StockItem": "Stock item",
    "CustomerOrderLine": "Order line", "PurchaseOrderLine": "PO line", "Shipment": "Shipment",
    "VendorBill": "Vendor invoice", "VendorPayment": "Vendor payment", "InvoicePayment": "Customer payment",
    "PurchaseOrderPayment": "PO payment", "PurchaseOrderCharge": "PO charge", "Attachment": "File",
    "LandedCost": "Landed cost", "ProductGroup": "Product group", "VendorItem": "Vendor part # link",
    "User": "User", "Invoice": "Invoice",
}
PRIORITY = list(BIN_KINDS)  # the first kind found names the entry (an order beats its lines)


def trash_dir() -> Path:
    d = Path(settings.upload_dir).resolve() / ".trash"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _label(obj) -> str:
    for attr in ("code", "bill_number", "name", "filename", "username", "lot_code", "vendor_item_code", "description"):
        v = getattr(obj, attr, None)
        if v:
            return str(v)
    if type(obj).__name__ in ("CustomerOrderLine", "PurchaseOrderLine"):
        parent = getattr(obj, "order", None) or getattr(obj, "po", None)
        item = getattr(obj, "item_id", None)
        return f"{getattr(parent, 'code', '?')} line (item {item}, qty {getattr(obj, 'quantity', '?'):g})"
    return f"#{getattr(obj, 'id', '?')}"


def _row(obj) -> Dict[str, Any]:
    cols = {}
    for c in obj.__table__.columns:
        v = getattr(obj, c.key, None)
        cols[c.name] = v.isoformat() if isinstance(v, (datetime, date)) else v
    return {"table": obj.__table__.name, "cols": cols}


def _before_flush(session: Session, flush_context, instances) -> None:
    if session.info.get("restoring") or session.info.get("no_bin"):
        return
    deleted = list(session.deleted)
    tops = [o for o in deleted if type(o).__name__ in BIN_KINDS]
    if not tops:
        return
    from app.models import DeletedRecord
    tops.sort(key=lambda o: PRIORITY.index(type(o).__name__))
    main = tops[0]
    same = [o for o in tops if type(o) is type(main)]
    label = _label(main) if len(same) == 1 else f"{len(same)} × {BIN_KINDS[type(main).__name__].lower()}s ({', '.join(_label(o) for o in same[:3])}{'…' if len(same) > 3 else ''})"
    session.add(DeletedRecord(kind=BIN_KINDS[type(main).__name__], label=label[:300],
                              rows=json.dumps([_row(o) for o in deleted]), deleted_by=current_user.get(),
                              deleted_at=datetime.utcnow()))


def install() -> None:
    event.listen(Session, "before_flush", _before_flush)


def move_to_trash(stored_name: str) -> None:
    """A deleted attachment's file is kept (until the bin entry is emptied)."""
    root = Path(settings.upload_dir).resolve()
    src = (root / stored_name).resolve()
    if root in src.parents and src.exists():
        dst = trash_dir() / stored_name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))


def restore(db: Session, entry_id: int) -> Dict[str, Any]:
    from app.models import Base, DeletedRecord
    entry = db.query(DeletedRecord).filter(DeletedRecord.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Not in the recycle bin")
    if entry.restored_at:
        raise HTTPException(status_code=400, detail="Already restored")
    rows: List[Dict[str, Any]] = json.loads(entry.rows)
    tables = {t.name: t for t in Base.metadata.sorted_tables}
    order = {t.name: i for i, t in enumerate(Base.metadata.sorted_tables)}  # parents before children
    if any(r["table"] == "shipments" and r["cols"].get("status") in ("new", "ready") for r in rows):
        raise HTTPException(status_code=400, detail="An open shipment's stock bookings were released when it was deleted -- create the shipment again instead")
    db.info["restoring"] = True
    renumbered = []
    try:
        new_id: Dict[tuple, int] = {}  # (table, old id) -> id it gets back when the old one was reused meanwhile
        for r in sorted(rows, key=lambda r: order.get(r["table"], 999)):
            t = tables[r["table"]]
            vals = {}
            for c in t.columns:
                v = r["cols"].get(c.name)
                if v is not None and isinstance(c.type, (DateTime, Date)) and isinstance(v, str):
                    v = datetime.fromisoformat(v) if isinstance(c.type, DateTime) else date.fromisoformat(v[:10])
                for fk in c.foreign_keys:  # follow a parent that came back under a new id
                    v = new_id.get((fk.column.table.name, v), v)
                vals[c.name] = v
            pk = t.primary_key.columns.values()[0] if len(t.primary_key.columns) == 1 else None
            if pk is not None and vals.get(pk.name) is not None and db.execute(t.select().where(pk == vals[pk.name])).first():
                old = vals[pk.name]
                vals[pk.name] = (db.execute(t.select().with_only_columns(pk).order_by(pk.desc()).limit(1)).scalar() or 0) + 1
                new_id[(t.name, old)] = vals[pk.name]
            # a code / number someone has used since gets "-R" (C89127 -> C89127-R)
            for c in t.columns:
                if c.unique and isinstance(vals.get(c.name), str) and db.execute(t.select().where(c == vals[c.name])).first():
                    renumbered.append(f"{vals[c.name]} -> {vals[c.name]}-R")
                    vals[c.name] = f"{vals[c.name]}-R"
            db.execute(t.insert().values(**vals))
        for r in rows:  # files back from the trash
            if r["table"] == "attachments":
                src = trash_dir() / r["cols"]["stored_name"]
                if src.exists():
                    dst = Path(settings.upload_dir).resolve() / r["cols"]["stored_name"]
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dst))
        entry.restored_at = datetime.utcnow()
        entry.restored_by = current_user.get()
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Can't restore: something now uses the same number or id ({str(exc.orig)[:120]})")
    finally:
        db.info.pop("restoring", None)
    return {"restored": entry.label, "rows": len(rows), "renumbered": renumbered}


def purge(db: Session, entry_id: int) -> None:
    """Empty one entry for good (its trashed files too)."""
    from app.models import DeletedRecord
    entry = db.query(DeletedRecord).filter(DeletedRecord.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Not in the recycle bin")
    for r in json.loads(entry.rows):
        if r["table"] == "attachments" and not entry.restored_at:
            (trash_dir() / r["cols"]["stored_name"]).unlink(missing_ok=True)
    db.info["no_bin"] = True
    try:
        db.delete(entry)
        db.commit()
    finally:
        db.info.pop("no_bin", None)
