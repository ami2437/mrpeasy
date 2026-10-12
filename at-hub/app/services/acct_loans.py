"""Loans, first round (user, 2026-10-12 -- "a lot of manual entries I make are not recorded anywhere"; to be built up as
they test): per loan account a statement + timeline (bank lines, book entries and the events a person writes down, in
date order with the balance after each), the loan's details (lender, rate, payment, due day ...), events that move money
without the bank -- a partner paid it off, the balance moved to another loan / account, interest added, an adjustment --
which make Book Entries so the balance follows (and Undo / delete take them back), interest worked out to a date,
payments expected vs made, and a check against the lender's own statement.

Sign: a loan account holds what the company owes -- money borrowed comes into the bank (+), repaid goes out (-), so the
balance owed = opening + sum of the lines in the account (accounting.loans())."""
import io
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload

from app.models import AcctAccount, AcctLoanEvent, AcctSplit, AcctTxn
from app.services import accounting as A
from app.services.money import cents

KINDS = {  # kind -> (label, makes book entries?)
    "note": ("Note", False), "took": ("Took The Loan", False), "rate": ("Rate Changed", False), "terms": ("Terms / Due Date", False),
    "call": ("Talked To The Lender", False), "closed": ("Closed", False), "reopened": ("Reopened", False),
    "moved_out": ("Moved To Another Account", True), "paid_by_partner": ("Paid By A Partner", True), "interest": ("Interest Added", True),
    "adjust": ("Balance Adjusted", True), "moved_in": ("Moved In From Another Account", False),
}
DETAIL_KEYS = ("lender", "original", "start", "rate", "rate_type", "payment", "due_day", "term_months", "secured_by", "partner", "role", "note")


def _loan(db: Session, account_id: int) -> AcctAccount:
    a = db.get(AcctAccount, account_id)
    if not a or a.kind != "loan":
        raise HTTPException(status_code=404, detail="Loan account not found")
    return a


def details(a: AcctAccount) -> dict:
    d = json.loads(a.loan_info or "{}")
    return {k: d.get(k) for k in DETAIL_KEYS}


def set_details(db: Session, account_id: int, body: dict) -> dict:
    a = _loan(db, account_id)
    cur = details(a)
    for k in DETAIL_KEYS:
        if k in body:
            v = body[k]
            if k in ("original", "rate", "payment") and v not in (None, ""):
                v = float(v)
            if k in ("due_day", "term_months") and v not in (None, ""):
                v = int(v)
            cur[k] = v if v != "" else None
    a.loan_info = json.dumps(cur)
    db.flush()
    return cur


def _ids(a: AcctAccount, accounts) -> set:
    return {a.id, *A.descendants(a.id, accounts)}


def statement(db: Session, account_id: int) -> dict:
    """Every line in the loan (a split line: its part here) + every event, oldest first, with the balance after each."""
    accounts, sources = A.maps(db)
    a = _loan(db, account_id)
    ids = _ids(a, accounts)
    events = db.query(AcctLoanEvent).filter(AcctLoanEvent.account_id == a.id).all()
    made = {tid for e in events for tid in json.loads(e.txn_ids or "[]")}
    rows = []
    for t in db.query(AcctTxn).options(selectinload(AcctTxn.splits)).all():
        part = sum(x.amount for x in t.splits if x.account_id in ids) if t.splits else (t.amount if t.account_id in ids else None)
        if part is None or (t.splits and not any(x.account_id in ids for x in t.splits)):
            continue
        src = sources.get(t.source_id)
        rows.append({"type": "line", "date": t.date.date().isoformat(), "amount": cents(part), "txn_id": t.id, "description": t.bank_description or t.description,
                     "note": t.note, "source": src.name if src else "", "book": bool(src and src.kind == "book"), "by_event": t.id in made,
                     "kind": "borrowed" if part > 0 else "repaid"})
    for e in events:
        rows.append({"type": "event", "date": e.date.date().isoformat(), "id": e.id, "kind": e.kind, "label": KINDS.get(e.kind, (e.kind,))[0],
                     "text": e.text, "amount": e.amount, "other_account_id": e.other_account_id,
                     "other_account": A.path(accounts[e.other_account_id], accounts) if e.other_account_id in accounts else None,
                     "pair_id": e.pair_id, "created_by": e.created_by, "created_at": e.created_at})
    rows.sort(key=lambda r: (r["date"], 0 if r["type"] == "event" else 1, r.get("txn_id") or r.get("id") or 0))
    bal = a.opening_balance or 0.0
    closed_at, was_open, history = None, abs(bal) > 0.004, []
    for r in rows:
        if r["type"] == "line":
            bal = cents(bal + r["amount"])
        r["balance"] = bal
        if abs(bal) <= 0.004 and was_open:
            history.append({"date": r["date"], "what": "balance reached 0 -- closed"})
            closed_at, was_open = r["date"], False
        elif abs(bal) > 0.004 and not was_open:
            if closed_at:
                history.append({"date": r["date"], "what": "borrowing again -- reopened"})
            was_open = True
    lines_ = [r for r in rows if r["type"] == "line"]
    moved = [r for r in lines_ if r["by_event"]]
    return {"account": A.account_dict(a, accounts), "details": details(a), "opening": a.opening_balance or 0.0, "rows": rows,
            "owed": bal, "borrowed": cents(sum(r["amount"] for r in lines_ if r["amount"] > 0 and not r["by_event"])),
            "repaid": cents(sum(r["amount"] for r in lines_ if r["amount"] < 0 and not r["by_event"])),
            "moved_or_adjusted": cents(sum(r["amount"] for r in moved)), "open": abs(bal) > 0.004, "history": history,
            "events": len(events), "last_event": max((r for r in rows if r["type"] == "event"), key=lambda r: r["date"], default=None)}


def _book(db: Session, account_id: int, d: date, amount: float, text: str, by: str) -> AcctTxn:
    t = AcctTxn(source_id=A.book_source(db).id, date=A._dt(d), amount=cents(amount), description=text[:250], payee=None,
                account_id=account_id, how="person", reviewed=True, origin="manual", note=text, created_by=by, updated_by=by)
    db.add(t)
    db.flush()
    return t


def add_event(db: Session, account_id: int, body: dict, by: str) -> AcctLoanEvent:
    """Write down what happened. With an amount, these make Book Entries (no bank money moved):
      moved_out / paid_by_partner -- this loan goes down by the amount, the other account up (another loan: owed there;
                                     a partner's account: what they put in)
      interest                    -- the loan goes up, the interest expense account down
      adjust                      -- the loan goes up (+) or down (-) by the amount, nothing else."""
    accounts, _ = A.maps(db)
    a = _loan(db, account_id)
    kind = body.get("kind") or "note"
    if kind not in KINDS:
        raise HTTPException(status_code=400, detail="Unknown event")
    d = A.parse_day(body.get("date")) or A.today()
    amt = body.get("amount")
    amt = None if amt in (None, "") else cents(float(amt))
    other = int(body["other_account_id"]) if body.get("other_account_id") else None
    text = (body.get("text") or "").strip() or None
    e = AcctLoanEvent(account_id=a.id, date=A._dt(d), kind=kind, text=text, amount=amt, other_account_id=other, created_by=by)
    db.add(e)
    db.flush()
    made = []
    label = f"{a.name}: {KINDS[kind][0]}" + (f" -- {text}" if text else "")
    if KINDS[kind][1]:
        if not amt:
            raise HTTPException(status_code=400, detail="This one needs an amount")
        size = abs(amt)
        if kind in ("moved_out", "paid_by_partner"):
            if not other or other not in accounts or other == a.id:
                raise HTTPException(status_code=400, detail="Pick the account it moved to" if kind == "moved_out" else "Pick the partner's account")
            made.append(_book(db, a.id, d, -size, label + f" -> {accounts[other].name}", by).id)
            made.append(_book(db, other, d, size, f"{accounts[other].name}: from {a.name} ({KINDS[kind][0].lower()})" + (f" -- {text}" if text else ""), by).id)
            if accounts[other].kind == "loan":
                pe = AcctLoanEvent(account_id=other, date=A._dt(d), kind="moved_in", text=text, amount=size, other_account_id=a.id, pair_id=e.id, created_by=by)
                db.add(pe)
                db.flush()
                e.pair_id = pe.id
        elif kind == "interest":
            exp = other if other in accounts else A.account_by_name(db, "Loan Interest", "expense").id
            e.other_account_id = exp
            made.append(_book(db, a.id, d, size, label, by).id)
            made.append(_book(db, exp, d, -size, f"Interest on {a.name}" + (f" -- {text}" if text else ""), by).id)
        else:  # adjust
            made.append(_book(db, a.id, d, amt, label, by).id)
    if kind == "rate" and amt is not None:  # a rate change also updates the details
        info = details(a)
        info["rate"] = amt
        a.loan_info = json.dumps(info)
    e.txn_ids = json.dumps(made)
    db.flush()
    return e


def delete_event(db: Session, event_id: int) -> int:
    """The event, its pair on the other loan, and the book entries they made."""
    e = db.get(AcctLoanEvent, event_id)
    if not e:
        raise HTTPException(status_code=404, detail="Event not found")
    evs = [e] + ([db.get(AcctLoanEvent, e.pair_id)] if e.pair_id and db.get(AcctLoanEvent, e.pair_id) else [])
    n = 0
    for x in evs:
        for tid in json.loads(x.txn_ids or "[]"):
            t = db.get(AcctTxn, tid)
            if t:
                db.delete(t)
                n += 1
        db.delete(x)
    db.flush()
    return n


def interest(db: Session, account_id: int, to: date) -> dict:
    """Interest owed to a date at the loan's rate: day by day on the balance (simple), or monthly compounding -- from the
    last "interest added" event (or the start)."""
    st = statement(db, account_id)
    rate = (st["details"].get("rate") or 0) / 100
    if not rate:
        return {"rate": 0, "interest": 0, "from": None, "to": to.isoformat(), "note": "Set the loan's rate first"}
    last = max((r["date"] for r in st["rows"] if r["type"] == "event" and r["kind"] == "interest"), default=None)
    start = date.fromisoformat(last) if last else date.fromisoformat(st["details"].get("start") or st["rows"][0]["date"]) if st["rows"] else to
    bal_on = defaultdict(float)
    bal = st["opening"]
    days = sorted({r["date"] for r in st["rows"] if r["type"] == "line"})
    for r in st["rows"]:
        if r["type"] == "line":
            bal_on[r["date"]] = r["balance"]
    cur, d, total = st["opening"], start, 0.0
    for dd in days:
        if date.fromisoformat(dd) <= start:
            cur = bal_on[dd]
    compound = (st["details"].get("rate_type") or "simple") == "compound"
    month_acc = 0.0
    while d < to:
        cur = bal_on.get(d.isoformat(), cur)
        day_i = max(0.0, cur) * rate / 365
        if compound:
            month_acc += day_i
            if (d + timedelta(days=1)).day == 1:
                cur += month_acc
                total += month_acc
                month_acc = 0.0
        else:
            total += day_i
        d += timedelta(days=1)
    total += month_acc
    return {"rate": rate * 100, "interest": cents(total), "from": start.isoformat(), "to": to.isoformat(), "compound": compound}


def schedule(db: Session, account_id: int, months: int = 24) -> List[dict]:
    """Payment expected each month (details: payment + due day, from the start) vs repayments actually made."""
    st = statement(db, account_id)
    pay, due = st["details"].get("payment"), st["details"].get("due_day")
    start = st["details"].get("start")
    if not pay or not start:
        return []
    s0 = date.fromisoformat(start)
    out, today = [], A.today()
    y, m = s0.year, s0.month
    paid = defaultdict(float)
    for r in st["rows"]:
        if r["type"] == "line" and r["amount"] < 0 and not r["by_event"]:
            paid[r["date"][:7]] += -r["amount"]
    for _ in range(months):
        key = f"{y}-{m:02d}"
        if date(y, m, 1) > today:
            break
        dd = min(due or 1, 28)
        got = cents(paid.get(key, 0.0))
        out.append({"month": key, "due": date(y, m, dd).isoformat(), "expected": cents(pay), "paid": got,
                    "status": "paid" if got >= pay - 0.5 else "short" if got > 0 else ("missed" if date(y, m, dd) < today else "due")})
        y, m = (y + (m == 12), m % 12 + 1)
    return out


def verify(db: Session, account_id: int, data: bytes, filename: str, tol_days: int = 5) -> dict:
    """The lender's own statement (CSV / Excel) against AT-HUB's lines in this loan: matched (size of amount + date
    within tol_days), amount differs (same date, other amount), only theirs, only AT-HUB's; month totals side by side."""
    rows = A.read_table(data, filename)
    lay = A.guess_layout(rows)
    theirs, bad = A.parse_rows(rows, lay)
    st = statement(db, account_id)
    ours = [r for r in st["rows"] if r["type"] == "line"]
    used, out = set(), {"matched": [], "amount_differs": [], "only_theirs": [], "only_ours": []}
    for r in theirs:
        d = r["date"]
        best = None
        for i, o in enumerate(ours):
            if i in used:
                continue
            days = abs((date.fromisoformat(o["date"]) - d).days)
            if days <= tol_days and abs(abs(o["amount"]) - abs(r["amount"])) < 0.01:
                if best is None or days < best[0]:
                    best = (days, i)
        item = {"date": d.isoformat(), "amount": r["amount"], "description": r["description"]}
        if best:
            used.add(best[1])
            out["matched"].append({**item, "ours": ours[best[1]], "days": best[0]})
            continue
        near = next((i for i, o in enumerate(ours) if i not in used and abs((date.fromisoformat(o["date"]) - d).days) <= 2), None)
        if near is not None:
            used.add(near)
            out["amount_differs"].append({**item, "ours": ours[near], "difference": cents(abs(r["amount"]) - abs(ours[near]["amount"]))})
        else:
            out["only_theirs"].append(item)
    out["only_ours"] = [o for i, o in enumerate(ours) if i not in used and (not theirs or theirs[0]["date"].isoformat() <= o["date"] <= theirs[-1]["date"].isoformat())]
    months = defaultdict(lambda: {"theirs": 0.0, "ours": 0.0})
    for r in theirs:
        months[r["date"].isoformat()[:7]]["theirs"] += abs(r["amount"])
    for o in ours:
        if not theirs or min(r["date"] for r in theirs).isoformat()[:7] <= o["date"][:7] <= max(r["date"] for r in theirs).isoformat()[:7]:
            months[o["date"][:7]]["ours"] += abs(o["amount"])
    return {**{k: v for k, v in out.items()}, "counts": {k: len(v) for k, v in out.items()}, "bad": bad[:10],
            "months": [{"month": k, "theirs": cents(v["theirs"]), "ours": cents(v["ours"]), "difference": cents(v["ours"] - v["theirs"])} for k, v in sorted(months.items())]}


def overview(db: Session) -> List[dict]:
    """A card per loan: owed now, borrowed / repaid, last event, next payment, balance by month (for a small chart)."""
    accounts, _ = A.maps(db)
    out = []
    for a in sorted((x for x in accounts.values() if x.kind == "loan" and not x.parent_id), key=lambda x: x.name.lower()):
        st = statement(db, a.id)
        by_month = {}
        for r in st["rows"]:
            by_month[r["date"][:7]] = r["balance"]
        sch = schedule(db, a.id, 600)
        nxt = next((x for x in sch if x["status"] in ("due", "missed")), None)
        out.append({"account_id": a.id, "name": a.name, "details": st["details"], "owed": st["owed"], "borrowed": st["borrowed"], "repaid": st["repaid"],
                    "moved_or_adjusted": st["moved_or_adjusted"], "open": st["open"], "events": st["events"], "last_event": st["last_event"],
                    "history": st["history"][-3:], "lines": sum(1 for r in st["rows"] if r["type"] == "line"),
                    "months": [{"month": k, "balance": v} for k, v in sorted(by_month.items())][-24:],
                    "next_payment": nxt, "late": sum(1 for x in sch if x["status"] in ("missed", "short"))})
    return out


def statement_xlsx(db: Session, account_id: int) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    st = statement(db, account_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "Statement"
    ws.append([f"{st['account']['path']} -- loan statement"])
    ws["A1"].font = Font(bold=True, size=13)
    for k, v in st["details"].items():
        if v not in (None, ""):
            ws.append([k.replace("_", " ").title(), v])
    ws.append([])
    ws.append(["Date", "What", "Description / Note", "Amount", "Balance Owed", "From"])
    for c in ws[ws._current_row]:
        c.font = Font(bold=True)
    ws.append(["", "Opening", "", "", st["opening"], ""])
    for r in st["rows"]:
        if r["type"] == "line":
            ws.append([r["date"], "Borrowed" if r["amount"] > 0 else "Repaid", r["description"] + (f" ({r['note']})" if r.get("note") else ""), r["amount"], r["balance"],
                       r["source"] + (" (book entry)" if r["book"] else "")])
        else:
            ws.append([r["date"], r["label"], (r.get("text") or "") + (f" -> {r['other_account']}" if r.get("other_account") else ""), r.get("amount"), r["balance"], r.get("created_by") or ""])
        for col in (4, 5):
            ws.cell(ws._current_row, col).number_format = "#,##0.00;[Red]-#,##0.00"
    for col, w in zip("ABCDEF", (12, 26, 70, 14, 14, 22)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
