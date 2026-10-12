"""Accounting, third batch (user, 2026-10-11): rules that disagree go to Review (unless one wins), a rule that files but
still needs review, more rule building blocks (QuickBooks memo, groups, NOT, dates, pattern); QuickBooks' pasted memo taken
off the bank's wording; a QuickBooks split found on import / in Reconcile; splits remembered across a fresh start; Undo for
every change; Super-admin-only statement Undo and Start Fresh; split parts follow the line's sign; combine / health of rules.
Made-up data."""
import io
from datetime import datetime

from openpyxl import Workbook

from tests.builders import uid
from tests.test_accounting import _csv, _import, _source, _txns, _xlsx
from tests.test_accounting_rules import c, name
from tests.test_roles import _user


def _qb(rows) -> bytes:
    """A QuickBooks register: Amount = money out, Deposit = money in, Account (blank = split in QuickBooks)."""
    wb = Workbook()
    ws = wb.active
    ws.append(["Date", "Description", "Amount", "Deposit", "Ref No.", "Payee", "Reconciliation Status", "Balance", "Type", "Account", "Added in Banking"])
    for d, desc, out, inn, acct in rows:
        ws.append([d, desc, out, inn, None, None, "Cleared", 0, "Expense" if out else "Deposit", acct, "Automatically added with rule"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _up(client, h, src_id, data, fname="Register from QB.xlsx", path="/api/accounting/import", **form):
    r = client.post(path, headers=h, data={"source_id": str(src_id), **{k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in form.items()}},
                    files={"file": (fname, io.BytesIO(data), "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()


def test_quickbooks_pasted_memo_comes_off_and_rules_dont_read_it(api, client, admin_headers):
    y = 2060
    src = _source(api)
    nir = api.post("/api/accounting/accounts", json={"name": uid("Nir "), "kind": "owner"})
    cc = api.post("/api/accounting/accounts", json={"name": uid("CC Pay "), "kind": "transfer"})
    who = name("SHAHQ")
    rule = api.post("/api/accounting/rules", json={"conditions": [c("text", "contains", f"{who} SHARE")], "sign": "out", "account_id": nir["id"]})["id"]
    tail = f"WF Direct Pay-Payment- {who} Share-Tran ID DPAAAC7JZT"
    rows = [
        (f"03/05/{y + 1}", tail, 25000, None, "Partner distributions"),  # the line QuickBooks' rule was made from
        (f"04/29/{y}", f"ONLINE TRANSFER TO M & T BANK CHK XXXXXX6636 A. MITTAL REF #F20MZQRQVL CC PAYMENT BUSINESS {tail}", 10000, None, "Partner distributions"),
        (f"01/17/{y}", f"ONLINE TRANSFER REF #IB0LY2Q78Z TO BUSINESS CARD XXXXXXXXXXXX2414 ON 01/17/24 {tail}", 2482.62, None, "Partner distributions"),
        (f"02/08/{y}", "DIRECT PAY MONTHLY BASE DIRECT PAY MONTHLY BASE", 10, None, "Bank fees"),
        (f"06/12/{y}", "WT 240612-023157 AXIS BANK LIMITED /BNF=AKBARALI ENTERPRISES SRF# OWXXXXXXXX845058 WT 250113-033290 AXIS BANK LIMITED /BNF=AKBARALI ENTERPRISES SRF# OWXXXXXXXX135254", 7000, None, "COGS"),
        (f"01/13/{y + 1}", "WT 250113-033290 AXIS BANK LIMITED /BNF=AKBARALI ENTERPRISES SRF# OWXXXXXXXX135254", 14000, None, "COGS"),
        (f"07/01/{y}", "Sandy Spring Ban Transfer 240701 ANUJ MITTAL ANUJ MITTAL", 5000, None, "Loan"),  # the bank's own repeat: stays
    ]
    p = _up(client, admin_headers, src["id"], _qb(rows), path="/api/accounting/import/preview")
    assert p["memos"] == 4
    r = _up(client, admin_headers, src["id"], _qb(rows))
    assert r["memos"] == 4
    got = {t["amount"]: t for t in _txns(api, all_dates="true", source_id=src["id"])["rows"]}
    m_t = got[-10000.0]
    assert m_t["description"].endswith("CC PAYMENT BUSINESS") and m_t["qb_memo"] == tail
    assert m_t["account_id"] is None                      # "SHARE" was only in QuickBooks' text: the rule doesn't file it
    assert got[-25000.0]["description"] == tail and got[-25000.0]["account_id"] == nir["id"]   # the real NIRAJ Share line
    assert got[-10.0]["description"] == "DIRECT PAY MONTHLY BASE"
    assert got[-7000.0]["description"].endswith("SRF# OWXXXXXXXX845058")
    assert got[-5000.0]["description"].endswith("ANUJ MITTAL ANUJ MITTAL") and not got[-5000.0]["qb_memo"]
    # a rule can still look at QuickBooks' text on purpose
    memo_rule = {"conditions": [c("memo", "contains", f"{who} SHARE"), c("description", "contains", "CC PAYMENT")], "sign": "out", "account_id": cc["id"]}
    prev = api.post("/api/accounting/rules/preview", json=memo_rule)
    assert [t["id"] for t in prev["groups"]["unfiled"]] == [m_t["id"]]
    api.delete(f"/api/accounting/rules/{rule}")


def test_qb_tails_on_lines_already_in_and_rule_filings_go_back(api, client, admin_headers):
    from app.config.database import SessionLocal
    from app.models import AcctTxn
    y = 2061
    src = _source(api)
    nir = api.post("/api/accounting/accounts", json={"name": uid("Nir "), "kind": "owner"})
    who = name("SHAHR")
    tail = f"WF Direct Pay-Payment- {who} Share-Tran ID DPBBBC7JZT"
    card = "CARDMEMBER SERV WEB PYMT 240110 ***********2738 PAYABLE,ACCOUNTS 12"
    _import(client, admin_headers, src["id"], "q.csv", _csv([(f"03/05/{y}", -25000.0, tail), (f"01/10/{y}", -215.0, card)]))
    rule = api.post("/api/accounting/rules", json={"conditions": [c("text", "contains", f"{who} SHARE")], "sign": "out", "account_id": nir["id"]})["id"]
    ids = {t["amount"]: t["id"] for t in _txns(api, all_dates="true", source_id=src["id"])["rows"]}
    db = SessionLocal()  # lines brought in before AT-HUB knew: QuickBooks' text on them, filed by the rule because of it
    t = db.get(AcctTxn, ids[-215.0])
    t.description, t.account_id, t.how, t.rule_id, t.reviewed = f"{card} {tail}", nir["id"], "rule", rule, False
    db.commit()
    db.close()
    found = {x["id"]: x for x in api.get("/api/accounting/qb-tails")}
    assert ids[-215.0] in found and ids[-25000.0] not in found
    f = found[ids[-215.0]]
    assert f["after"] == card and f["removed"] == tail and f["effect"]
    r = api.post("/api/accounting/qb-tails/apply", json={"ids": [ids[-215.0]]})
    assert r["cleaned"] == 1 and r["back_to_review"] == 1
    now = next(x for x in _txns(api, all_dates="true", source_id=src["id"])["rows"] if x["id"] == ids[-215.0])
    assert now["description"] == card and now["qb_memo"] == tail and now["account_id"] is None and now["sent_back"]
    api.post(f"/api/accounting/changes/{r['change_id']}/undo")
    now = next(x for x in _txns(api, all_dates="true", source_id=src["id"])["rows"] if x["id"] == ids[-215.0])
    assert now["account_id"] == nir["id"] and now["description"].endswith(tail)
    api.delete(f"/api/accounting/rules/{rule}")


def test_rules_disagree_review_flag_wins_and_still_reviewed(api, client, admin_headers):
    y = 2062
    src = _source(api)
    a1 = api.post("/api/accounting/accounts", json={"name": uid("Rich "), "kind": "owner"})
    a2 = api.post("/api/accounting/accounts", json={"name": uid("Nira "), "kind": "owner"})
    tag, other = name("MAALIKQ"), name("NIRAJQ")
    r_nir = api.post("/api/accounting/rules", json={"name": "zelle niraj", "conditions": [c("text", "contains", f"ZELLE {other}")], "sign": "out", "account_id": a2["id"]})["id"]
    r_maal = api.post("/api/accounting/rules", json={"name": "maaliks", "conditions": [c("text", "contains", tag)], "sign": "out", "account_id": a1["id"], "review": True})["id"]
    _import(client, admin_headers, src["id"], "z.csv", _csv([(f"05/01/{y}", -5000.0, f"ZELLE TO {other} ON 05/01 REF #RX1 {tag} SHARE"),
                                                             (f"05/02/{y}", -700.0, f"ZELLE TO SOMEONE ON 05/02 REF #RX2 {tag}"),
                                                             (f"05/03/{y}", -800.0, f"ZELLE TO {other} ON 05/03 REF #RX3 PHONE")]))
    got = {t["description"][-12:]: t for t in _txns(api, all_dates="true", source_id=src["id"])["rows"]}
    both = next(t for t in got.values() if t["amount"] == -5000)
    one = next(t for t in got.values() if t["amount"] == -700)
    assert both["flag"] == "conflict" and both["account_id"] is None
    assert one["account_id"] == a1["id"] and one["flag"] == "check"           # filed by the rule, still waits in Review
    review = {t["id"] for t in _txns(api, status="review", all_dates="true")["rows"]}
    assert {both["id"], one["id"]} <= review
    # the line's rules: both match, they disagree
    why = api.get(f"/api/accounting/txns/{both['id']}/rules")
    assert why["conflict"] and {x["id"] for x in why["rules"] if x["in_conflict"]} == {r_nir, r_maal}
    # while building a rule: the other rule shows up as an overlap
    ov = api.post("/api/accounting/rules/preview", json={"conditions": [c("text", "contains", tag)], "sign": "out", "account_id": a1["id"]})["overlaps"]
    assert any(o["id"] == r_nir for o in ov)
    # "looks right" takes the check off; the conflict line needs a pick
    assert api.post("/api/accounting/txns/clear-flag", json={"ids": [one["id"], both["id"]]})["done"] == 1
    # NOT: the Niraj rule ignores lines naming the other partner -> no conflict on the next one
    api.put(f"/api/accounting/rules/{r_nir}", json={"name": "zelle niraj", "conditions": [c("text", "contains", f"ZELLE {other}"), c("text", "not_contains", tag)],
                                                    "sign": "out", "account_id": a2["id"]})
    _import(client, admin_headers, src["id"], "z2.csv", _csv([(f"06/01/{y}", -5100.0, f"ZELLE TO {other} ON 06/01 REF #RX4 {tag} SHARE")]))
    nxt = next(t for t in _txns(api, all_dates="true", source_id=src["id"])["rows"] if t["amount"] == -5100)
    assert nxt["account_id"] == a1["id"] and nxt["flag"] == "check"
    for rid in (r_nir, r_maal):
        api.delete(f"/api/accounting/rules/{rid}")


def test_more_rule_blocks_groups_dates_weekday_pattern(api, client, admin_headers):
    y = 2063
    src = _source(api)
    rent = api.post("/api/accounting/accounts", json={"name": uid("Rent "), "kind": "expense"})
    tag = name("VARMAQ")
    group = {"field": "group", "match": "any", "conditions": [c("text", "contains", "RENT"), c("text", "contains", "WHOLESALE")]}
    rule = {"conditions": [c("text", "contains", tag), group, c("date", "between", [f"{y}-01-01", f"{y}-06-30"]),
                           c("description", "regex", r"REF #RP0\w+"), c("weekday", "not_any_of", "SUN")], "sign": "out", "account_id": rent["id"]}
    rid = api.post("/api/accounting/rules", json=rule)["id"]
    _import(client, admin_headers, src["id"], "r.csv", _csv([(f"03/01/{y}", -650.0, f"ZELLE TO {tag} ON 03/01 REF #RP0AA1 RENT MAR"),
                                                             (f"08/01/{y}", -650.0, f"ZELLE TO {tag} ON 08/01 REF #RP0AA2 RENT AUG"),
                                                             (f"03/02/{y}", -100.0, f"ZELLE TO {tag} ON 03/02 REF #RP0AA3 GAS")]))
    got = {t["amount"] * 0 + int(t["date"][5:7]) * 100 + int(t["date"][8:]): t for t in _txns(api, all_dates="true", source_id=src["id"])["rows"]}
    assert got[301]["account_id"] == rent["id"]        # in the dates, rent
    assert got[801]["account_id"] is None              # after the rule's dates
    assert got[302]["account_id"] is None              # neither rent nor wholesale
    bad = client.post("/api/accounting/rules", headers=admin_headers, json={"conditions": [c("text", "regex", "([")], "account_id": rent["id"]})
    assert bad.status_code == 400
    api.delete(f"/api/accounting/rules/{rid}")


def test_split_found_in_quickbooks_split_memory_and_start_fresh(api, client, admin_headers):
    y = 2064
    src = _source(api)
    nir = api.post("/api/accounting/accounts", json={"name": uid("Niraj "), "kind": "owner"})
    ric = api.post("/api/accounting/accounts", json={"name": uid("Richard "), "kind": "owner"})
    rows = [(f"04/09/{y}", "WT FED#04944 BANK OF AMERICA, N /FTR/BNF=Niraj Shah SRF# XXXXXXXXXXXX9526 TRN#XXXXXXXX2012 RFB#", 140731, None, None),
            (f"04/10/{y}", "CHART INDUSTRIES PAYMENTS 240410 2272887", None, 5000, "Sales")]
    p = _up(client, admin_headers, src["id"], _qb(rows), path="/api/accounting/import/preview")
    assert p["qb_splits"] == 1
    _up(client, admin_headers, src["id"], _qb(rows))
    big = next(t for t in _txns(api, all_dates="true", source_id=src["id"])["rows"] if t["amount"] == -140731)
    assert big["flag"] == "qb_split"
    check = api.get("/api/accounting/check?all_dates=true")
    assert any(f["check"] == "qb_split" and f["txn"]["id"] == big["id"] for f in check["findings"])
    # parts typed without the minus sign follow the line
    done = api.put(f"/api/accounting/txns/{big['id']}", json={"splits": [{"account_id": nir["id"], "amount": 71472}, {"account_id": ric["id"], "amount": 69259}], "learn": False})
    assert {(s["account_id"], s["amount"]) for s in done["splits"]} == {(nir["id"], -71472.0), (ric["id"], -69259.0)} and not done["flag"]
    mem = [m for m in api.get("/api/accounting/memory") if abs(m["amount"] + 140731) < 0.01]
    assert mem and mem[0]["kind"] == "split"
    # Start Fresh: Super admin only, typed confirmation, a backup first; the split comes back on the next import
    admin = _user(client, admin_headers, "admin")
    assert client.post("/api/accounting/start-fresh", headers=admin, json={"confirm": "START FRESH"}).status_code == 403
    assert client.post("/api/accounting/start-fresh", headers=admin_headers, json={"confirm": "yes"}).status_code == 400
    fr = api.post("/api/accounting/start-fresh", json={"confirm": "START FRESH"})
    assert fr["lines"] >= 2 and fr["backup"] and fr["remembered_splits"] >= 1
    assert _txns(api, all_dates="true")["total"] == 0
    assert api.get("/api/accounting/accounts-summary")  # accounts stay
    back = _up(client, admin_headers, src["id"], _qb(rows))
    assert back["remembered"] == 1
    again = next(t for t in _txns(api, all_dates="true", source_id=src["id"])["rows"] if t["amount"] == -140731)
    assert {(s["account_id"], s["amount"]) for s in again["splits"]} == {(nir["id"], -71472.0), (ric["id"], -69259.0)}
    assert not again["flag"] and again["how"] == "person"


def test_undo_import_is_super_admin_only(api, client, admin_headers):
    src = _source(api)
    r = _import(client, admin_headers, src["id"], "u.csv", _csv([("01/02/2065", -10.0, f"FEE {name('UNDOQ')}")]))
    admin = _user(client, admin_headers, "admin")
    assert client.delete(f"/api/accounting/imports/{r['import_id']}", headers=admin).status_code == 403
    assert client.delete(f"/api/accounting/imports/{r['import_id']}", headers=admin_headers).status_code == 200


def test_undo_any_change_and_refuse_when_changed_since(api, client, admin_headers):
    y = 2066
    src = _source(api)
    labor = api.post("/api/accounting/accounts", json={"name": uid("Labor "), "kind": "expense"})
    other = api.post("/api/accounting/accounts", json={"name": uid("Other "), "kind": "expense"})
    tag = name("UNDOPEDRO")
    _import(client, admin_headers, src["id"], "w.csv", _csv([(f"07/0{d}/{y}", -50.0 - d, f"ZELLE TO {tag} ON 07/0{d} REF #U{d}") for d in range(1, 6)]))
    got = sorted(_txns(api, all_dates="true", source_id=src["id"])["rows"], key=lambda t: t["date"])
    api.put(f"/api/accounting/txns/{got[0]['id']}", json={"account_id": labor["id"]})
    second = api.put(f"/api/accounting/txns/{got[1]['id']}", json={"account_id": labor["id"]})
    assert second["filed_more"] >= 3 and second["change_id"]
    api.post(f"/api/accounting/changes/{second['change_id']}/undo")
    now = {t["id"]: t for t in _txns(api, all_dates="true", source_id=src["id"])["rows"]}
    assert now[got[1]["id"]]["account_id"] is None and all(now[t["id"]]["account_id"] is None for t in got[2:])
    assert now[got[0]["id"]]["account_id"] == labor["id"]     # the change before it stays
    # bulk move, then a later change to one of those lines: the bulk can't be undone until the later one is
    b = api.post("/api/accounting/txns/bulk", json={"ids": [t["id"] for t in got[2:]], "action": "file", "account_id": other["id"]})
    later = api.put(f"/api/accounting/txns/{got[2]['id']}", json={"account_id": labor["id"], "learn": False})
    assert client.post(f"/api/accounting/changes/{b['change_id']}/undo", headers=admin_headers).status_code == 409
    api.post(f"/api/accounting/changes/{later['change_id']}/undo")
    api.post(f"/api/accounting/changes/{b['change_id']}/undo")
    assert all(t["account_id"] is None for t in _txns(api, all_dates="true", source_id=src["id"])["rows"] if t["id"] in {x["id"] for x in got[2:]})
    # a merge comes back: the account and its lines
    api.put(f"/api/accounting/txns/{got[4]['id']}", json={"account_id": other["id"], "learn": False})
    m = api.post(f"/api/accounting/accounts/{other['id']}/merge?into_id={labor['id']}")
    api.post(f"/api/accounting/changes/{m['change_id']}/undo")
    assert next(t for t in _txns(api, all_dates="true", source_id=src["id"])["rows"] if t["id"] == got[4]["id"])["account_id"] == other["id"]
    assert any(x["id"] == m["change_id"] and x["undone_at"] for x in api.get("/api/accounting/changes"))


def test_reconcile_split_amount_differs_decisions_and_pairs(api, client, admin_headers):
    y = 2067
    src = _source(api)
    nir = api.post("/api/accounting/accounts", json={"name": uid("NirajR "), "kind": "owner"})
    ric = api.post("/api/accounting/accounts", json={"name": uid("RichR "), "kind": "owner"})
    _import(client, admin_headers, src["id"], "b.csv", _csv([
        (f"04/09/{y}", -140731.0, "WT FED#04944 BANK OF AMERICA /BNF=Niraj Shah TRN#240409122012"),
        (f"04/15/{y}", -1545.79, "ZELLE TO SHAH NIRAJ ON 04/15 REF #RP0QQ11111 NIRAJ SHARE"),
        (f"04/20/{y}", -900.0, "ZELLE TO SHAH NIRAJ ON 04/20 REF #RP0QQ22222")]))
    lines = {t["amount"]: t for t in _txns(api, all_dates="true", source_id=src["id"])["rows"]}
    api.put(f"/api/accounting/txns/{lines[-900.0]['id']}", json={"account_id": nir["id"], "learn": False})
    qb = [{"date": f"{y}-04-09", "amount": -71472.0, "name": "", "memo": "NIRAJ SHARE", "account": "Partner distributions:Niraj Distributions"},
          {"date": f"{y}-04-09", "amount": -69259.0, "name": "", "memo": "MAALIKS SHARE", "account": "Partner distributions:Maaliks Distributions"},
          {"date": f"{y}-04-15", "amount": -325.0, "name": "", "memo": "ZELLE TO SHAH NIRAJ ON 04/15 REF #RP0QQ11111", "account": "Partner distributions:Niraj Distributions"},
          {"date": f"{y}-04-20", "amount": -900.0, "name": "", "memo": "ZELLE TO SHAH NIRAJ ON 04/20 REF #RP0QQ22222", "account": "Partner distributions:Niraj Distributions"}]
    mapping = {"Partner distributions:Niraj Distributions": nir["id"], "Partner distributions:Maaliks Distributions": ric["id"]}
    body = {"account_ids": [nir["id"], ric["id"]], "start": f"{y}-04-01", "end": f"{y}-04-30", "rows": qb, "mapping": mapping, "save": False}
    res = api.post("/api/accounting/reconcile/run", json=body)
    assert res["counts"]["split"] == 1 and res["split"][0]["can_split"]
    assert res["counts"]["amount_differs"] == 1 and res["amount_differs"][0]["difference"] == -1220.79
    assert res["counts"]["same"] == 1 and res["same"][0]["reason"] == "same bank reference" and res["same"][0]["confidence"] == "high"
    assert res["months"] and res["mapping"]
    sp = res["split"][0]
    api.put(f"/api/accounting/txns/{sp['athub']['id']}", json={"splits": [{"account_id": p["mapped_account_id"], "amount": p["amount"]} for p in sp["parts"]], "learn": False})
    res = api.post("/api/accounting/reconcile/run", json=body)
    assert res["counts"]["same"] == 3                      # a split line is matched part by part now
    # "AT-HUB is right" on the amount-differs one: it's left alone next time; undecide brings it back
    key = res["amount_differs"][0]["key"]
    api.post("/api/accounting/reconcile/decide", json={"items": [{"key": key, "decision": "athub_right"}]})
    res = api.post("/api/accounting/reconcile/run", json=body)
    assert res["counts"]["amount_differs"] == 0 and res["counts"]["decided"] == 1
    api.post("/api/accounting/reconcile/decide", json={"items": [{"key": key, "decision": None}]})
    x = client.post("/api/accounting/reconcile/export.xlsx", headers=admin_headers, json=body)
    assert x.status_code == 200 and x.content[:2] == b"PK"


def test_combine_rules_health_and_workbook_split_cell(api, client, admin_headers):
    labor = api.post("/api/accounting/accounts", json={"name": uid("LaborC "), "kind": "expense"})
    t1, t2 = name("GOMEZC"), name("SAULC")
    ids = [api.post("/api/accounting/rules", json={"conditions": [c("text", "contains", w)], "sign": "out", "account_id": labor["id"]})["id"] for w in (t1, t2)]
    src = _source(api)
    _import(client, admin_headers, src["id"], "g.csv", _csv([("02/01/2068", -50.0, f"ZELLE TO {t1}"), ("02/02/2068", -60.0, f"ZELLE TO {t2}")]))
    sug = next(g for g in api.get("/api/accounting/rule-combine") if set(g["rule_ids"]) == set(ids))
    assert sug["same_lines"] and sug["lines_before"] == sug["lines_after"] == 2
    r = api.post("/api/accounting/rule-combine", json={"rule_ids": ids, "name": "Saul all"})
    rules = {x["id"]: x for x in api.get("/api/accounting/rules")}
    assert r["id"] in rules and not set(ids) & set(rules)
    assert any(h["id"] == r["id"] for h in api.get("/api/accounting/rule-health"))
    api.post(f"/api/accounting/changes/{r['change_id']}/undo")
    rules = {x["id"] for x in api.get("/api/accounting/rules")}
    assert set(ids) <= rules and r["id"] not in rules
    for i in ids:
        api.delete(f"/api/accounting/rules/{i}")
    # a workbook Account cell with two accounts and amounts: one line, split
    wsrc = _source(api)
    who1, who2 = name("NIRW"), name("RICHW")
    data = _xlsx([(datetime(2069, 4, 9), -140731, "WT FED BANK OF AMERICA NIRAJ", f"{who1} 71472; {who2} 69259", "Shares")])
    _import(client, admin_headers, wsrc["id"], "wb.xlsx", data)
    t = _txns(api, all_dates="true", source_id=wsrc["id"])["rows"][0]
    assert sorted(s["amount"] for s in t["splits"]) == [-71472.0, -69259.0]


def test_a_split_line_counts_only_its_part_in_each_partners_account(api, client, admin_headers):
    """Transactions filtered to one partner: the totals, groups and Excel count that partner's part of a split line, not
    the whole line (the reports -- P&L, Accounts, Partner Accounts -- always did)."""
    src = _source(api)
    n_ = api.post("/api/accounting/accounts", json={"name": uid("NirajP "), "kind": "owner"})
    r_ = api.post("/api/accounting/accounts", json={"name": uid("RichP "), "kind": "owner"})
    _import(client, admin_headers, src["id"], "p.csv", _csv([("04/09/2070", -140731.0, f"WT FED BANK OF AMERICA {name('SPLITP')}"),
                                                             ("04/10/2070", -1000.0, f"ZELLE TO {name('NIRP')}")]))
    big, small = sorted(_txns(api, all_dates="true", source_id=src["id"])["rows"], key=lambda t: t["amount"])
    api.put(f"/api/accounting/txns/{big['id']}", json={"splits": [{"account_id": n_["id"], "amount": -71472}, {"account_id": r_["id"], "amount": -69259}], "learn": False})
    api.put(f"/api/accounting/txns/{small['id']}", json={"account_id": n_["id"], "learn": False})
    niraj = _txns(api, all_dates="true", account_id=n_["id"])
    assert niraj["net"] == -72472.0 and niraj["total"] == 2
    assert next(t for t in niraj["rows"] if t["id"] == big["id"])["in_account"] == -71472.0
    rich = _txns(api, all_dates="true", account_id=r_["id"], group="account")
    assert rich["net"] == -69259.0 and [g["net"] for g in rich["groups"]] == [-69259.0]
    allg = {g["key"]: g["net"] for g in _txns(api, all_dates="true", source_id=src["id"], group="account")["groups"]}
    assert sorted(allg.values()) == [-72472.0, -69259.0]
    summ = {a["id"]: a["net"] for a in api.get("/api/accounting/accounts-summary?start=2070-01-01&end=2070-12-31")}
    assert summ[n_["id"]] == -72472.0 and summ[r_["id"]] == -69259.0
