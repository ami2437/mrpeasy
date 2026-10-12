"""Undo for every change to the books (routes/accounting.py): filing a line, bulk moves, a rule applied, Check My Books /
Reconcile fixes, merging / deleting an account, a rule saved / deleted / combined, an ignored line restored ...

How: before an action the Accounting tables are read (start); after it they're read again and only what changed is kept
(finish) -- each row as it was and as the action left it -- in AcctChange. Undo puts the "before" rows back, but only
when nothing has changed those rows since (else it says which and does nothing). Statement imports keep their own Undo
(Super admin)."""
import json
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import AcctChange

TABLES = ("acct_accounts", "acct_rules", "acct_txns", "acct_ignored", "acct_check_ok", "acct_memory", "acct_loan_events")
IGNORE = {"updated_at", "updated_by", "hits", "used", "last_used"}  # bookkeeping, not part of what a person changed


def _rows(db: Session, table: str) -> Dict[int, dict]:
    out = {r["id"]: dict(r) for r in db.execute(text(f"select * from {table}")).mappings()}
    if table == "acct_txns":
        for r in db.execute(text("select txn_id, account_id, amount, note from acct_splits order by id")).mappings():
            if r["txn_id"] in out:
                out[r["txn_id"]].setdefault("_splits", []).append({"account_id": r["account_id"], "amount": r["amount"], "note": r["note"]})
        for v in out.values():
            v.setdefault("_splits", [])
    return out


def start(db: Session) -> Dict[str, Dict[int, dict]]:
    db.flush()
    return {t: _rows(db, t) for t in TABLES}


def _same(a: Optional[dict], b: Optional[dict]) -> bool:
    if a is None or b is None:
        return a is b
    return {k: v for k, v in a.items() if k not in IGNORE} == {k: v for k, v in b.items() if k not in IGNORE}


def finish(db: Session, before: Dict[str, Dict[int, dict]], what: str, kind: str, by: str) -> Optional[int]:
    """Keep what the action changed; returns the change's id (None when nothing changed). Commits."""
    db.flush()
    b_out, a_out, n = {}, {}, 0
    for t in TABLES:
        now = _rows(db, t)
        old = before.get(t, {})
        for i in set(old) | set(now):
            if not _same(old.get(i), now.get(i)):
                b_out.setdefault(t, {})[str(i)] = old.get(i)
                a_out.setdefault(t, {})[str(i)] = now.get(i)
                if t == "acct_txns":
                    n += 1
    if not b_out:
        db.commit()
        return None
    c = AcctChange(what=what[:250], kind=kind, before=json.dumps(b_out, default=str), after=json.dumps(a_out, default=str),
                   count=n or sum(len(v) for v in b_out.values()), created_by=by)
    db.add(c)
    db.commit()
    return c.id


def _cols(db: Session, table: str) -> List[str]:
    return [r[1] for r in db.execute(text(f"pragma table_info({table})")).fetchall()]


def undo(db: Session, change_id: int, by: str) -> dict:
    c = db.get(AcctChange, change_id)
    if not c:
        raise HTTPException(status_code=404, detail="Change not found")
    if c.undone_at:
        raise HTTPException(status_code=400, detail=f"Already undone by {c.undone_by}")
    before, after = json.loads(c.before or "{}"), json.loads(c.after or "{}")
    now = {t: _rows(db, t) for t in before}
    changed = []
    for t, rows in after.items():
        for i, row in rows.items():
            if not _same(json.loads(json.dumps(now[t].get(int(i)), default=str)), row):
                changed.append((t, int(i)))
    if changed:
        lines = [i for t, i in changed if t == "acct_txns"]
        raise HTTPException(status_code=409, detail=(f"Can't undo: {len(changed)} of the rows it changed were changed again since"
                                                     + (f" (lines {', '.join(map(str, lines[:8]))})" if lines else "") + ". Undo the later changes first."))
    order = ["acct_accounts", "acct_rules", "acct_memory", "acct_txns", "acct_loan_events", "acct_ignored", "acct_check_ok"]
    for t in order:  # put back / update first (parents before children) ...
        cols = set(_cols(db, t))
        for i, row in (before.get(t) or {}).items():
            if row is None:
                continue
            vals = {k: v for k, v in row.items() if k in cols}
            if int(i) in now[t]:
                sets = ", ".join(f"{k} = :{k}" for k in vals if k != "id")
                db.execute(text(f"update {t} set {sets} where id = :id"), vals)
            else:
                db.execute(text(f"insert into {t} ({', '.join(vals)}) values ({', '.join(':' + k for k in vals)})"), vals)
            if t == "acct_txns":
                db.execute(text("delete from acct_splits where txn_id = :i"), {"i": int(i)})
                for sp in row.get("_splits") or []:
                    db.execute(text("insert into acct_splits (txn_id, account_id, amount, note) values (:t, :a, :m, :n)"),
                               {"t": int(i), "a": sp["account_id"], "m": sp["amount"], "n": sp.get("note")})
    for t in reversed(order):  # ... then remove what the change had made (children before parents)
        for i, row in (before.get(t) or {}).items():
            if row is None:
                if t == "acct_txns":
                    db.execute(text("delete from acct_splits where txn_id = :i"), {"i": int(i)})
                db.execute(text(f"delete from {t} where id = :i"), {"i": int(i)})
    c.undone_by, c.undone_at = by, datetime.utcnow()
    db.commit()
    db.expire_all()
    return {"undone": c.count, "what": c.what}


def recent(db: Session, limit: int = 50) -> List[dict]:
    return [{"id": c.id, "what": c.what, "kind": c.kind, "count": c.count, "created_by": c.created_by, "created_at": c.created_at,
             "undone_by": c.undone_by, "undone_at": c.undone_at}
            for c in db.query(AcctChange).order_by(AcctChange.id.desc()).limit(limit).all()]


def history_of_txn(db: Session, txn_id: int, limit: int = 20) -> List[dict]:
    """The changes that touched one line (newest first) -- for the line's history in Reconcile's Compare."""
    key = f'"{txn_id}":'
    out = []
    for c in db.query(AcctChange).filter(AcctChange.before.like(f"%{key}%")).order_by(AcctChange.id.desc()).limit(200).all():
        if str(txn_id) in (json.loads(c.before or "{}").get("acct_txns") or {}):
            out.append({"id": c.id, "what": c.what, "by": c.created_by, "at": c.created_at, "undone": bool(c.undone_at)})
            if len(out) >= limit:
                break
    return out
