"""Accounting (services/accounting.py): statements in, every line in an account, reports. Money permission
"accounting" -- Admin and Super admin until a role is given it on Users & Roles."""
import json
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_perm
from app.models import AcctAccount, AcctImport, AcctOpenItem, AcctRule, AcctSource, AcctSplit, AcctTxn, User
from app.routes.attachments import MAX_BYTES
from app.services import accounting as A
from app.services import acct_mapper
from app.services.money import cents

router = APIRouter(prefix="/api/accounting", tags=["accounting"], dependencies=[Depends(require_perm("accounting"))])


def _who(u: User) -> str:
    return u.full_name or u.username


def _period(db: Session, start: Optional[str], end: Optional[str]):
    a, b = A.parse_day(start), A.parse_day(end)
    if not a or not b:
        m = A.settings(db)["fy_start_month"]
        a, b = A.fy_range(A.fy_start(A.today(), m).year, m)
    if b < a:
        raise HTTPException(status_code=400, detail="The period ends before it starts")
    return a, b


def _fy_key(db: Session, a: date) -> str:
    m = A.settings(db)["fy_start_month"]
    return A.fy_label(A.fy_start(a, m), m)


# ---------------------------------------------------------------- setup
@router.get("/meta")
def meta(db: Session = Depends(get_db)):
    A.book_source(db)
    db.commit()
    return {"settings": A.settings(db), "years": A.years(db), "kinds": [{"key": k, "label": v[0], "qb_type": v[1], "in_pnl": v[2]} for k, v in A.KINDS.items()],
            "accounts": [A.account_dict(a) for a in db.query(AcctAccount).order_by(AcctAccount.name).all()],
            "sources": [A.source_dict(db, s) for s in db.query(AcctSource).order_by(AcctSource.kind, AcctSource.id).all()],
            "today": A.today().isoformat()}


class SettingsIn(BaseModel):
    fy_start_month: Optional[int] = None
    partners: Optional[List[dict]] = None
    year: Optional[str] = None  # FY label the next three are for
    bank_balance: Optional[float] = None
    notes: Optional[str] = None
    adjust: Optional[dict] = None
    open_orders_counted: Optional[bool] = None


@router.put("/settings")
def put_settings(body: SettingsIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    s = A.settings(db)
    if body.fy_start_month is not None:
        if not 1 <= body.fy_start_month <= 12:
            raise HTTPException(status_code=400, detail="Month 1-12")
        s["fy_start_month"] = body.fy_start_month
    if body.partners is not None:
        ps = [{"name": str(p.get("name") or "").strip(), "share": float(p.get("share") or 0)} for p in body.partners if str(p.get("name") or "").strip()]
        if ps and abs(sum(p["share"] for p in ps) - 1) > 0.001:
            raise HTTPException(status_code=400, detail=f"The shares add up to {sum(p['share'] for p in ps) * 100:.1f}%, not 100%")
        s["partners"] = ps
    if body.year:
        y = dict(s.get("years", {}).get(body.year, {}))
        if body.bank_balance is not None:
            y["bank_balance"] = cents(body.bank_balance)
        if body.notes is not None:
            y["notes"] = body.notes
        if body.adjust is not None:
            y["adjust"] = {k: cents(float(v or 0)) for k, v in body.adjust.items()}
        s.setdefault("years", {})[body.year] = y
    if body.open_orders_counted is not None:
        s.setdefault("apar_groups", {})["open_orders"] = body.open_orders_counted
    return A.save_settings(db, s, _who(user))


class AccountIn(BaseModel):
    name: Optional[str] = None
    kind: Optional[str] = None
    partner: Optional[str] = None
    qb_type: Optional[str] = None
    external_id: Optional[str] = None
    note: Optional[str] = None
    opening_balance: Optional[float] = None
    is_active: Optional[bool] = None


def _apply_account(a: AcctAccount, body: AccountIn, db: Session):
    if body.kind is not None and body.kind not in A.KINDS:
        raise HTTPException(status_code=400, detail="Unknown account type")
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Give the account a name")
        clash = next((x for x in db.query(AcctAccount).all() if x.id != a.id and x.name.strip().lower() == name.lower()), None)
        if clash:
            raise HTTPException(status_code=400, detail=f"There's already an account called {clash.name}")
        a.name = name
    for k in ("kind", "partner", "qb_type", "external_id", "note", "opening_balance", "is_active"):
        v = getattr(body, k)
        if v is not None:
            setattr(a, k, (v.strip() or None) if isinstance(v, str) else v)
    if a.kind != "owner":
        a.partner = None


@router.post("/accounts")
def add_account(body: AccountIn, db: Session = Depends(get_db)):
    a = AcctAccount(name="", kind=body.kind or "expense")
    _apply_account(a, body, db)
    if not a.name:
        raise HTTPException(status_code=400, detail="Give the account a name")
    a.qb_type = a.qb_type or A.KINDS[a.kind][1]
    db.add(a)
    db.commit()
    return A.account_dict(a)


@router.put("/accounts/{account_id}")
def edit_account(account_id: int, body: AccountIn, db: Session = Depends(get_db)):
    a = db.get(AcctAccount, account_id)
    if not a:
        raise HTTPException(status_code=404, detail="Account not found")
    _apply_account(a, body, db)
    db.commit()
    return A.account_dict(a)


@router.post("/accounts/{account_id}/merge")
def merge_account(account_id: int, into_id: int, db: Session = Depends(get_db)):
    """Move every line, part and rule of this account into another, then remove it (two names for one thing)."""
    a, b = db.get(AcctAccount, account_id), db.get(AcctAccount, into_id)
    if not a or not b or a.id == b.id:
        raise HTTPException(status_code=400, detail="Pick another account to merge into")
    n = db.query(AcctTxn).filter(AcctTxn.account_id == a.id).update({AcctTxn.account_id: b.id})
    n += db.query(AcctSplit).filter(AcctSplit.account_id == a.id).update({AcctSplit.account_id: b.id})
    db.query(AcctRule).filter(AcctRule.account_id == a.id).update({AcctRule.account_id: b.id})
    b.opening_balance = (b.opening_balance or 0) + (a.opening_balance or 0)
    db.delete(a)
    db.commit()
    return {"moved": n}


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    a = db.get(AcctAccount, account_id)
    if not a:
        raise HTTPException(status_code=404, detail="Account not found")
    used = db.query(AcctTxn).filter(AcctTxn.account_id == a.id).count() + db.query(AcctSplit).filter(AcctSplit.account_id == a.id).count()
    if used:
        raise HTTPException(status_code=400, detail=f"{used} line(s) are in {a.name} -- merge it into another account, or switch it off")
    db.query(AcctRule).filter(AcctRule.account_id == a.id).delete()
    db.delete(a)
    db.commit()
    return {"ok": True}


class SourceIn(BaseModel):
    name: Optional[str] = None
    kind: Optional[str] = None
    last4: Optional[str] = None
    opening_balance: Optional[float] = None
    opening_date: Optional[str] = None
    external_id: Optional[str] = None
    is_active: Optional[bool] = None


def _apply_source(s: AcctSource, body: SourceIn):
    if body.kind is not None:
        if body.kind not in ("bank", "card"):
            raise HTTPException(status_code=400, detail="A bank account or a credit card")
        s.kind = body.kind
    for k in ("name", "last4", "external_id"):
        v = getattr(body, k)
        if v is not None:
            setattr(s, k, v.strip() or None)
    if body.opening_balance is not None:
        s.opening_balance = cents(body.opening_balance)
    if body.opening_date is not None:
        d = A.parse_day(body.opening_date)
        s.opening_date = A._dt(d) if d else None
    if body.is_active is not None:
        s.is_active = body.is_active
    if not s.name:
        raise HTTPException(status_code=400, detail="Give it a name (e.g. Wells Fargo Checking)")


@router.post("/sources")
def add_source(body: SourceIn, db: Session = Depends(get_db)):
    s = AcctSource(name="", kind=body.kind or "bank")
    _apply_source(s, body)
    db.add(s)
    db.commit()
    return A.source_dict(db, s)


@router.put("/sources/{source_id}")
def edit_source(source_id: int, body: SourceIn, db: Session = Depends(get_db)):
    s = db.get(AcctSource, source_id)
    if not s:
        raise HTTPException(status_code=404, detail="Not found")
    if s.kind == "book":
        body.kind = None
    _apply_source(s, body)
    db.commit()
    return A.source_dict(db, s)


@router.delete("/sources/{source_id}")
def delete_source(source_id: int, db: Session = Depends(get_db)):
    s = db.get(AcctSource, source_id)
    if not s or s.kind == "book":
        raise HTTPException(status_code=400, detail="Book Entries stays")
    n = db.query(AcctTxn).filter(AcctTxn.source_id == s.id).count()
    if n:
        raise HTTPException(status_code=400, detail=f"{s.name} has {n} line(s) -- switch it off instead")
    db.delete(s)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- statement files
async def _read(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="File too big")
    if not (file.filename or "").lower().endswith((".csv", ".xlsx", ".xlsm", ".txt")):
        raise HTTPException(status_code=400, detail="A CSV or Excel (.xlsx) statement")
    return data


@router.post("/import/preview")
async def import_preview(source_id: int = Form(...), layout: Optional[str] = Form(None), file: UploadFile = File(...),
                         db: Session = Depends(get_db)):
    data = await _read(file)
    return A.preview(db, data, file.filename, source_id, json.loads(layout) if layout else None)


@router.post("/import")
async def import_statement(source_id: int = Form(...), layout: Optional[str] = Form(None), file: UploadFile = File(...),
                           db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    data = await _read(file)
    return A.import_file(db, data, file.filename, source_id, json.loads(layout) if layout else None, _who(user))


@router.get("/imports")
def imports(db: Session = Depends(get_db)):
    src = {s.id: s.name for s in db.query(AcctSource).all()}
    return [{"id": i.id, "source": src.get(i.source_id, ""), "filename": i.filename, "kind": i.kind, "rows": i.rows, "added": i.added,
             "skipped": i.skipped, "matched": i.matched, "auto": i.auto, "created_by": i.created_by, "created_at": i.created_at,
             "still_there": db.query(AcctTxn).filter(AcctTxn.import_id == i.id).count()}
            for i in db.query(AcctImport).order_by(AcctImport.id.desc()).limit(100).all()]


@router.delete("/imports/{import_id}")
def undo_import(import_id: int, db: Session = Depends(get_db)):
    return {"removed": A.undo_import(db, import_id)}


# ---------------------------------------------------------------- lines
@router.get("/txns")
def txns(start: Optional[str] = None, end: Optional[str] = None, source_id: Optional[int] = None, account_id: Optional[int] = None,
         kind: Optional[str] = None, status: Optional[str] = None, q: Optional[str] = None, all_dates: bool = False,
         limit: int = 500, offset: int = 0, db: Session = Depends(get_db)):
    """status: unfiled (no account) | auto (AT-HUB put it there, nobody looked) | expected (typed in, not on a statement yet)
    | typed (typed in) | split."""
    qy = db.query(AcctTxn).options(selectinload(AcctTxn.splits))
    if not all_dates:
        a, b = _period(db, start, end)
        qy = qy.filter(AcctTxn.date >= A._dt(a), AcctTxn.date <= A._dt(b))
    if source_id:
        qy = qy.filter(AcctTxn.source_id == source_id)
    if account_id:
        qy = qy.filter(or_(AcctTxn.account_id == account_id, AcctTxn.splits.any(AcctSplit.account_id == account_id)))
    if kind:
        ids = [a.id for a in db.query(AcctAccount).filter(AcctAccount.kind == kind).all()]
        qy = qy.filter(or_(AcctTxn.account_id.in_(ids), AcctTxn.splits.any(AcctSplit.account_id.in_(ids))))
    if status == "unfiled":
        qy = qy.filter(AcctTxn.account_id.is_(None), ~AcctTxn.splits.any())
    elif status == "auto":
        qy = qy.filter(AcctTxn.reviewed.is_(False))
    elif status == "expected":
        qy = qy.filter(AcctTxn.expected.is_(True))
    elif status == "typed":
        qy = qy.filter(AcctTxn.origin == "manual")
    elif status == "split":
        qy = qy.filter(AcctTxn.splits.any())
    if q:
        like = f"%{q.strip()}%"
        qy = qy.filter(or_(AcctTxn.description.ilike(like), AcctTxn.note.ilike(like), AcctTxn.payee.ilike(like), AcctTxn.bank_description.ilike(like)))
    total = qy.count()
    rows = qy.order_by(AcctTxn.date.desc(), AcctTxn.id.desc()).offset(offset).limit(min(limit, 2000)).all()
    accounts, sources = A.maps(db)
    sums = qy.with_entities(AcctTxn.amount).all()
    unfiled = [t for t in rows if not t.account_id and not t.splits]
    sugg = A.suggestions(db, unfiled) if unfiled else {}
    out = []
    for t in rows:
        d = A.txn_dict(t, accounts, sources)
        if t.id in sugg:
            s = sugg[t.id]
            d["suggest"] = {**s, "account": accounts[s["account_id"]].name if s["account_id"] in accounts else ""}
        out.append(d)
    return {"total": total, "rows": out, "money_in": cents(sum(a for (a,) in sums if a > 0)), "money_out": cents(sum(a for (a,) in sums if a < 0))}


class TxnIn(BaseModel):
    source_id: Optional[int] = None
    date: Optional[str] = None
    amount: Optional[float] = None
    description: Optional[str] = None
    payee: Optional[str] = None
    account_id: Optional[int] = None
    splits: Optional[List[dict]] = None
    note: Optional[str] = None
    expected: Optional[bool] = None
    learn: bool = True  # after a person files a line, file the look-alikes AT-HUB is now sure about


@router.post("/txns")
def add_txn(body: TxnIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """A typed-in line: on a bank account and not on a statement yet (expected -- matched when the statement brings it),
    or a book entry that never will be (year-end figures, adjustments, interest to add)."""
    d = A.parse_day(body.date)
    if not d or body.amount in (None, 0):
        raise HTTPException(status_code=400, detail="A date and an amount (+ money in, - money out)")
    src = db.get(AcctSource, body.source_id) if body.source_id else A.book_source(db)
    if not src:
        raise HTTPException(status_code=400, detail="Account not found")
    desc = (body.description or "").strip() or (body.payee or "").strip()
    if not desc:
        raise HTTPException(status_code=400, detail="Say what it is")
    t = AcctTxn(source_id=src.id, date=A._dt(d), amount=cents(body.amount), description=desc, payee=(body.payee or "").strip() or acct_mapper.payee_label(desc),
                note=(body.note or "").strip() or None, origin="manual", expected=bool(body.expected) and src.kind != "book",
                created_by=_who(user))
    db.add(t)
    db.flush()
    if body.account_id or body.splits:
        A.set_account(db, t, body.account_id, body.splits, _who(user))
    db.commit()
    accounts, sources = A.maps(db)
    return A.txn_dict(t, accounts, sources)


@router.put("/txns/{txn_id}")
def edit_txn(txn_id: int, body: TxnIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    t = db.get(AcctTxn, txn_id)
    if not t:
        raise HTTPException(status_code=404, detail="Line not found")
    fields = body.model_dump(exclude_unset=True)
    src = db.get(AcctSource, t.source_id)
    if t.origin == "manual" or src.kind == "book":  # typed in / book entries can be changed; a statement line's date / amount / wording can't
        if "date" in fields:
            d = A.parse_day(body.date)
            if not d:
                raise HTTPException(status_code=400, detail="Date?")
            t.date = A._dt(d)
        if "amount" in fields and body.amount:
            t.amount = cents(body.amount)
        if "description" in fields and (body.description or "").strip():
            t.description = body.description.strip()
        if "expected" in fields:
            t.expected = bool(body.expected) and src.kind != "book"
        if "source_id" in fields and body.source_id and db.get(AcctSource, body.source_id):
            t.source_id = body.source_id
    elif {"date", "amount", "description"} & {k for k, v in fields.items() if v is not None}:
        raise HTTPException(status_code=400, detail="This line came from a statement -- its date, amount and wording stay as the bank has them")
    if "payee" in fields:
        t.payee = (body.payee or "").strip() or None
    if "note" in fields:
        t.note = (body.note or "").strip() or None
    filed_more = 0
    if "account_id" in fields or "splits" in fields:
        A.set_account(db, t, body.account_id, body.splits, _who(user))
        db.flush()
        if body.learn and (body.account_id or body.splits):
            filed_more = A.autofill(db)
    t.updated_by = _who(user)
    db.commit()
    accounts, sources = A.maps(db)
    return {**A.txn_dict(t, accounts, sources), "filed_more": filed_more}


class BulkIn(BaseModel):
    ids: List[int]
    action: str  # file | review | unfile | delete
    account_id: Optional[int] = None


@router.post("/txns/bulk")
def bulk(body: BulkIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    ts = db.query(AcctTxn).filter(AcctTxn.id.in_(body.ids)).all()
    n = skipped = 0
    for t in ts:
        if body.action == "file":
            if not body.account_id:
                raise HTTPException(status_code=400, detail="Pick the account")
            A.set_account(db, t, body.account_id, None, _who(user))
        elif body.action == "review":
            t.reviewed = True
            t.how = "person" if t.how in ("auto", "rule") and t.account_id else t.how
        elif body.action == "unfile":
            A.set_account(db, t, None, None, _who(user))
        elif body.action == "delete":
            if t.origin != "manual" and db.get(AcctSource, t.source_id).kind != "book":
                skipped += 1
                continue
            db.delete(t)
        else:
            raise HTTPException(status_code=400, detail="Unknown action")
        n += 1
    db.flush()
    more = A.autofill(db) if body.action == "file" else 0
    db.commit()
    return {"done": n, "skipped": skipped, "filed_more": more}


@router.delete("/txns/{txn_id}")
def delete_txn(txn_id: int, db: Session = Depends(get_db)):
    """Typed-in lines only -- a statement line is undone with its whole import (Statements tab)."""
    t = db.get(AcctTxn, txn_id)
    if not t:
        raise HTTPException(status_code=404, detail="Line not found")
    if t.origin != "manual" and db.get(AcctSource, t.source_id).kind != "book":
        raise HTTPException(status_code=400, detail="This line came from a statement -- undo the import it came with instead")
    db.delete(t)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- rules
class RuleIn(BaseModel):
    contains: str
    account_id: int
    sign: Optional[str] = None
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    refile: bool = False  # also move lines AT-HUB filed (not a person) that this rule now covers


@router.get("/rules")
def rules(db: Session = Depends(get_db)):
    acc = {a.id: a.name for a in db.query(AcctAccount).all()}
    return [{"id": r.id, "contains": r.contains, "sign": r.sign, "min_amount": r.min_amount, "max_amount": r.max_amount,
             "account_id": r.account_id, "account": acc.get(r.account_id, ""), "hits": r.hits, "created_by": r.created_by}
            for r in db.query(AcctRule).order_by(AcctRule.id).all()]


@router.post("/rules")
def add_rule(body: RuleIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    if not body.contains.strip() or not db.get(AcctAccount, body.account_id):
        raise HTTPException(status_code=400, detail="The words to look for and the account")
    r = AcctRule(contains=body.contains.strip().upper(), account_id=body.account_id, sign=body.sign or None,
                 min_amount=body.min_amount, max_amount=body.max_amount, created_by=_who(user))
    db.add(r)
    db.flush()
    rd = {"contains": r.contains, "sign": r.sign, "min_amount": r.min_amount, "max_amount": r.max_amount}
    moved = 0
    if body.refile:
        for t in db.query(AcctTxn).filter(AcctTxn.how == "auto").all():
            if not t.splits and acct_mapper.rule_matches(rd, acct_mapper.clean(t.bank_description or t.description), t.amount) and t.account_id != r.account_id:
                t.account_id, t.how = r.account_id, "rule"
                moved += 1
    filed = A.autofill(db)
    r.hits += moved
    db.commit()
    return {"id": r.id, "filed": filed + moved}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    r = db.get(AcctRule, rule_id)
    if r:
        db.delete(r)
        db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- reports
@router.get("/overview")
def overview(start: Optional[str] = None, end: Optional[str] = None, db: Session = Depends(get_db)):
    a, b = _period(db, start, end)
    return A.overview(db, a, b)


@router.get("/pnl")
def pnl(start: Optional[str] = None, end: Optional[str] = None, by: str = "month", db: Session = Depends(get_db)):
    a, b = _period(db, start, end)
    if by not in ("month", "quarter", "fy"):
        raise HTTPException(status_code=400, detail="month, quarter or fy")
    return A.pnl(db, a, b, by)


@router.get("/accounts-summary")
def accounts_summary(start: Optional[str] = None, end: Optional[str] = None, db: Session = Depends(get_db)):
    a, b = _period(db, start, end)
    return A.account_summary(db, a, b)


@router.get("/loans")
def loans(end: Optional[str] = None, db: Session = Depends(get_db)):
    return A.loans(db, A.parse_day(end))


@router.get("/partners")
def partners(start: Optional[str] = None, end: Optional[str] = None, db: Session = Depends(get_db)):
    a, b = _period(db, start, end)
    return {**A.partners(db, a, b, _fy_key(db, a)), "year": _fy_key(db, a)}


@router.get("/cashflow")
def cashflow(start: Optional[str] = None, end: Optional[str] = None, source_id: Optional[int] = None, db: Session = Depends(get_db)):
    a, b = _period(db, start, end)
    return A.cashflow(db, a, b, source_id)


@router.get("/open-items")
def open_items(db: Session = Depends(get_db)):
    return A.open_items(db)


class OpenItemIn(BaseModel):
    id: Optional[int] = None
    source: Optional[str] = None
    source_id: Optional[int] = None
    side: Optional[str] = None
    party: Optional[str] = None
    ref: Optional[str] = None
    amount: Optional[float] = None
    item_date: Optional[str] = None
    due_date: Optional[str] = None
    status: Optional[str] = None
    paid_date: Optional[str] = None
    note: Optional[str] = None


@router.post("/open-items")
def set_open_item(body: OpenItemIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    o = A.set_open_item(db, body.model_dump(exclude_unset=True), _who(user))
    return {"id": o.id}


@router.delete("/open-items/{item_id}")
def delete_open_item(item_id: int, db: Session = Depends(get_db)):
    """A typed-in item goes; a change to an AT-HUB figure is dropped (AT-HUB's figure again)."""
    o = db.get(AcctOpenItem, item_id)
    if o:
        db.delete(o)
        db.commit()
    return {"ok": True}


@router.get("/export.xlsx")
def export_xlsx(start: Optional[str] = None, end: Optional[str] = None, db: Session = Depends(get_db)):
    a, b = _period(db, start, end)
    data = A.export_xlsx(db, a, b, _fy_key(db, a))
    name = f"Accounting {a.isoformat()} to {b.isoformat()}.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/export/quickbooks.csv")
def export_qb(source_id: int, start: Optional[str] = None, end: Optional[str] = None, db: Session = Depends(get_db)):
    a, b = _period(db, start, end)
    s = db.get(AcctSource, source_id)
    if not s:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(A.export_quickbooks_csv(db, source_id, a, b), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{s.name} {a.isoformat()} to {b.isoformat()} QuickBooks.csv"'})
