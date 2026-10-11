"""Accounting, the rest (routes/accounting.py): bank-style search with live totals / groups / stats, comments, send a
line back to Review, the Ignored list, Check My Books, Partner Accounts, Payees, 1099, Recurring, scratch sheets
(Projections, Loans quick calculations) and reconciling against QuickBooks. Built on accounting.py."""
import csv
import io
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.models import (AcctAccount, AcctCheckOk, AcctComment, AcctIgnored, AcctReconcile, AcctSheet, AcctSource, AcctSplit,
                        AcctTxn, AppSetting)
from app.services import accounting as A
from app.services import acct_mapper, acct_rules
from app.services.money import cents


def _setting(db: Session, key: str, default):
    row = db.get(AppSetting, key)
    return json.loads(row.value) if row and row.value else default


def _save_setting(db: Session, key: str, value, by: str) -> None:
    row = db.get(AppSetting, key)
    if not row:
        row = AppSetting(key=key)
        db.add(row)
    row.value, row.updated_by = json.dumps(value), by


def period_key(start: Optional[date], end: Optional[date]) -> Optional[str]:
    return f"{start.isoformat()}..{end.isoformat()}" if start and end else None


def prev_range(start: date, end: date) -> Tuple[date, date]:
    """The period just before, the same length (FY -> the FY before)."""
    days = (end - start).days + 1
    return start - timedelta(days=days), start - timedelta(days=1)


# ================================================================ search
_NUM = r"\$?\d[\d,]*(?:\.\d{1,2})?"


def _num(s: str) -> float:
    return float(s.replace("$", "").replace(",", ""))


def parse_q(q: str) -> dict:
    """A bank-style search box: amounts ("1072.50", "$1,072.50", "1072"), ranges (">500", "<25", "500-1000"), money
    out / in ("-300", "+50"), words (all of them, any order) and "quoted phrases"."""
    out = {"words": [], "phrases": [], "amount": [], "loose_num": []}
    q = q or ""
    out["phrases"] = [p.strip().lower() for p in re.findall(r'"([^"]+)"', q) if p.strip()]
    for tok in re.sub(r'"[^"]*"', " ", q).split():
        m = None
        if re.fullmatch(rf"[<>]=?{_NUM}", tok):
            op = tok[0] + ("=" if tok[1] == "=" else "")
            out["amount"].append(("gt" if op[0] == ">" else "lt", _num(tok.lstrip("<>="))))
        elif (m := re.fullmatch(rf"({_NUM})\s*-\s*({_NUM})", tok)):
            a, b = sorted((_num(m.group(1)), _num(m.group(2))))
            out["amount"].append(("between", (a, b)))
        elif re.fullmatch(rf"[-+]{_NUM}", tok):
            v = _num(tok[1:])
            out["amount"].append(("signed", (-1 if tok[0] == "-" else 1, v, "." in tok)))
        elif re.fullmatch(rf"\$[\d,]+(?:\.\d{{1,2}})?|\d[\d,]*\.\d{{1,2}}|\d{{1,3}}(?:,\d{{3}})+", tok):
            out["amount"].append(("eq", (_num(tok), "." in tok)))
        elif re.fullmatch(r"\d+", tok):
            out["loose_num"].append(tok)  # an amount in whole dollars, or a check / reference number
        else:
            out["words"].append(tok.lower())
    return out


def _amount_ok(cond, amount: float) -> bool:
    a = abs(amount)
    kind, v = cond
    if kind == "gt":
        return a > v - 0.005
    if kind == "lt":
        return a < v + 0.005
    if kind == "between":
        return v[0] - 0.005 <= a <= v[1] + 0.005
    if kind == "eq":
        val, exact = v
        return abs(a - val) < 0.005 if exact else int(a) == int(val)
    if kind == "signed":
        sign, val, exact = v
        return (amount < 0 if sign < 0 else amount > 0) and (abs(a - val) < 0.005 if exact else int(a) == int(val))
    return True


def _text_of(t: AcctTxn, accounts) -> str:
    acc = accounts.get(t.account_id)
    parts = [t.description, t.bank_description or "", t.payee or "", t.note or "", A.path(acc, accounts) if acc else ""]
    parts += [A.path(accounts[x.account_id], accounts) for x in t.splits if x.account_id in accounts]
    return " ".join(parts).lower()


def search(db: Session, p: dict) -> dict:
    """Transactions for the lists (Review, Transactions, drill-downs): every filter, totals / stats / groups for ALL
    that match, the page asked for."""
    accounts, sources = A.maps(db)
    qy = db.query(AcctTxn).options(selectinload(AcctTxn.splits))
    dfrom, dto = A.parse_day(p.get("date_from")), A.parse_day(p.get("date_to"))
    if not p.get("all_dates"):
        dfrom = dfrom or A.parse_day(p.get("start"))
        dto = dto or A.parse_day(p.get("end"))
    if dfrom:
        qy = qy.filter(AcctTxn.date >= A._dt(dfrom))
    if dto:
        qy = qy.filter(AcctTxn.date <= A._dt(dto))
    if p.get("source_id"):
        qy = qy.filter(AcctTxn.source_id == int(p["source_id"]))
    if p.get("account_id"):
        ids = [int(p["account_id"])] + A.descendants(int(p["account_id"]), accounts)
        qy = qy.filter(or_(AcctTxn.account_id.in_(ids), AcctTxn.splits.any(AcctSplit.account_id.in_(ids))))
    if p.get("kind"):
        ids = [a.id for a in accounts.values() if a.kind == p["kind"]]
        qy = qy.filter(or_(AcctTxn.account_id.in_(ids), AcctTxn.splits.any(AcctSplit.account_id.in_(ids))))
    st = p.get("status")
    if st in ("unfiled", "review"):
        qy = qy.filter(AcctTxn.account_id.is_(None), ~AcctTxn.splits.any())
    elif st == "auto":
        qy = qy.filter(AcctTxn.reviewed.is_(False))
    elif st == "expected":
        qy = qy.filter(AcctTxn.expected.is_(True))
    elif st == "typed":
        qy = qy.filter(AcctTxn.origin == "manual")
    elif st == "split":
        qy = qy.filter(AcctTxn.splits.any())
    elif st == "sent_back":
        qy = qy.filter(AcctTxn.sent_back.is_(True))
    if p.get("how"):
        qy = qy.filter(AcctTxn.how == p["how"])
    if p.get("sign") == "in":
        qy = qy.filter(AcctTxn.amount > 0)
    elif p.get("sign") == "out":
        qy = qy.filter(AcctTxn.amount < 0)
    rows = qy.all()
    # amount from / to / exact (always the size of the amount; in / out is "sign")
    amin, amax, amt = (A.parse_money(p.get(k)) if p.get(k) not in (None, "") else None for k in ("amin", "amax", "amt"))
    if amin is not None:
        rows = [t for t in rows if abs(t.amount) >= amin - 0.005]
    if amax is not None:
        rows = [t for t in rows if abs(t.amount) <= amax + 0.005]
    if amt is not None:
        rows = [t for t in rows if abs(abs(t.amount) - amt) < 0.005]
    if p.get("has_note"):
        rows = [t for t in rows if (t.note or "").strip()]
    q = parse_q(p.get("q") or "")
    if any(q.values()):
        keep = []
        for t in rows:
            text = _text_of(t, accounts)
            if any(w not in text for w in q["words"]) or any(ph not in text for ph in q["phrases"]):
                continue
            if not all(_amount_ok(c, t.amount) for c in q["amount"]):
                continue
            if not all(int(abs(t.amount)) == int(n) or re.search(rf"\b{n}\b", text) for n in q["loose_num"]):
                continue
            keep.append(t)
        rows = keep
    # sort
    sort = p.get("sort") or "date_desc"
    key = {"date_desc": lambda t: (t.date, t.id), "date_asc": lambda t: (t.date, t.id), "amount_desc": lambda t: (abs(t.amount), t.id),
           "amount_asc": lambda t: (abs(t.amount), t.id)}.get(sort, lambda t: (t.date, t.id))
    rows.sort(key=key, reverse=sort in ("date_desc", "amount_desc"))
    # totals / stats / groups for everything that matched
    ins = [t.amount for t in rows if t.amount > 0]
    outs = [t.amount for t in rows if t.amount < 0]
    tot = {"count": len(rows), "money_in": cents(sum(ins)), "money_out": cents(sum(outs)), "net": cents(sum(ins) + sum(outs))}
    stats = {}
    if rows:
        ds = [t.date.date() for t in rows]
        months = max(1, (max(ds).year - min(ds).year) * 12 + max(ds).month - min(ds).month + 1)
        stats = {"largest_in": cents(max(ins)) if ins else None, "largest_out": cents(min(outs)) if outs else None,
                 "average": cents(tot["net"] / len(rows)), "first": min(ds).isoformat(), "last": max(ds).isoformat(),
                 "per_month": cents(tot["net"] / months), "months": months}
    groups = []
    gmode = p.get("group")
    if gmode in ("account", "payee", "month", "source"):
        g = defaultdict(lambda: {"count": 0, "in": 0.0, "out": 0.0})
        for t in rows:
            if gmode == "account":
                a = accounts.get(t.account_id)
                k = A.path(a, accounts) if a else ("Split" if t.splits else "Not filed")
            elif gmode == "payee":
                k = t.payee or acct_mapper.payee_label(t.bank_description or t.description)
            elif gmode == "month":
                k = t.date.strftime("%Y-%m")
            else:
                k = sources[t.source_id].name if t.source_id in sources else "?"
            x = g[k]
            x["count"] += 1
            x["in" if t.amount > 0 else "out"] += t.amount
        groups = [{"key": k, "count": v["count"], "in": cents(v["in"]), "out": cents(v["out"]), "net": cents(v["in"] + v["out"])} for k, v in g.items()]
        groups.sort(key=(lambda x: x["key"]) if gmode == "month" else (lambda x: -abs(x["net"])))
    # running balance (one bank account, by date): the bank's balance after each line
    balance = {}
    if p.get("source_id") and sort in ("date_desc", "date_asc"):
        s = sources.get(int(p["source_id"]))
        if s and s.kind != "book":
            run = s.opening_balance or 0.0
            q2 = db.query(AcctTxn.id, AcctTxn.amount, AcctTxn.date).filter(AcctTxn.source_id == s.id, AcctTxn.expected.is_(False))
            if s.opening_date:
                q2 = q2.filter(AcctTxn.date >= s.opening_date)
            for tid, amt_, _d in sorted(q2.all(), key=lambda x: (x[2], x[0])):
                run += amt_
                balance[tid] = cents(run)
    off, lim = int(p.get("offset") or 0), min(int(p.get("limit") or 300), 5000)
    page = rows[off:off + lim]
    return {"rows_obj": page, "all_rows": rows, "total": len(rows), **tot, "stats": stats, "groups": groups,
            "balance": {k: v for k, v in balance.items() if k in {t.id for t in page}}, "has_balance": bool(balance)}


def found_xlsx(db: Session, rows: List[AcctTxn]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    accounts, sources = A.maps(db)
    wb = Workbook()
    ws = wb.active
    ws.title = "Found"
    ws.append(["Date", "Amount", "Description", "Payee", "Account", "Bank Account", "Note"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for t in rows:
        a = accounts.get(t.account_id)
        acc = A.path(a, accounts) if a else (", ".join(f"{A.path(accounts[x.account_id], accounts)} {x.amount:.2f}" for x in t.splits if x.account_id in accounts) or "Not filed")
        ws.append([t.date.date(), t.amount, t.bank_description or t.description, t.payee or "", acc, sources[t.source_id].name if t.source_id in sources else "", t.note or ""])
        ws.cell(ws._current_row, 2).number_format = "#,##0.00;[Red]-#,##0.00"
    ws.append([])
    ws.append(["Total", cents(sum(t.amount for t in rows)), f"{len(rows)} line(s)"])
    ws.cell(ws._current_row, 1).font = Font(bold=True)
    for col, w in zip("ABCDEFG", (12, 13, 70, 26, 28, 20, 30)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ================================================================ comments
def comment_dict(c: AcctComment) -> dict:
    return {"id": c.id, "section": c.section, "period_key": c.period_key, "ref_type": c.ref_type, "ref_id": c.ref_id, "text": c.text,
            "created_by": c.created_by, "created_at": c.created_at, "updated_by": c.updated_by, "updated_at": c.updated_at}


def comments(db: Session, section: Optional[str] = None, pkey: Optional[str] = None, all_periods: bool = False) -> List[dict]:
    q = db.query(AcctComment)
    if section:
        q = q.filter(AcctComment.section == section)
    if pkey and not all_periods:
        q = q.filter(or_(AcctComment.period_key == pkey, AcctComment.period_key.is_(None)))
    return [comment_dict(c) for c in q.order_by(AcctComment.created_at).all()]


# ================================================================ send back to Review
def send_back(db: Session, ids: List[int], reason: Optional[str], by: str) -> int:
    n = 0
    for t in db.query(AcctTxn).options(selectinload(AcctTxn.splits)).filter(AcctTxn.id.in_(ids)).all():
        if not t.account_id and not t.splits:
            continue
        was = {"account_id": t.account_id} if t.account_id else {"splits": [{"account_id": x.account_id, "amount": x.amount, "note": x.note} for x in t.splits]}
        t.sent_back_from = json.dumps(was)
        t.splits.clear()
        t.account_id, t.how, t.rule_id, t.reviewed = None, None, None, True
        t.sent_back, t.sent_back_reason, t.sent_back_by, t.sent_back_at = True, (reason or "").strip() or None, by, datetime.utcnow()
        t.updated_by = by
        n += 1
    db.commit()
    return n


def undo_send_back(db: Session, ids: List[int], by: str) -> int:
    n = 0
    for t in db.query(AcctTxn).options(selectinload(AcctTxn.splits)).filter(AcctTxn.id.in_(ids)).all():
        if not t.sent_back_from or t.account_id or t.splits:
            continue
        was = json.loads(t.sent_back_from)
        if was.get("account_id"):
            t.account_id = was["account_id"]
        for x in was.get("splits") or []:
            t.splits.append(AcctSplit(account_id=x["account_id"], amount=x["amount"], note=x.get("note")))
        t.how, t.reviewed, t.sent_back, t.updated_by = "person", True, None, by
        n += 1
    db.commit()
    return n


# ================================================================ Ignored (duplicates / estimates)
def ignored(db: Session, import_id: Optional[int] = None, status: Optional[str] = None) -> dict:
    accounts, sources = A.maps(db)
    q = db.query(AcctIgnored)
    if import_id:
        q = q.filter(AcctIgnored.import_id == import_id)
    if status:
        q = q.filter(AcctIgnored.status == status)
    rows = q.order_by(AcctIgnored.status.desc(), AcctIgnored.date.desc()).all()
    dup_ids = {r.dup_of_txn_id for r in rows if r.dup_of_txn_id}
    dups = {t.id: A.txn_dict(t, accounts, sources) for t in db.query(AcctTxn).options(selectinload(AcctTxn.splits)).filter(AcctTxn.id.in_(dup_ids)).all()} if dup_ids else {}
    out = [{"id": r.id, "import_id": r.import_id, "source": sources[r.source_id].name if r.source_id in sources else "", "date": r.date.date().isoformat() if r.date else None,
            "amount": r.amount, "description": r.description, "extra": json.loads(r.extra) if r.extra else {}, "reason": r.reason, "status": r.status,
            "dup_of": dups.get(r.dup_of_txn_id), "restored_txn_id": r.restored_txn_id, "decided_by": r.decided_by, "created_at": r.created_at} for r in rows]
    c = Counter(r["status"] for r in out)
    return {"rows": out, "possible": c["possible"], "ignored": c["ignored"], "restored": c["restored"],
            "total_ignored": cents(sum(r["amount"] or 0 for r in out if r["status"] == "ignored"))}


def decide_ignored(db: Session, ids: List[int], action: str, by: str) -> int:
    """ignore: it IS a duplicate (stays out) | keep / restore: it isn't -- it becomes a normal line, in Review."""
    n = 0
    for r in db.query(AcctIgnored).filter(AcctIgnored.id.in_(ids)).all():
        if action == "ignore":
            if r.status == "possible":
                r.status, r.decided_by = "ignored", by
                n += 1
        elif action in ("keep", "restore"):
            if r.status == "restored":
                continue
            src = db.get(AcctSource, r.source_id) or A.book_source(db)
            t = AcctTxn(source_id=src.id, date=r.date, amount=r.amount or 0.0, description=r.description or "",
                        payee=acct_mapper.payee_label(r.description or ""), origin="import", import_id=r.import_id,
                        dedupe=f"{r.dedupe or ''}|kept{r.id}", created_by=by)
            db.add(t)
            db.flush()
            A.autofill(db, [t])
            r.status, r.restored_txn_id, r.decided_by = "restored", t.id, by
            n += 1
        else:
            raise HTTPException(status_code=400, detail="ignore, keep or restore")
    db.commit()
    return n


# ================================================================ Check My Books
CHECKS = {
    "odd_one_out": ("Odd One Out", "high"), "rule_says": ("A Rule Says Another Account", "high"),
    "rules_disagree": ("Two Rules Disagree", "low"), "direction": ("Money Going The Wrong Way", "med"),
    "unusual": ("Unusual Amount", "med"), "duplicate": ("Possible Duplicate", "high"), "split": ("Split Doesn't Add Up", "high"),
    "inactive": ("In A Switched-Off Account", "low"), "loan_negative": ("Loan Repaid More Than Borrowed", "med"),
    "transfer_net": ("Transfers Don't Net Out", "low"), "book_no_note": ("Book Entry Without A Note", "low"),
    "auto_old": ("AT-HUB's Pick Not Looked At (30+ Days)", "low"),
}


def check_books(db: Session, start: Optional[date], end: Optional[date]) -> dict:
    accounts, sources = A.maps(db)
    oks = {(o.txn_id, o.check) for o in db.query(AcctCheckOk).all()}
    every = db.query(AcctTxn).options(selectinload(AcctTxn.splits)).all()
    inp = [t for t in every if (not start or t.date.date() >= start) and (not end or t.date.date() <= end)]
    findings = []
    inp_ids = {t.id for t in inp}

    def add(check, t, why, suggest=None, also=None, ref=None):
        if t is not None and (t.id, check) in oks:
            return
        label, sev = CHECKS[check]
        findings.append({"key": f"{check}:{t.id if t is not None else ref}", "check": check, "label": label, "severity": sev, "why": why,
                         "txn": A.txn_dict(t, accounts, sources) if t is not None else None,
                         "also": [A.txn_dict(x, accounts, sources) for x in (also or [])],
                         "suggest": {"account_id": suggest, "account": A.path(accounts[suggest], accounts)} if suggest in accounts else None})

    # odd one out: a payee whose lines nearly all go to one account, this one went elsewhere
    groups = defaultdict(list)
    for t in every:
        if t.account_id and not t.splits:
            groups[(acct_mapper.payee(t.bank_description or t.description), t.amount >= 0)].append(t)
    for _k, ts in groups.items():
        if len(ts) < 4:
            continue
        c = Counter(t.account_id for t in ts)
        top, n = c.most_common(1)[0]
        if n / len(ts) < 0.8:
            continue
        for t in ts:
            if t.account_id != top and c[t.account_id] <= max(1, len(ts) // 10) and t.id in inp_ids and top in accounts:
                add("odd_one_out", t, f"{n} of {len(ts)} lines from {acct_mapper.payee_label(t.bank_description or t.description)} are in "
                                      f"{A.path(accounts[top], accounts)}; this one is in {A.path(accounts[t.account_id], accounts) if t.account_id in accounts else '?'}", top)
    # rules
    specs = [s for s in A.rule_specs(db) if s["enabled"] and s["action"] != "suggest"]
    for t in inp:
        if not t.account_id or t.splits:
            continue
        line = acct_rules.line_of(t)
        hits = [s for s in specs if acct_rules.matches(s, line)]
        if hits and hits[0]["account_id"] and t.account_id != hits[0]["account_id"] and not hits[0]["splits"] and t.rule_id != hits[0]["id"]:
            add("rule_says", t, f"Rule \"{hits[0]['name'] or acct_rules.describe(hits[0])}\" puts lines like this in "
                                f"{A.path(accounts[hits[0]['account_id']], accounts) if hits[0]['account_id'] in accounts else '?'}", hits[0]["account_id"])
        if len({s["account_id"] for s in hits}) > 1:
            add("rules_disagree", t, "These rules all match it and point to different accounts (the first one in the order wins): "
                + "; ".join(f"{s['name'] or acct_rules.describe(s)}" for s in hits[:3]))
    # direction / switched off / splits / book entries / old auto picks
    today = A.today()
    for t in inp:
        a = accounts.get(t.account_id)
        if a:
            if a.kind == "income" and t.amount < 0:
                add("direction", t, f"Money out in {A.path(a, accounts)}, an income account")
            elif a.kind in ("cogs", "expense") and t.amount > 0:
                add("direction", t, f"Money in in {A.path(a, accounts)}, a {A.KINDS[a.kind][0].lower()} account -- a refund? Then it's fine")
            if not a.is_active:
                add("inactive", t, f"{A.path(a, accounts)} is switched off")
        if t.splits and abs(sum(x.amount for x in t.splits) - t.amount) > 0.01:
            add("split", t, f"The parts add up to {sum(x.amount for x in t.splits):,.2f}, the line is {t.amount:,.2f}")
        if t.source_id in sources and sources[t.source_id].kind == "book" and not (t.note or "").strip():
            add("book_no_note", t, "A book entry (not on a statement) with no note to say why")
        if t.how in ("auto", "rule") and not t.reviewed and t.created_at and (today - t.created_at.date()).days > 30:
            add("auto_old", t, "AT-HUB filed it by itself over a month ago and nobody has looked")
    # unusual amounts per account
    per_acc = defaultdict(list)
    for t in every:
        if t.account_id and not t.splits:
            per_acc[t.account_id].append(abs(t.amount))
    med = {k: statistics.median(v) for k, v in per_acc.items() if len(v) >= 5}
    for t in inp:
        if t.account_id in med and abs(t.amount) > 500 and abs(t.amount) > 5 * med[t.account_id]:
            add("unusual", t, f"{abs(t.amount):,.2f} is over 5x the usual {med[t.account_id]:,.2f} for {A.path(accounts[t.account_id], accounts)}")
    # possible duplicates already in the books
    by_amt = defaultdict(list)
    for t in inp:
        by_amt[(t.source_id, round(t.amount, 2))].append(t)
    for _k, ts in by_amt.items():
        ts.sort(key=lambda x: (x.date, x.id))
        for i, t in enumerate(ts):
            for u in ts[i + 1:]:
                if (u.date - t.date).days > 3:
                    break
                if A.same_line(t.bank_description or t.description, u.bank_description or u.description, (u.date - t.date).days):
                    add("duplicate", u, f"Looks like the {t.date.date().isoformat()} line of the same amount", also=[t])
    # account-level: loans repaid more than borrowed, transfers that don't net out
    for l_ in A.loans(db, end):
        if l_["owed"] < -0.5:
            add("loan_negative", None, f"{l_['name']}: repaid {abs(l_['owed']):,.2f} more than borrowed (owed below zero)", ref=f"loan{l_['account_id']}")
    tnet = defaultdict(float)
    for t in inp:
        a = accounts.get(t.account_id)
        if a and a.kind == "transfer":
            tnet[a.id] += t.amount
    for aid, v in tnet.items():
        if abs(v) > 1:
            add("transfer_net", None, f"{A.path(accounts[aid], accounts)} nets to {v:,.2f} in this period (transfers usually come back to ~0)", ref=f"transfer{aid}")
    order = {"high": 0, "med": 1, "low": 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["check"], f["txn"]["date"] if f["txn"] else ""))
    counts = Counter(f["check"] for f in findings)
    return {"findings": findings, "counts": [{"check": k, "label": CHECKS[k][0], "severity": CHECKS[k][1], "count": counts[k]} for k in CHECKS if counts[k]],
            "total": len(findings)}


# ================================================================ Partner Accounts / Payees / 1099 / Recurring
def _partner_of(a: AcctAccount, accounts) -> Optional[str]:
    for x in [a] + A.ancestors(a, accounts):
        if x.partner:
            return x.partner
    return None


def partner_accounts(db: Session, start: date, end: date, fy_key: str) -> dict:
    """Per partner: withdrawn (money out on their partner accounts), invested (money in on them), loans they made the
    company (loan accounts tagged with them), net, by fiscal year (all time) and by month (the period)."""
    s = A.settings(db)
    accounts = {a.id: a for a in db.query(AcctAccount).all()}
    names = [p_["name"] for p_ in s["partners"]]
    for a in accounts.values():
        if a.kind in ("owner", "loan"):
            pn = _partner_of(a, accounts) or (a.name if a.kind == "owner" and not a.parent_id else None)
            if pn and pn.strip().lower() not in {n.lower() for n in names}:
                names.append(pn)
    m = s["fy_start_month"]
    pt = A.partners(db, start, end, fy_key)
    share = {x["name"].lower(): x for x in pt["partners"]}
    out = []
    for name in names:
        key = name.strip().lower()
        accs = [a for a in accounts.values() if a.kind in ("owner", "loan") and ((_partner_of(a, accounts) or (a.name if a.kind == "owner" else "")).strip().lower() == key)]
        ids = {a.id for a in accs}
        kind_of = {a.id: a.kind for a in accs}
        lines = [l for l in A.lines(db) if l["account_id"] in ids]
        def tot(ls):
            w = sum(l["amount"] for l in ls if kind_of[l["account_id"]] == "owner" and l["amount"] < 0)
            i = sum(l["amount"] for l in ls if kind_of[l["account_id"]] == "owner" and l["amount"] > 0)
            lent = sum(l["amount"] for l in ls if kind_of[l["account_id"]] == "loan" and l["amount"] > 0)
            repaid = sum(l["amount"] for l in ls if kind_of[l["account_id"]] == "loan" and l["amount"] < 0)
            return {"withdrawn": cents(w), "invested": cents(i), "net": cents(w + i), "lent": cents(lent), "repaid": cents(repaid), "loan_owed": cents(lent + repaid), "count": len(ls)}
        period = [l for l in lines if start <= l["date"] <= end]
        by_fy = defaultdict(list)
        for l in lines:
            by_fy[A.fy_label(A.fy_start(l["date"], m), m)].append(l)
        by_month = defaultdict(list)
        for l in period:
            by_month[l["date"].strftime("%Y-%m")].append(l)
        sh = share.get(key)
        out.append({"name": name, "account_ids": sorted(ids), "accounts": [A.path(a, accounts) for a in accs],
                    "period": tot(period), "all_time": tot(lines),
                    "by_fy": [{"fy": k, **tot(v)} for k, v in sorted(by_fy.items())],
                    "by_month": [{"month": k, **tot(v)} for k, v in sorted(by_month.items())],
                    "share": sh["share"] if sh else None, "share_pct": sh["share_pct"] if sh else None,
                    "remaining": sh["remaining"] if sh else None, "adjust": sh["adjust"] if sh else 0})
    return {"partners": out, "to_share": pt["total"], "year": fy_key}


def payees(db: Session, start: date, end: date) -> List[dict]:
    accounts, _ = A.maps(db)
    g = defaultdict(lambda: {"count": 0, "in": 0.0, "out": 0.0, "last": None, "first": None, "accounts": Counter(), "months": set()})
    for t in db.query(AcctTxn).filter(AcctTxn.date >= A._dt(start), AcctTxn.date <= A._dt(end)).all():
        a = accounts.get(t.account_id)
        if a and a.kind == "pending":
            continue
        k = t.payee or acct_mapper.payee_label(t.bank_description or t.description)
        x = g[k]
        x["count"] += 1
        x["in" if t.amount > 0 else "out"] += t.amount
        d = t.date.date()
        x["last"] = max(x["last"], d) if x["last"] else d
        x["first"] = min(x["first"], d) if x["first"] else d
        x["months"].add(d.strftime("%Y-%m"))
        x["accounts"][A.path(a, accounts) if a else "Not filed"] += 1
    out = [{"payee": k, "count": v["count"], "in": cents(v["in"]), "out": cents(v["out"]), "net": cents(v["in"] + v["out"]),
            "first": v["first"].isoformat(), "last": v["last"].isoformat(), "months": len(v["months"]),
            "accounts": [{"account": a, "count": c} for a, c in v["accounts"].most_common(3)]} for k, v in g.items()]
    out.sort(key=lambda x: -abs(x["net"]))
    return out


TAX_KEY = "acct_1099"


def tax1099(db: Session, year: int, threshold: float = 600.0) -> dict:
    """People / businesses paid >= $600 in a calendar year (money out filed in cost of goods / expenses / other) -- the
    1099 list for the CPA. Each can be marked needs a 1099 / doesn't (a card company, a corporation)."""
    accounts, _ = A.maps(db)
    marks = _setting(db, TAX_KEY, {}).get(str(year), {})
    g = defaultdict(lambda: {"total": 0.0, "count": 0, "accounts": Counter(), "first": None, "last": None})
    for t in db.query(AcctTxn).options(selectinload(AcctTxn.splits)).filter(AcctTxn.date >= datetime(year, 1, 1), AcctTxn.date < datetime(year + 1, 1, 1), AcctTxn.amount < 0).all():
        parts = [(x.account_id, x.amount) for x in t.splits] or [(t.account_id, t.amount)]
        for aid, amt in parts:
            a = accounts.get(aid)
            if not a or a.kind not in ("cogs", "expense", "other"):
                continue
            k = t.payee or acct_mapper.payee_label(t.bank_description or t.description)
            x = g[k]
            x["total"] += -amt
            x["count"] += 1
            x["accounts"][A.path(a, accounts)] += 1
            d = t.date.date()
            x["first"] = min(x["first"], d) if x["first"] else d
            x["last"] = max(x["last"], d) if x["last"] else d
    rows = [{"payee": k, "total": cents(v["total"]), "count": v["count"], "first": v["first"].isoformat(), "last": v["last"].isoformat(),
             "accounts": [a for a, _c in v["accounts"].most_common(3)], "mark": marks.get(k)} for k, v in g.items() if v["total"] >= threshold - 0.005]
    rows.sort(key=lambda x: -x["total"])
    return {"year": year, "threshold": threshold, "rows": rows, "needs": cents(sum(r["total"] for r in rows if r["mark"] == "yes"))}


def mark1099(db: Session, year: int, payee: str, mark: Optional[str], by: str) -> None:
    allm = _setting(db, TAX_KEY, {})
    y = allm.setdefault(str(year), {})
    if mark in ("yes", "no"):
        y[payee] = mark
    else:
        y.pop(payee, None)
    _save_setting(db, TAX_KEY, allm, by)
    db.commit()


def recurring(db: Session) -> List[dict]:
    """Payments that keep coming back (rent, loan payments, payroll, card payments): how often, how much, when the next
    one is due, and ones that look missed."""
    accounts, _ = A.maps(db)
    today = A.today()
    since = today - timedelta(days=460)
    g = defaultdict(list)
    for t in db.query(AcctTxn).filter(AcctTxn.date >= A._dt(since), AcctTxn.expected.is_(False)).all():
        g[(acct_mapper.payee(t.bank_description or t.description), t.amount >= 0)].append(t)
    out = []
    for (k, money_in), ts in g.items():
        if len(ts) < 3 or k.startswith("text:") and len(k) < 10:
            continue
        ts.sort(key=lambda t: t.date)
        gaps = [(b.date - a.date).days for a, b in zip(ts, ts[1:]) if (b.date - a.date).days > 0]
        if len(gaps) < 2:
            continue
        gap = statistics.median(gaps)
        how = "weekly" if 5 <= gap <= 9 else "every 2 weeks" if 12 <= gap <= 16 else "monthly" if 26 <= gap <= 35 else "quarterly" if 84 <= gap <= 96 else None
        if not how:
            continue
        regular = sum(1 for x in gaps if abs(x - gap) <= max(3, gap * 0.25)) / len(gaps)
        if regular < 0.6:
            continue
        amts = [abs(t.amount) for t in ts]
        medamt = statistics.median(amts)
        last = ts[-1].date.date()
        nxt = last + timedelta(days=round(gap))
        acc = Counter(t.account_id for t in ts if t.account_id).most_common(1)
        out.append({"key": k, "payee": ts[-1].payee or acct_mapper.payee_label(ts[-1].bank_description or ts[-1].description),
                    "money": "in" if money_in else "out", "how_often": how, "every_days": round(gap), "amount": cents(medamt),
                    "same_amount": (max(amts) - min(amts)) <= max(1.0, medamt * 0.1), "times": len(ts), "last": last.isoformat(),
                    "next": nxt.isoformat(), "missed": today > nxt + timedelta(days=max(5, round(gap * 0.3))),
                    "account": A.path(accounts[acc[0][0]], accounts) if acc and acc[0][0] in accounts else None,
                    "per_month": cents(medamt * 30.4 / gap * (1 if money_in else -1))})
    out.sort(key=lambda x: x["next"])
    return out


# ================================================================ scratch sheets (Projections, Loans quick calculations)
def sheet_dict(s: AcctSheet) -> dict:
    return {"id": s.id, "kind": s.kind, "name": s.name, "owner": s.owner, "doc": json.loads(s.doc or "{}"), "created_by": s.created_by,
            "created_at": s.created_at, "updated_by": s.updated_by, "updated_at": s.updated_at}


def books_snapshot(db: Session, months: int = 6) -> dict:
    """Today's figures from the books, for "Fill from the books" on a projection."""
    sources = [A.source_dict(db, s) for s in db.query(AcctSource).filter(AcctSource.kind != "book", AcctSource.is_active.is_(True)).all()]
    oi = A.open_items(db)
    groups = defaultdict(float)
    for r in oi["items"]:
        if r["status"] == "open":
            groups[(r["side"], r["group"])] += r["amount"]
    today = A.today()
    end = today.replace(day=1) - timedelta(days=1)
    start = date(end.year, end.month, 1)
    for _ in range(months - 1):
        start = date(start.year - (start.month == 1), (start.month - 2) % 12 + 1, 1)
    p = A.pnl(db, start, end, "month")
    k = {x["kind"]: x["total"] for x in p["sections"]}
    return {"banks": [{"name": s["name"], "balance": s["balance"] if s["opening_date"] else None, "has_opening": bool(s["opening_date"])} for s in sources],
            "loans": [{"name": l_["name"], "owed": l_["owed"]} for l_ in A.loans(db) if abs(l_["owed"]) > 0.004],
            "ar": [{"name": g, "amount": cents(v)} for (side, g), v in groups.items() if side == "ar"],
            "ap": [{"name": g, "amount": cents(v)} for (side, g), v in groups.items() if side == "ap"],
            "monthly": {"months": months, "from": start.isoformat(), "to": end.isoformat(), "income": cents(k.get("income", 0) / months),
                        "cogs": cents(k.get("cogs", 0) / months), "expense": cents(k.get("expense", 0) / months)},
            "recurring": [r for r in recurring(db) if r["money"] == "out"][:20],
            "partners": A.settings(db)["partners"]}


_REF = re.compile(r"\bR(\d+)\b")


def sheet_xlsx(name: str, doc: dict) -> bytes:
    """A scenario as an Excel sheet: Group | Name | Amount (formulas kept: R3 -> C4) | Month | Note."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = (name or "Projection")[:31]
    ws.append(["Group", "Name", "Amount", "Month", "Note"])
    for c in ws[1]:
        c.font = Font(bold=True)
    def to_xl(f: str):
        f = str(f or "")
        if not f.startswith("="):
            try:
                return float(f.replace(",", "").replace("$", ""))
            except ValueError:
                return f
        f = re.sub(r'GROUP\("([^"]+)"\)', lambda m: f'SUMIF(A:A,"{m.group(1)}",C:C)', f, flags=re.I)
        return _REF.sub(lambda m: f"C{int(m.group(1)) + 1}", f)
    for r in doc.get("rows", []):
        ws.append([r.get("group", ""), r.get("name", ""), to_xl(r.get("a", "")), r.get("month", ""), r.get("note", "")])
        ws.cell(ws._current_row, 3).number_format = "#,##0.00;[Red]-#,##0.00"
    for col, w in zip("ABCDE", (22, 34, 16, 12, 40)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ================================================================ reconcile against QuickBooks
QB_MAP_KEY = "acct_qb_map"
QB_HEADS = {"date": ("date", "transaction date", "txn date"), "amount": ("amount",), "debit": ("debit",), "credit": ("credit",),
            "name": ("name", "payee", "vendor", "customer"), "memo": ("memo/description", "memo", "description"),
            "account": ("account", "account full name", "account name"), "type": ("transaction type", "type"), "num": ("num", "no.", "ref no.")}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def parse_qb(data: Optional[bytes], filename: Optional[str], text: Optional[str]) -> dict:
    """QuickBooks lines from a CSV / Excel export or text pasted from Excel. Understands "Transaction Detail by Account"
    (account names as group rows, "Total for ..." rows) as well as a plain list with an Account column."""
    if data is not None:
        rows = A.read_table(data, filename or "qb.csv")
    else:
        txt = (text or "").strip()
        if not txt:
            raise HTTPException(status_code=400, detail="Paste the QuickBooks lines or pick a file")
        delim = "\t" if "\t" in txt.splitlines()[0] or "\t" in txt else ","
        rows = [r for r in csv.reader(io.StringIO(txt), delimiter=delim) if any((c or "").strip() for c in r)]
    head_i, cols = None, {}
    for i, r in enumerate(rows[:30]):
        cells = [_norm(str(c or "")) for c in r]
        found = {}
        for key, names in QB_HEADS.items():
            for j, c in enumerate(cells):
                if c in names and j not in found.values() and key not in found:
                    found[key] = j
        if "date" in found and ("amount" in found or "debit" in found):
            head_i, cols = i, found
            break
    if head_i is None:
        raise HTTPException(status_code=400, detail="Couldn't find the header row (Date, Amount, Name / Memo, Account) -- copy it with the lines")
    out, current, skipped = [], None, 0
    for r in rows[head_i + 1:]:
        cell = lambda k: (r[cols[k]] if k in cols and cols[k] < len(r) else None)
        d = A.parse_day(cell("date"))
        if cols.get("amount") is not None:
            amt = A.parse_money(cell("amount"))
        else:
            dr, cr = A.parse_money(cell("debit")), A.parse_money(cell("credit"))
            amt = None if dr is None and cr is None else (dr or 0) - (cr or 0)
        first = str(r[0] or "").strip() if r else ""
        if d is None and amt is None:
            if first and not first.lower().startswith("total") and not any(str(c or "").strip() for c in r[1:]):
                current = first  # "Partner Distributions:Anuj" group row
            continue
        if d is None or amt is None:
            if first.lower().startswith("total"):
                continue
            skipped += 1
            continue
        acct = str(cell("account") or "").strip() or current or ""
        out.append({"i": len(out), "date": d.isoformat(), "amount": cents(amt), "name": str(cell("name") or "").strip(), "memo": str(cell("memo") or "").strip(),
                    "account": acct, "type": str(cell("type") or "").strip(), "num": str(cell("num") or "").strip()})
    return {"rows": out, "skipped": skipped, "columns": {k: v for k, v in cols.items()}}


def qb_map(db: Session) -> Dict[str, int]:
    return {k: int(v) for k, v in _setting(db, QB_MAP_KEY, {}).items()}


def guess_map(name: str, accounts: Dict[int, AcctAccount]) -> Optional[int]:
    """'Partner Distributions:Anuj' -> AT-HUB's Anuj; the last part of a QuickBooks name against account names / paths."""
    parts = [_norm(x) for x in re.split(r"[:›>]", name or "") if x.strip()]
    if not parts:
        return None
    by_path = {_norm(A.path(a, accounts)).replace(" › ", ":"): a.id for a in accounts.values()}
    full = ":".join(parts)
    if full in by_path:
        return by_path[full]
    last = parts[-1]
    hits = [a.id for a in accounts.values() if _norm(a.name) == last]
    if len(hits) == 1:
        return hits[0]
    hits = [a.id for a in accounts.values() if a.partner and _norm(a.partner) == last and a.kind == "owner" and not a.parent_id]
    return hits[0] if len(hits) == 1 else None


def reconcile(db: Session, account_ids: List[int], start: date, end: date, qb_rows: List[dict], mapping: Dict[str, int], tol_days: int = 5) -> dict:
    """Each QuickBooks line against AT-HUB's lines (any account -- that's how a line in the wrong account is found):
    same size of amount, date within tol_days, the closest date / most shared words. Buckets: same account / a different
    account / QuickBooks account not mapped yet / only in QuickBooks / only in AT-HUB (in the accounts picked)."""
    accounts, sources = A.maps(db)
    picked = set()
    for aid in account_ids:
        picked |= {aid, *A.descendants(aid, accounts)}
    pool = (db.query(AcctTxn).options(selectinload(AcctTxn.splits))
            .filter(AcctTxn.date >= A._dt(start - timedelta(days=tol_days)), AcctTxn.date <= A._dt(end + timedelta(days=tol_days))).all())
    by_amt = defaultdict(list)
    for t in pool:
        by_amt[round(abs(t.amount), 2)].append(t)
    used = set()
    out = {"same": [], "different": [], "unmapped": [], "qb_only": [], "athub_only": []}
    unmapped_names = Counter()
    guessed = {}
    for nm in {r["account"] for r in qb_rows}:
        if _norm(nm) not in mapping:
            g_ = guess_map(nm, accounts)
            if g_:
                guessed[_norm(nm)] = g_
    eff = {**guessed, **mapping}  # a saved / picked mapping wins over a guess

    def in_tree(acc_id, root):
        return acc_id == root or root in [x.id for x in A.ancestors(accounts[acc_id], accounts)] if acc_id in accounts else False

    for r in qb_rows:
        d = A.parse_day(r["date"])
        mapped = eff.get(_norm(r["account"]))
        cands = []
        words = set(re.findall(r"[a-z0-9]+", (r["name"] + " " + r["memo"]).lower()))
        for t in by_amt.get(round(abs(r["amount"]), 2), []):
            if t.id in used:
                continue
            days = abs((t.date.date() - d).days)
            if days > tol_days:
                continue
            tw = set(re.findall(r"[a-z0-9]+", ((t.bank_description or t.description) + " " + (t.payee or "") + " " + (t.note or "")).lower()))
            shared = len(words & tw)
            in_picked = (t.account_id in picked) or any(x.account_id in picked for x in t.splits)
            cands.append((-(1 if in_picked else 0), days, -shared, t.id, t))
        rr = dict(r)
        rr["mapped_account_id"] = mapped
        rr["mapped_account"] = A.path(accounts[mapped], accounts) if mapped in accounts else None
        rr["mapped_by"] = "you" if _norm(r["account"]) in mapping else ("guess" if mapped else None)
        if not cands:
            out["qb_only"].append(rr)
            if not mapped:
                unmapped_names[r["account"]] += 1
            continue
        cands.sort(key=lambda x: x[:4])
        t = cands[0][4]
        used.add(t.id)
        rr["athub"] = A.txn_dict(t, accounts, sources)
        if not mapped:
            unmapped_names[r["account"]] += 1
            out["unmapped"].append(rr)
            continue
        ok = in_tree(t.account_id, mapped) if t.account_id else any(in_tree(x.account_id, mapped) and abs(abs(x.amount) - abs(r["amount"])) < 0.01 for x in t.splits)
        out["same" if ok else "different"].append(rr)
    for t in pool:
        if t.id in used or not (start <= t.date.date() <= end):
            continue
        if t.account_id in picked or any(x.account_id in picked for x in t.splits):
            out["athub_only"].append(A.txn_dict(t, accounts, sources))
    # totals per picked account: AT-HUB vs QuickBooks
    totals = []
    for aid in account_ids:
        if aid not in accounts:
            continue
        tree = {aid, *A.descendants(aid, accounts)}
        at_total = 0.0
        for t in pool:
            if not (start <= t.date.date() <= end):
                continue
            parts = [(x.account_id, x.amount) for x in t.splits] or [(t.account_id, t.amount)]
            at_total += sum(a_ for acc, a_ in parts if acc in tree)
        qb_total = sum(r["amount"] for r in qb_rows if eff.get(_norm(r["account"])) in tree)
        totals.append({"account_id": aid, "account": A.path(accounts[aid], accounts), "athub": cents(at_total), "qb": cents(qb_total),
                       "athub_abs": cents(abs(at_total)), "qb_abs": cents(abs(qb_total)), "difference": cents(abs(at_total) - abs(qb_total))})
    names = Counter(r["account"] for r in qb_rows)
    return {**{k: v for k, v in out.items()}, "totals": totals,
            "mapping": [{"name": n, "count": c, "account_id": eff.get(_norm(n)), "account": A.path(accounts[eff[_norm(n)]], accounts) if eff.get(_norm(n)) in accounts else None,
                         "by": "you" if _norm(n) in mapping else ("guess" if eff.get(_norm(n)) else None)} for n, c in names.most_common()],
            "counts": {k: len(v) for k, v in out.items()},
            "unmapped_names": [{"name": n, "count": c, "guess": guess_map(n, accounts),
                                "guess_account": A.path(accounts[guess_map(n, accounts)], accounts) if guess_map(n, accounts) in accounts else None}
                               for n, c in unmapped_names.most_common()]}


def drop_estimate_accounts(db: Session) -> dict:
    """One-off (user, 2026-10-11): the old year-end AR / AP estimate accounts (kind "pending") and their lines go -- they
    were 2024 guesses, already left out of every total. Rules pointing at them go too."""
    accs = db.query(AcctAccount).filter(AcctAccount.kind == "pending").all()
    ids = [a.id for a in accs]
    if not ids:
        return {"accounts": 0, "lines": 0}
    lines = db.query(AcctTxn).filter(AcctTxn.account_id.in_(ids)).all()
    total = cents(sum(t.amount for t in lines))
    for t in lines:
        db.delete(t)
    db.query(AcctSplit).filter(AcctSplit.account_id.in_(ids)).delete(synchronize_session=False)
    from app.models import AcctRule
    db.query(AcctRule).filter(AcctRule.account_id.in_(ids)).delete(synchronize_session=False)
    db.query(AcctAccount).filter(AcctAccount.parent_id.in_(ids)).update({AcctAccount.parent_id: None}, synchronize_session=False)
    for a in accs:
        db.delete(a)
    db.commit()
    return {"accounts": len(ids), "lines": len(lines), "total": total, "names": [a.name for a in accs]}
