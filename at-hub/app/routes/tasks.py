"""Admin task list: manual to-dos plus tasks suggested from the data (payments to record,
vendor invoices without an amount, orders missing their customer PO...)."""
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_perm, require_any
from app.models import Attachment, CustomerOrder, PurchaseOrder, Task, User, Vendor

router = APIRouter(prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(require_perm("tasks"))])
AUTO = "AT-HUB"


def _suggestions(db: Session) -> dict:
    """key -> (category, title, detail, link) for everything that currently needs doing."""
    out = {}
    vendors = dict(db.query(Vendor.id, Vendor.name).all())
    for po in db.query(PurchaseOrder).filter(PurchaseOrder.status != "cancelled").all():
        vendor = vendors.get(po.vendor_id, "")
        for b in po.bills:
            if b.amount < 0.005:
                out[f"bill-amount:{po.code}:{b.bill_number}"] = (
                    "Vendor invoices", f"Enter the amount of invoice {b.bill_number} on {po.code}",
                    f"{vendor}: MRPeasy doesn't export how this PO's total was split between its {len(po.bills)} invoices.",
                    f"purchase-orders.html?id={po.id}")
        billed = sum(b.amount for b in po.bills)
        paid = sum(p.amount for p in po.payments)
        if po.bills and paid < billed - 0.005:
            out[f"po-payment:{po.code}"] = (
                "PO payments", f"Record the payments on {po.code}",
                f"{vendor} · invoices {', '.join(b.bill_number for b in po.bills)} · ${billed:,.2f} billed, ${paid:,.2f} recorded. "
                "Check the PO's Payment Status in MRPeasy (Paid / Unpaid / partly) and enter each payment with its date.",
                f"purchase-orders.html?id={po.id}")
    with_po = {a.entity_id for a in db.query(Attachment).filter(Attachment.entity_type == "customer_order", Attachment.category == "customer_po").all()}
    for o in db.query(CustomerOrder).filter(CustomerOrder.status != "cancelled").all():
        if o.id not in with_po and o.po_number:
            out[f"co-po-pdf:{o.code}"] = ("Missing documents", f"Attach the customer PO to {o.code}",
                                          f"PO # {o.po_number}: no PDF found by File Matcher.", f"customer-orders.html?id={o.id}")
    # billing: an order with nothing left to ship or bill must have billed exactly what shipped
    from app.services import billing
    from app.models import BillingVariance, StockItem
    for row in billing.unbalanced_orders(db):
        o = row["order"]
        codes = dict(db.query(StockItem.id, StockItem.code).filter(StockItem.id.in_([r["item_id"] for r in row["lines"]])).all())
        notes = [v.reason for v in db.query(BillingVariance).filter(BillingVariance.order_id == o.id, BillingVariance.reason.isnot(None)).all()]
        out[f"billing:{o.code}"] = ("Billing", f"Billing on {o.code} doesn't match what shipped",
                                    "; ".join(f"#{r['line_no']} {codes.get(r['item_id'], '')}: shipped {r['shipped']:g}, billed {r['billed']:g} "
                                              f"({'over' if r['billed'] > r['shipped'] else 'under'} by {abs(r['billed'] - r['shipped']):g})" for r in row["lines"])
                                    + (f". Accepted because: {' / '.join(notes)}" if notes else "")
                                    + ". Correct it with a credit or a further invoice.", f"customer-orders.html?id={o.id}")
    return out


def refresh(db: Session) -> dict:
    """Raise new suggested tasks, close the ones whose work is done. Done/dismissed ones stay closed."""
    want, added, closed = _suggestions(db), 0, 0
    have = {t.key: t for t in db.query(Task).filter(Task.key.isnot(None)).all()}
    for key, (cat, title, detail, link) in want.items():
        t = have.get(key)
        if not t:
            db.add(Task(key=key, category=cat, title=title, detail=detail, link=link, created_by=AUTO))
            added += 1
        elif t.status == "open":
            t.detail, t.title = detail, title  # keep amounts current
    for key, t in have.items():
        if t.status == "open" and key not in want:
            t.status, t.done_by, t.done_at, closed = "done", AUTO + " (data shows it's done)", datetime.utcnow(), closed + 1
    db.commit()
    return {"added": added, "closed": closed}


def _out(t: Task) -> dict:
    return {c: getattr(t, c) for c in ("id", "key", "category", "title", "detail", "link", "status", "note", "created_by",
                                       "created_at", "done_by", "done_at")}


@router.get("/")
def list_tasks(db: Session = Depends(get_db)):
    refresh(db)
    return [_out(t) for t in db.query(Task).order_by(Task.status, Task.category, Task.id).all()]


class TaskIn(BaseModel):
    title: str
    detail: Optional[str] = None
    category: Optional[str] = None
    link: Optional[str] = None

    @field_validator("link")
    @classmethod
    def _safe_link(cls, v):
        """A web address or a page of AT-HUB -- never javascript: / data: (it's put straight into a clickable link)."""
        v = (v or "").strip()
        if not v:
            return None
        bare = re.sub(r"[\s\x00-\x1f]+", "", v)  # browsers drop these inside a URL ("java\nscript:")
        if re.match(r"^[a-z][a-z0-9+.-]*:", bare, re.I) and not re.match(r"^https?://", bare, re.I):
            raise ValueError("The link has to be a web address (https://...) or an AT-HUB page")
        return v


@router.post("/")
def add(data: TaskIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    if not data.title.strip():
        raise HTTPException(status_code=400, detail="Give the task a title")
    t = Task(title=data.title.strip(), detail=(data.detail or "").strip() or None, category=(data.category or "").strip() or "General",
             link=data.link, created_by=user.username)
    db.add(t)
    db.commit()
    return _out(t)


class TaskUpdate(BaseModel):
    status: Optional[str] = None  # open | done | dismissed
    note: Optional[str] = None


@router.put("/{task_id}")
def update(task_id: int, data: TaskUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    t = db.query(Task).filter(Task.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    if data.status:
        if data.status not in ("open", "done", "dismissed"):
            raise HTTPException(status_code=400, detail="status is open, done or dismissed")
        t.status = data.status
        t.done_by, t.done_at = (user.username, datetime.utcnow()) if data.status != "open" else (None, None)
    if data.note is not None:
        t.note = data.note.strip() or None
    db.commit()
    return _out(t)


@router.delete("/{task_id}")
def delete(task_id: int, db: Session = Depends(get_db)):
    t = db.query(Task).filter(Task.id == task_id).first()
    if not t or t.key:
        raise HTTPException(status_code=400, detail="Only manual tasks can be deleted (dismiss a suggested one)")
    db.delete(t)
    db.commit()
    return {"deleted": task_id}
