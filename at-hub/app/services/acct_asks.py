"""Ask a partner to check a line (user, 2026-10-12): "did you receive this?" -- to another AT-HUB user who has the
Accounting permission, with a note. They get a To-Do item in their own list and see it on Accounting (Waiting For You);
they answer Yes received / No / with a note. The line shows the question and the answers."""
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import AcctAsk, AcctTxn, TodoItem, TodoList, User
from app.services.permissions import perms_for

STATUS = {"open": "Waiting", "yes": "Confirmed", "no": "Says No", "replied": "Replied", "withdrawn": "Withdrawn"}


def who_can_answer(db: Session) -> List[dict]:
    out = []
    for u in db.query(User).filter(User.is_active.is_(True)).order_by(User.full_name, User.username).all():
        if "accounting" in perms_for(db, u.role):
            out.append({"username": u.username, "name": u.full_name or u.username})
    return out


def _name(db: Session, username: str) -> str:
    u = db.query(User).filter(User.username == username).first()
    return (u.full_name or u.username) if u else username


def ask_dict(db: Session, a: AcctAsk, names: Optional[Dict[str, str]] = None) -> dict:
    names = names or {}
    return {"id": a.id, "txn_id": a.txn_id, "to_user": a.to_user, "to_name": names.get(a.to_user) or _name(db, a.to_user), "text": a.text,
            "status": a.status, "status_label": STATUS.get(a.status, a.status), "reply": a.reply, "asked_by": a.asked_by, "asked_at": a.asked_at,
            "answered_by": a.answered_by, "answered_at": a.answered_at}


def ask(db: Session, txn_ids: List[int], to_user: str, text: Optional[str], by_name: str) -> int:
    if to_user not in {u["username"] for u in who_can_answer(db)}:
        raise HTTPException(status_code=400, detail="Pick someone who can open Accounting")
    n = 0
    for t in db.query(AcctTxn).filter(AcctTxn.id.in_(txn_ids)).all():
        a = AcctAsk(txn_id=t.id, to_user=to_user, text=(text or "").strip() or None, asked_by=by_name)
        db.add(a)
        db.flush()
        lst = db.query(TodoList).filter(TodoList.owner == to_user).order_by(TodoList.position, TodoList.id).first()
        if not lst:
            lst = TodoList(name="Reminders", color="#2f6fed", owner=to_user, shared=False)
            db.add(lst)
            db.flush()
        item = TodoItem(list_id=lst.id, title=f"Accounting: please check {t.amount:,.2f} on {t.date.date().isoformat()} ({by_name} asks)",
                        notes=((a.text + "\n\n") if a.text else "") + f"{t.bank_description or t.description}\nAnswer on Accounting > Review (Waiting For You).",
                        due_date=None, created_by=by_name, flagged=True, priority=2)
        db.add(item)
        db.flush()
        a.todo_item_id = item.id
        n += 1
    db.commit()
    return n


def answer(db: Session, ask_id: int, status: str, reply: Optional[str], user: User) -> dict:
    a = db.get(AcctAsk, ask_id)
    if not a:
        raise HTTPException(status_code=404, detail="Not found")
    if status not in ("yes", "no", "replied", "withdrawn"):
        raise HTTPException(status_code=400, detail="yes, no, replied or withdrawn")
    if status == "withdrawn" and a.asked_by not in (user.full_name, user.username):
        raise HTTPException(status_code=403, detail="Only the person who asked can withdraw it")
    if status != "withdrawn" and a.to_user != user.username:
        raise HTTPException(status_code=403, detail=f"This was asked of {_name(db, a.to_user)}")
    a.status, a.reply = status, (reply or "").strip() or a.reply
    a.answered_by, a.answered_at = user.full_name or user.username, datetime.utcnow()
    if a.todo_item_id:
        it = db.get(TodoItem, a.todo_item_id)
        if it and not it.done:
            it.done, it.done_at, it.done_by = True, datetime.utcnow(), user.username
    db.commit()
    return ask_dict(db, a)


def asks_for(db: Session, txn_ids: List[int]) -> Dict[int, List[dict]]:
    out: Dict[int, List[dict]] = {}
    if not txn_ids:
        return out
    names = {u["username"]: u["name"] for u in who_can_answer(db)}
    for a in db.query(AcctAsk).filter(AcctAsk.txn_id.in_(txn_ids)).order_by(AcctAsk.asked_at).all():
        out.setdefault(a.txn_id, []).append(ask_dict(db, a, names))
    return out


def waiting(db: Session, user: User, mine: bool = True) -> List[dict]:
    """Questions waiting for this user (mine) -- or every open one (asked by anyone)."""
    from app.services import accounting as A
    q = db.query(AcctAsk).filter(AcctAsk.status == "open")
    if mine:
        q = q.filter(AcctAsk.to_user == user.username)
    asks = q.order_by(AcctAsk.asked_at).all()
    accounts, sources = A.maps(db)
    tx = {t.id: t for t in db.query(AcctTxn).filter(AcctTxn.id.in_([a.txn_id for a in asks])).all()} if asks else {}
    names = {u["username"]: u["name"] for u in who_can_answer(db)}
    return [{**ask_dict(db, a, names), "txn": A.txn_dict(tx[a.txn_id], accounts, sources)} for a in asks if a.txn_id in tx]
