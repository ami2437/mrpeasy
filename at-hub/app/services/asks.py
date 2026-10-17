"""Assign / Ask a person on a record (user, 2026-10-17): "tag Niraj to validate PO325380, with a comment so he knows what
to look at" -- on customer orders, shipments, POs, invoices and Vendor Account lines.

The ask lands in that person's To-Do (their first list), linked to the record; the record shows it as a banner; either
side can reply; it closes itself when the action happens (a PO / order no longer in Validation) or when they say done --
and the asker gets a To-Do line saying so.

Permissions (the user's rule): you can only ask someone whose role can SEE the record -- its module, and "See prices" too
for money records (POs, invoices, Vendor Accounts). Anyone else is listed with the reason and refused by the server."""
import json
from datetime import datetime
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import (CustomerOrder, Invoice, PurchaseOrder, RecordAsk, Shipment, TodoItem, TodoList, User, VacctAccount, VacctBill,
                        VacctPayment)
from app.services import permissions

# entity -> (model, permission to see it, money record?, page)
ENTITIES = {
    "customer_order": (CustomerOrder, "orders.view", False, "customer-orders.html"),
    "shipment": (Shipment, "shipments.view", False, "shipments.html"),
    "purchase_order": (PurchaseOrder, "purchasing", True, "purchase-orders.html"),
    "invoice": (Invoice, "invoices", True, "invoices.html"),
    "vacct_bill": (VacctBill, "vendor_accounts", True, "vendor-accounts.html"),
    "vacct_payment": (VacctPayment, "vendor_accounts", True, "vendor-accounts.html"),
}
ACTIONS = {"validate": "Validate", "review": "Review", "approve": "Approve", "check_prices": "Check prices", "other": "Look at"}
MODULE_WORDS = {"orders.view": "customer orders", "shipments.view": "shipments", "purchasing": "purchase orders", "invoices": "invoices",
                "vendor_accounts": "Vendor Accounts"}


def _entity(db: Session, entity_type: str, entity_id: int):
    if entity_type not in ENTITIES:
        raise HTTPException(status_code=400, detail="Asks can be made on orders, shipments, POs, invoices and Vendor Account lines")
    rec = db.get(ENTITIES[entity_type][0], entity_id)
    if not rec:
        raise HTTPException(status_code=404, detail="That record isn't there")
    return rec


def label(db: Session, entity_type: str, rec) -> str:
    if entity_type in ("vacct_bill", "vacct_payment"):
        acc = db.get(VacctAccount, rec.account_id)
        what = f"invoice {rec.bill_number or 'SO ' + (rec.so_number or '')}" if entity_type == "vacct_bill" else f"payment {rec.code or ''} ({rec.amount:,.2f})"
        return f"{what} in the {acc.vendor_name if acc else ''} Vendor Account"
    return getattr(rec, "code", None) or f"#{rec.id}"


def link(db: Session, entity_type: str, rec) -> str:
    page = ENTITIES[entity_type][3]
    if entity_type in ("vacct_bill", "vacct_payment"):
        return f"{page}?id={rec.account_id}"
    return f"{page}?id={rec.id}"


def can_see(db: Session, user: User, entity_type: str) -> Optional[str]:
    """None when this user can see that kind of record, else why not (for the picker and the refusal)."""
    _model, perm, money, _page = ENTITIES[entity_type]
    if not user.is_active:
        return "account deactivated"
    perms = permissions.perms_for(db, user.role)
    if perm not in perms:
        return f"can't see {MODULE_WORDS.get(perm, perm)}"
    if money and "money.view" not in perms:
        return "can't see prices / money"
    return None


def eligible(db: Session, entity_type: str, entity_id: int, me: User) -> List[dict]:
    _entity(db, entity_type, entity_id)
    out = []
    for u in db.query(User).order_by(User.full_name, User.username).all():
        why = can_see(db, u, entity_type)
        out.append({"username": u.username, "name": u.full_name or u.username, "ok": why is None, "why": why, "me": u.username == me.username})
    return out


def _first_list(db: Session, owner: str) -> TodoList:
    lst = db.query(TodoList).filter(TodoList.owner == owner).order_by(TodoList.position, TodoList.id).first()
    if not lst:
        lst = TodoList(name="Reminders", color="#2f6fed", owner=owner, shared=False)
        db.add(lst)
        db.flush()
    return lst


def _todo(db: Session, owner: str, title: str, notes: str, entity_type: str, entity_id: int, due=None) -> TodoItem:
    lst = _first_list(db, owner)
    item = TodoItem(list_id=lst.id, title=title[:300], notes=notes, due_date=due, priority=2, entity_type=entity_type, entity_id=entity_id,
                    created_by="AT-HUB")
    db.add(item)
    db.flush()
    return item


def _name(db: Session, username: str) -> str:
    u = db.query(User).filter(User.username == username).first()
    return (u.full_name or u.username) if u else username


def _out(db: Session, a: RecordAsk) -> dict:
    return {"id": a.id, "entity_type": a.entity_type, "entity_id": a.entity_id, "label": a.entity_label, "action": a.action,
            "action_label": ACTIONS.get(a.action, a.action), "comment": a.comment, "asked_user": a.asked_user, "asked_name": _name(db, a.asked_user),
            "asked_by": a.asked_by, "asked_by_name": _name(db, a.asked_by), "due_date": a.due_date.date().isoformat() if a.due_date else None,
            "status": a.status, "done_by": a.done_by, "done_how": a.done_how, "done_at": a.done_at.isoformat() + "Z" if a.done_at else None,
            "replies": json.loads(a.replies or "[]"), "created_at": a.created_at.isoformat() + "Z" if a.created_at else None,
            "link": None}


def create(db: Session, me: User, entity_type: str, entity_id: int, user: str, action: str, comment: Optional[str], due_date=None) -> dict:
    rec = _entity(db, entity_type, entity_id)
    if can_see(db, me, entity_type):
        raise HTTPException(status_code=403, detail="You can't see this record")
    target = db.query(User).filter(User.username == user).first()
    if not target:
        raise HTTPException(status_code=404, detail="No such user")
    why = can_see(db, target, entity_type)
    if why:
        raise HTTPException(status_code=403, detail=f"{target.full_name or target.username} doesn't have permission to see this ({why}) -- "
                                                    "they can't be tagged on it")
    if action not in ACTIONS:
        raise HTTPException(status_code=400, detail=f"Pick what to do: {', '.join(ACTIONS.values())}")
    lab = label(db, entity_type, rec)
    due = datetime.fromisoformat(str(due_date)[:10]) if due_date else None
    a = RecordAsk(entity_type=entity_type, entity_id=entity_id, entity_label=lab, action=action, comment=(comment or "").strip() or None,
                  asked_user=target.username, asked_by=me.username, due_date=due, record_status=getattr(rec, "status", None))
    db.add(a)
    db.flush()
    todo_type, todo_id = (entity_type, entity_id)
    item = _todo(db, target.username, f"{ACTIONS[action]} {lab} — {me.full_name or me.username} asks",
                 (a.comment or "") + f"\n\nOpen: {link(db, entity_type, rec)}", todo_type, todo_id, due)
    a.todo_item_id = item.id
    db.commit()
    return _out(db, a)


def _close(db: Session, a: RecordAsk, by: str, how: str) -> None:
    a.status, a.done_by, a.done_how, a.done_at = "done", by, how, datetime.utcnow()
    it = db.get(TodoItem, a.todo_item_id) if a.todo_item_id else None
    if it and not it.done:
        it.done, it.done_at, it.done_by = True, datetime.utcnow(), by
    if a.asked_by != by:  # tell the asker
        _todo(db, a.asked_by, f"{_name(db, by)} — {a.entity_label}: {ACTIONS.get(a.action, a.action).lower()} done ({how})",
              None, a.entity_type, a.entity_id)


def auto_close(db: Session, asks: List[RecordAsk]) -> None:
    """A Validate ask closes itself once the PO / order moves on from the status it had when asked -- out of Validation, or
    a draft confirmed / ordered (or the record is gone)."""
    changed = False
    for a in asks:
        if a.status != "open":
            continue
        model = ENTITIES.get(a.entity_type, (None,))[0]
        rec = db.get(model, a.entity_id) if model else None
        if rec is None:
            a.status, a.done_how, a.done_at = "done", "record deleted", datetime.utcnow()
            changed = True
        elif (a.action == "validate" and a.entity_type in ("purchase_order", "customer_order")
              and rec.status != (a.record_status or "validation")):  # validated (a draft: confirmed / ordered), or cancelled
            how = "cancelled" if rec.status == "cancelled" else "validated"
            _close(db, a, getattr(rec, "validated_by", None) or a.asked_user, how)
            changed = True
    if changed:
        db.commit()


def for_record(db: Session, me: User, entity_type: str, entity_id: int) -> List[dict]:
    _entity(db, entity_type, entity_id)
    if can_see(db, me, entity_type):
        raise HTTPException(status_code=403, detail="You can't see this record")
    asks = db.query(RecordAsk).filter(RecordAsk.entity_type == entity_type, RecordAsk.entity_id == entity_id).order_by(RecordAsk.id.desc()).all()
    auto_close(db, asks)
    return [_out(db, a) for a in asks]


def mine(db: Session, me: User) -> List[dict]:
    asks = db.query(RecordAsk).filter(RecordAsk.status == "open", (RecordAsk.asked_user == me.username) | (RecordAsk.asked_by == me.username)).all()
    auto_close(db, asks)
    out = []
    for a in asks:
        if a.status == "open":
            d = _out(db, a)
            rec = db.get(ENTITIES[a.entity_type][0], a.entity_id)
            d["link"] = link(db, a.entity_type, rec) if rec else None
            out.append(d)
    return out


def _get(db: Session, ask_id: int) -> RecordAsk:
    a = db.get(RecordAsk, ask_id)
    if not a:
        raise HTTPException(status_code=404, detail="Ask not found")
    return a


def reply(db: Session, me: User, ask_id: int, text: str) -> dict:
    a = _get(db, ask_id)
    if me.username not in (a.asked_user, a.asked_by) and me.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only the two people on this ask can reply")
    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Write the reply first")
    rows = json.loads(a.replies or "[]")
    rows.append({"by": me.username, "name": me.full_name or me.username, "at": datetime.utcnow().isoformat() + "Z", "text": text[:2000]})
    a.replies = json.dumps(rows)
    other = a.asked_by if me.username == a.asked_user else a.asked_user
    _todo(db, other, f"{me.full_name or me.username} replied on {a.entity_label}: {text[:120]}", None, a.entity_type, a.entity_id)
    db.commit()
    return _out(db, a)


def done(db: Session, me: User, ask_id: int) -> dict:
    a = _get(db, ask_id)
    if me.username not in (a.asked_user, a.asked_by) and me.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only the two people on this ask can close it")
    if a.status == "open":
        _close(db, a, me.username, "marked done")
        db.commit()
    return _out(db, a)


def withdraw(db: Session, me: User, ask_id: int) -> dict:
    a = _get(db, ask_id)
    if me.username != a.asked_by and me.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only the person who asked can take it back")
    a.status, a.done_by, a.done_how, a.done_at = "withdrawn", me.username, "withdrawn", datetime.utcnow()
    it = db.get(TodoItem, a.todo_item_id) if a.todo_item_id else None
    if it and not it.done:
        db.delete(it)
    db.commit()
    return _out(db, a)
