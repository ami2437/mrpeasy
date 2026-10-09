"""To-Do (iOS Reminders style), sticky notes on records, and the calendar in the top bar.

Days are each person's own: "today", a due time and a reminder time are read in their time zone (app/services/clock.py).
To-do lists are personal unless shared. Sticky notes belong to a record and are seen by everyone who can open it."""
from datetime import datetime, time, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import get_current_active_user
from app.models import (CustomerOrder, Customer, Invoice, PurchaseOrder, Shipment, StickyNote, TodoItem, TodoList, User, Vendor)
from app.services import clock
from app.services.permissions import has

router = APIRouter(prefix="/api", tags=["planner"])

NOTE_ENTITIES = {"customer_order": (CustomerOrder, "orders.view", "customer-orders.html"),
                 "purchase_order": (PurchaseOrder, "purchasing", "purchase-orders.html"),
                 "shipment": (Shipment, "shipments.view", "shipments.html")}
COLORS = ("yellow", "pink", "green", "blue")


# ---------- shared bits ----------
def _day(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"'{value}' isn't a date (YYYY-MM-DD)")


def _moment(day: Optional[datetime], hhmm: Optional[str], tz: str) -> Optional[datetime]:
    """A day + a time of day in the user's zone -> UTC moment (no time -> None: an all-day item)."""
    if not day or not hhmm:
        return None
    try:
        t = datetime.strptime(hhmm[:5], "%H:%M").time()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"'{hhmm}' isn't a time (HH:MM)")
    return clock.to_utc(datetime.combine(day.date(), t), tz)


def _today(user: User) -> datetime:
    return clock.today(user.effective_timezone)


def _record_label(db: Session, entity_type: Optional[str], entity_id: Optional[int]) -> Optional[dict]:
    if not entity_type or entity_type not in NOTE_ENTITIES or not entity_id:
        return None
    model, _perm, page = NOTE_ENTITIES[entity_type]
    rec = db.get(model, entity_id)
    if not rec:
        return None
    who, extra = "", {}
    if entity_type == "customer_order":
        c = db.get(Customer, rec.customer_id)
        who = c.name if c else ""
        extra = {"po_number": rec.po_number, "job_number": rec.job_number}
    elif entity_type == "purchase_order":
        v = db.get(Vendor, rec.vendor_id)
        who = v.name if v else ""
        extra = {"vendor_so": rec.vendor_so_number}
    elif entity_type == "shipment":
        o = db.get(CustomerOrder, rec.order_id)
        c = db.get(Customer, o.customer_id) if o else None
        who = c.name if c else ""
        extra = {"order_id": o.id if o else None, "order_code": o.code if o else None, "po_number": o.po_number if o else None,
                 "job_number": o.job_number if o else None}
    return {"type": entity_type, "id": entity_id, "code": rec.code, "who": who, "status": rec.status,
            "link": f"{page}?id={entity_id}", **extra}


# ---------- To-Do ----------
def _visible_lists(db: Session, user: User):
    return db.query(TodoList).filter(or_(TodoList.owner == user.username, TodoList.shared.is_(True)))


def _ensure_default_list(db: Session, user: User) -> None:
    if not db.query(TodoList).filter(TodoList.owner == user.username).first():
        db.add(TodoList(name="Reminders", color="#2f6fed", owner=user.username, shared=False))
        db.commit()


def _list_or_404(db: Session, user: User, list_id: int, edit: bool = False) -> TodoList:
    lst = _visible_lists(db, user).filter(TodoList.id == list_id).first()
    if not lst:
        raise HTTPException(status_code=404, detail="To-do list not found")
    if edit and lst.owner != user.username:
        raise HTTPException(status_code=403, detail=f"Only {lst.owner} can change this list")
    return lst


def _item_out(db: Session, i: TodoItem, lists: dict) -> dict:
    lst = lists.get(i.list_id)
    return {"id": i.id, "kind": "todo", "list_id": i.list_id, "list_name": lst.name if lst else "", "list_color": lst.color if lst else None,
            "title": i.title, "notes": i.notes, "due_date": i.due_date, "due_at": i.due_at, "priority": i.priority, "flagged": i.flagged,
            "done": i.done, "done_at": i.done_at, "done_by": i.done_by, "created_by": i.created_by, "created_at": i.created_at,
            "record": _record_label(db, i.entity_type, i.entity_id)}


def _note_out(db: Session, n: StickyNote) -> dict:
    return {"id": n.id, "kind": "note", "entity_type": n.entity_type, "entity_id": n.entity_id, "text": n.text, "color": n.color,
            "remind_date": n.remind_date, "remind_at": n.remind_at, "done": n.done, "done_by": n.done_by, "done_at": n.done_at,
            "created_by": n.created_by, "created_at": n.created_at, "updated_by": n.updated_by, "updated_at": n.updated_at,
            "record": _record_label(db, n.entity_type, n.entity_id)}


def _notes_user_can_see(db: Session, user: User):
    types = [t for t, (_m, perm, _p) in NOTE_ENTITIES.items() if has(user, perm)]
    return db.query(StickyNote).filter(StickyNote.entity_type.in_(types)) if types else None


@router.get("/todo/lists")
def todo_lists(db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    _ensure_default_list(db, user)
    lists = _visible_lists(db, user).order_by(TodoList.shared, TodoList.position, TodoList.id).all()
    out = []
    for l in lists:
        n = db.query(TodoItem).filter(TodoItem.list_id == l.id, TodoItem.done.is_(False)).count()
        out.append({"id": l.id, "name": l.name, "color": l.color, "owner": l.owner, "shared": l.shared, "mine": l.owner == user.username, "open": n})
    return out


class ListIn(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    shared: Optional[bool] = None


@router.post("/todo/lists")
def todo_list_create(data: ListIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    if not (data.name or "").strip():
        raise HTTPException(status_code=400, detail="Name the list")
    lst = TodoList(name=data.name.strip(), color=data.color or "#2f6fed", owner=user.username, shared=bool(data.shared))
    db.add(lst)
    db.commit()
    return {"id": lst.id}


@router.put("/todo/lists/{list_id}")
def todo_list_update(list_id: int, data: ListIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    lst = _list_or_404(db, user, list_id, edit=True)
    if data.name is not None:
        if not data.name.strip():
            raise HTTPException(status_code=400, detail="Name the list")
        lst.name = data.name.strip()
    if data.color is not None:
        lst.color = data.color
    if data.shared is not None:
        lst.shared = data.shared
    db.commit()
    return {"id": lst.id}


@router.delete("/todo/lists/{list_id}", status_code=204)
def todo_list_delete(list_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    lst = _list_or_404(db, user, list_id, edit=True)
    if db.query(TodoList).filter(TodoList.owner == user.username).count() <= 1:
        raise HTTPException(status_code=400, detail="Keep at least one list of your own")
    db.query(TodoItem).filter(TodoItem.list_id == lst.id).delete()
    db.delete(lst)
    db.commit()
    return Response(status_code=204)


@router.get("/todo/items")
def todo_items(view: str = "today", list_id: Optional[int] = None, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """view: today (due today or overdue) | scheduled | flagged | all | completed | list (with list_id).
    Sticky-note reminders join today / scheduled / all."""
    _ensure_default_list(db, user)
    lists = {l.id: l for l in _visible_lists(db, user).all()}
    q = db.query(TodoItem).filter(TodoItem.list_id.in_(list(lists) or [0]))
    today = _today(user)
    if view == "completed":
        q = q.filter(TodoItem.done.is_(True))
    elif view == "list":
        if list_id not in lists:
            raise HTTPException(status_code=404, detail="To-do list not found")
        q = q.filter(TodoItem.list_id == list_id)
    else:
        q = q.filter(TodoItem.done.is_(False))
        if view == "today":
            q = q.filter(TodoItem.due_date.isnot(None), TodoItem.due_date <= today)
        elif view == "scheduled":
            q = q.filter(TodoItem.due_date.isnot(None))
        elif view == "flagged":
            q = q.filter(TodoItem.flagged.is_(True))
    items = [_item_out(db, i, lists) for i in q.all()]
    if view in ("today", "scheduled", "all"):
        nq = _notes_user_can_see(db, user)
        if nq is not None:
            nq = nq.filter(StickyNote.done.is_(False), StickyNote.remind_date.isnot(None))
            if view == "today":
                nq = nq.filter(StickyNote.remind_date <= today)
            items += [_note_out(db, n) for n in nq.all()]
    far = datetime(9999, 1, 1)
    key = lambda r: (r.get("done", False), r.get("due_date") or r.get("remind_date") or far, r.get("due_at") or r.get("remind_at") or far,
                     -(r.get("priority") or 0), r["id"])
    if view == "completed":
        return sorted(items, key=lambda r: r.get("done_at") or far, reverse=True)[:200]
    return sorted(items, key=key)


@router.get("/todo/counts")
def todo_counts(db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """The smart lists' numbers (and the top bar's badge: due today or overdue)."""
    lists = [l.id for l in _visible_lists(db, user).all()] or [0]
    base = db.query(TodoItem).filter(TodoItem.list_id.in_(lists), TodoItem.done.is_(False))
    today = _today(user)
    notes_today = notes_sched = 0
    nq = _notes_user_can_see(db, user)
    if nq is not None:
        nq = nq.filter(StickyNote.done.is_(False), StickyNote.remind_date.isnot(None))
        notes_sched = nq.count()
        notes_today = nq.filter(StickyNote.remind_date <= today).count()
    t = base.filter(TodoItem.due_date.isnot(None), TodoItem.due_date <= today).count() + notes_today
    return {"today": t, "scheduled": base.filter(TodoItem.due_date.isnot(None)).count() + notes_sched,
            "flagged": base.filter(TodoItem.flagged.is_(True)).count(), "all": base.count() + notes_sched,
            "completed": db.query(TodoItem).filter(TodoItem.list_id.in_(lists), TodoItem.done.is_(True)).count()}


class ItemIn(BaseModel):
    list_id: Optional[int] = None
    title: Optional[str] = None
    notes: Optional[str] = None
    due_date: Optional[str] = None   # YYYY-MM-DD ("" clears)
    due_time: Optional[str] = None   # HH:MM in the user's zone ("" = all day)
    priority: Optional[int] = None
    flagged: Optional[bool] = None
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None


def _item_or_404(db: Session, user: User, item_id: int) -> TodoItem:
    i = db.get(TodoItem, item_id)
    if not i or not _visible_lists(db, user).filter(TodoList.id == i.list_id).first():
        raise HTTPException(status_code=404, detail="To-do not found")
    return i


def _apply_item(db: Session, user: User, i: TodoItem, data: ItemIn) -> None:
    if data.list_id is not None:
        _list_or_404(db, user, data.list_id)
        i.list_id = data.list_id
    if data.title is not None:
        if not data.title.strip():
            raise HTTPException(status_code=400, detail="Give the to-do a title")
        i.title = data.title.strip()
    if data.notes is not None:
        i.notes = data.notes.strip() or None
    if data.due_date is not None:
        i.due_date = _day(data.due_date)
    if data.due_date is not None or data.due_time is not None:
        i.due_at = _moment(i.due_date, data.due_time, user.effective_timezone) if (data.due_time or "") else None
    if data.priority is not None:
        i.priority = max(0, min(3, int(data.priority)))
    if data.flagged is not None:
        i.flagged = data.flagged
    if data.entity_type is not None:
        if data.entity_type and data.entity_type not in NOTE_ENTITIES:
            raise HTTPException(status_code=400, detail="Unknown record type")
        i.entity_type, i.entity_id = (data.entity_type or None), (data.entity_id if data.entity_type else None)


@router.post("/todo/items")
def todo_item_create(data: ItemIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    _ensure_default_list(db, user)
    if not data.list_id:
        data.list_id = db.query(TodoList).filter(TodoList.owner == user.username).order_by(TodoList.position, TodoList.id).first().id
    if not (data.title or "").strip():
        raise HTTPException(status_code=400, detail="Give the to-do a title")
    i = TodoItem(list_id=data.list_id, title="x", created_by=user.username)
    _apply_item(db, user, i, data)
    db.add(i)
    db.commit()
    lists = {l.id: l for l in _visible_lists(db, user).all()}
    return _item_out(db, i, lists)


@router.put("/todo/items/{item_id}")
def todo_item_update(item_id: int, data: ItemIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    i = _item_or_404(db, user, item_id)
    _apply_item(db, user, i, data)
    db.commit()
    lists = {l.id: l for l in _visible_lists(db, user).all()}
    return _item_out(db, i, lists)


@router.post("/todo/items/{item_id}/toggle")
def todo_item_toggle(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    i = _item_or_404(db, user, item_id)
    i.done = not i.done
    i.done_at, i.done_by = (datetime.utcnow(), user.username) if i.done else (None, None)
    db.commit()
    return {"id": i.id, "done": i.done}


@router.delete("/todo/items/{item_id}", status_code=204)
def todo_item_delete(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    db.delete(_item_or_404(db, user, item_id))
    db.commit()
    return Response(status_code=204)


# ---------- sticky notes ----------
def _check_record(db: Session, user: User, entity_type: str, entity_id: int) -> None:
    if entity_type not in NOTE_ENTITIES:
        raise HTTPException(status_code=400, detail="Notes go on customer orders, purchase orders and shipments")
    model, perm, _page = NOTE_ENTITIES[entity_type]
    if not has(user, perm):
        raise HTTPException(status_code=403, detail="Your role can't see that record")
    if not db.get(model, entity_id):
        raise HTTPException(status_code=404, detail="That record no longer exists")


@router.get("/notes")
def notes(entity_type: str, entity_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    _check_record(db, user, entity_type, entity_id)
    rows = (db.query(StickyNote).filter(StickyNote.entity_type == entity_type, StickyNote.entity_id == entity_id)
            .order_by(StickyNote.done, StickyNote.id.desc()).all())
    return [_note_out(db, n) for n in rows]


@router.get("/notes/all")
def notes_all(q: str = "", status: str = "open", kind: str = "", db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """Every sticky note this person can see (Sticky Notes page): status open | done | all, kind = a record type,
    q searches the text, author, and the record's #, customer / vendor, PO # and job #."""
    nq = _notes_user_can_see(db, user)
    if nq is None:
        return []
    if status in ("open", "done"):
        nq = nq.filter(StickyNote.done.is_(status == "done"))
    if kind in NOTE_ENTITIES:
        nq = nq.filter(StickyNote.entity_type == kind)
    out = [_note_out(db, n) for n in nq.order_by(StickyNote.updated_at.desc()).limit(1000).all()]
    words = [w for w in (q or "").lower().split() if w]
    if words:
        def hay(n):
            r = n["record"] or {}
            return " ".join(str(x or "") for x in (n["text"], n["created_by"], n["updated_by"], r.get("code"), r.get("who"), r.get("po_number"),
                                                    r.get("job_number"), r.get("order_code"), r.get("vendor_so"))).lower()
        out = [n for n in out if all(w in hay(n) for w in words)]
    return out


@router.get("/notes/counts")
def note_counts(entity_type: str, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """{record id: open notes} for one record type -- the list pages' note markers."""
    if entity_type not in NOTE_ENTITIES or not has(user, NOTE_ENTITIES[entity_type][1]):
        return {}
    from sqlalchemy import func
    rows = (db.query(StickyNote.entity_id, func.count(StickyNote.id)).filter(StickyNote.entity_type == entity_type, StickyNote.done.is_(False))
            .group_by(StickyNote.entity_id).all())
    return {str(k): n for k, n in rows}


class NoteIn(BaseModel):
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    text: Optional[str] = None
    color: Optional[str] = None
    remind_date: Optional[str] = None  # YYYY-MM-DD ("" clears)
    remind_time: Optional[str] = None  # HH:MM in the user's zone


def _note_or_404(db: Session, user: User, note_id: int) -> StickyNote:
    n = db.get(StickyNote, note_id)
    if not n:
        raise HTTPException(status_code=404, detail="Note not found")
    _check_record(db, user, n.entity_type, n.entity_id)
    return n


def _apply_note(user: User, n: StickyNote, data: NoteIn) -> None:
    if data.text is not None:
        if not data.text.strip():
            raise HTTPException(status_code=400, detail="Write something on the note")
        n.text = data.text.strip()
    if data.color is not None:
        n.color = data.color if data.color in COLORS else "yellow"
    if data.remind_date is not None:
        n.remind_date = _day(data.remind_date)
    if data.remind_date is not None or data.remind_time is not None:
        n.remind_at = _moment(n.remind_date, data.remind_time, user.effective_timezone) if (data.remind_time or "") else None


@router.post("/notes")
def note_create(data: NoteIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    _check_record(db, user, data.entity_type, data.entity_id)
    n = StickyNote(entity_type=data.entity_type, entity_id=data.entity_id, text="x", created_by=user.username, updated_by=user.username)
    if not (data.text or "").strip():
        raise HTTPException(status_code=400, detail="Write something on the note")
    _apply_note(user, n, data)
    db.add(n)
    db.commit()
    return _note_out(db, n)


@router.put("/notes/{note_id}")
def note_update(note_id: int, data: NoteIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    n = _note_or_404(db, user, note_id)
    _apply_note(user, n, data)
    n.updated_by = user.username
    db.commit()
    return _note_out(db, n)


@router.post("/notes/{note_id}/done")
def note_toggle(note_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    n = _note_or_404(db, user, note_id)
    n.done = not n.done
    n.done_at, n.done_by = (datetime.utcnow(), user.username) if n.done else (None, None)
    db.commit()
    return _note_out(db, n)


@router.delete("/notes/{note_id}", status_code=204)
def note_delete(note_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    n = _note_or_404(db, user, note_id)
    if n.created_by != user.username and not has(user, "money.view"):
        raise HTTPException(status_code=403, detail="Only whoever wrote it or a manager can delete a note -- mark it done instead")
    db.delete(n)
    db.commit()
    return Response(status_code=204)


# ---------- calendar ----------
@router.get("/calendar")
def calendar(start: str, end: str, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """What falls on each day from start to end (YYYY-MM-DD, inclusive), in the user's zone, limited to what their role
    may see: deliveries due, POs expected, invoices due, shipments shipped, to-dos, note reminders."""
    a, b = _day(start), _day(end)
    if not a or not b or b < a or (b - a).days > 62:
        raise HTTPException(status_code=400, detail="Pick a range of up to two months")
    b_end = b + timedelta(days=1)
    tz = user.effective_timezone
    ev = []
    add = lambda day, kind, title, sub, link: ev.append({"date": day.strftime("%Y-%m-%d"), "kind": kind, "title": title, "sub": sub, "link": link})
    if has(user, "orders.view"):
        names = {c.id: c.name for c in db.query(Customer).all()}
        for o in db.query(CustomerOrder).filter(CustomerOrder.delivery_date >= a, CustomerOrder.delivery_date < b_end,
                                                CustomerOrder.status.in_(("draft", "confirmed"))).all():
            add(o.delivery_date, "delivery", f"{o.code} due", names.get(o.customer_id, ""), f"customer-orders.html?id={o.id}")
    if has(user, "purchasing"):
        names = {v.id: v.name for v in db.query(Vendor).all()}
        for p in db.query(PurchaseOrder).filter(PurchaseOrder.expected_date >= a, PurchaseOrder.expected_date < b_end,
                                                PurchaseOrder.status.in_(("ordered", "shipped", "partially_received"))).all():
            add(p.expected_date, "po", f"{p.code} expected", names.get(p.vendor_id, ""), f"purchase-orders.html?id={p.id}")
    if has(user, "invoices"):
        names = {c.id: c.name for c in db.query(Customer).all()}
        for i in db.query(Invoice).filter(Invoice.due_date >= a, Invoice.due_date < b_end, Invoice.status == "sent").all():
            if i.balance > 0.005:
                add(i.due_date, "invoice", f"{i.code} due", names.get(i.customer_id, ""), f"invoices.html?id={i.id}")
    if has(user, "shipments.view"):
        lo, hi = clock.to_utc(a, tz), clock.to_utc(b_end, tz)
        for s in db.query(Shipment).filter(Shipment.ship_date >= lo, Shipment.ship_date < hi).all():
            add(clock.local(s.ship_date, tz), "shipped", f"{s.code} shipped", "", f"shipments.html?id={s.id}")
    lists = {l.id: l for l in _visible_lists(db, user).all()}
    for i in db.query(TodoItem).filter(TodoItem.list_id.in_(list(lists) or [0]), TodoItem.done.is_(False),
                                       TodoItem.due_date >= a, TodoItem.due_date < b_end).all():
        add(i.due_date, "todo", i.title, clock.local(i.due_at, tz).strftime("%I:%M %p").lstrip("0") if i.due_at else lists[i.list_id].name, "todo.html")
    nq = _notes_user_can_see(db, user)
    if nq is not None:
        for n in nq.filter(StickyNote.done.is_(False), StickyNote.remind_date >= a, StickyNote.remind_date < b_end).all():
            rec = _record_label(db, n.entity_type, n.entity_id)
            add(n.remind_date, "note", n.text[:60], rec["code"] if rec else "", rec["link"] if rec else "todo.html")
    return sorted(ev, key=lambda e: (e["date"], e["kind"]))
