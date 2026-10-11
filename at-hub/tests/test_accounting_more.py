"""Accounting, the rest: bank-style search + totals / groups / stats, send back to Review, the Ignored list (exact repeats,
possible duplicates, restore), Check My Books, Partner Accounts, Payees, 1099, Recurring, comments, scratch sheets and
reconciling against QuickBooks. Made-up data only; each test has its own far-off year."""
from urllib.parse import urlencode

from tests.builders import uid
from tests.test_accounting import _csv, _import, _source, _txns
from tests.test_accounting_rules import name


def test_parse_q_bank_style():
    from app.services.acct_more import _amount_ok, parse_q
    q = parse_q('saul >500 "gomez saul" 1043')
    assert q["words"] == ["saul"] and q["phrases"] == ["gomez saul"] and q["amount"] == [("gt", 500.0)] and q["loose_num"] == ["1043"]
    assert parse_q("$1,072.50")["amount"] == [("eq", (1072.5, True))]
    assert parse_q("500-1000")["amount"] == [("between", (500.0, 1000.0))]
    assert _amount_ok(("signed", (-1, 300.0, False)), -300.4) and not _amount_ok(("signed", (-1, 300.0, False)), 300.0)
    assert _amount_ok(("eq", (1072.0, False)), -1072.5) and not _amount_ok(("eq", (1072.5, True)), -1072.49)


def test_search_totals_groups_send_back(api, client, admin_headers):
    y = 2045
    src = _source(api)
    a = api.post("/api/accounting/accounts", json={"name": uid("Labor "), "kind": "expense"})
    p1, p2 = name("SAULX"), name("RENTX")
    _import(client, admin_headers, src["id"], "s.csv", _csv([
        (f"01/05/{y}", -300.0, f"ZELLE TO {p1} ON 01/05 REF #A1"), (f"02/05/{y}", -320.5, f"ZELLE TO {p1} ON 02/05 REF #A2"),
        (f"01/10/{y}", -1072.5, f"CHECK 1043 {p2}"), (f"02/10/{y}", 250.0, f"DEPOSIT FROM {p2}")]))
    base = {"source_id": src["id"], "start": f"{y}-01-01", "end": f"{y}-12-31"}
    get = lambda **kw: api.get(f"/api/accounting/txns?{urlencode({**base, **kw})}")
    assert get(q=p1.lower())["total"] == 2
    assert get(q="1072.50")["total"] == 1 and get(q="1043")["total"] == 1
    assert get(q=">1000")["total"] == 1 and get(q="300-400")["total"] == 2 and get(q="-300")["total"] == 1 and get(q="+250")["total"] == 1
    assert get(amin="300", amax="400", sign="out")["total"] == 2
    r = get(group="month", sort="amount_desc")
    assert r["count"] == 4 and r["money_in"] == 250 and r["money_out"] == -1693.0 and r["net"] == -1443.0
    assert [g["key"] for g in r["groups"]] == [f"{y}-01", f"{y}-02"] and r["rows"][0]["amount"] == -1072.5
    assert r["stats"]["largest_out"] == -1072.5 and r["stats"]["months"] == 2
    x = client.get(f"/api/accounting/txns/export.xlsx?{urlencode({**base, 'q': p1})}", headers=admin_headers)
    assert x.status_code == 200 and x.content[:2] == b"PK"
    # send back to Review (old account kept as the suggestion), undo
    line = get(q=p1)["rows"][0]
    api.put(f"/api/accounting/txns/{line['id']}", json={"account_id": a["id"], "learn": False})
    assert api.post("/api/accounting/txns/send-back", json={"ids": [line["id"]], "reason": "not sure"})["done"] == 1
    back = get(status="sent_back")["rows"][0]
    assert back["account_id"] is None and back["sent_back_reason"] == "not sure" and back["suggest"]["account_id"] == a["id"]
    assert api.post("/api/accounting/txns/undo-send-back", json={"ids": [line["id"]]})["done"] == 1
    assert get(q=p1, status="sent_back")["total"] == 0


def test_ignored_exact_possible_restore(api, client, admin_headers):
    y = 2046
    src = _source(api)
    p = name("WIDGETX")
    first = _import(client, admin_headers, src["id"], "a.csv", _csv([(f"03/01/{y}", -99.0, f"ACH {p} INVOICE 7"), (f"03/02/{y}", -15.0, f"FEE {p}")]))
    assert first["added"] == 2
    # the same file again + a near-duplicate (the bank's wording changed a little)
    again = _import(client, admin_headers, src["id"], "b.csv", _csv([(f"03/01/{y}", -99.0, f"ACH {p} INVOICE 7"), (f"03/03/{y}", -15.0, f"FEE {p}  ")]))
    assert again["added"] == 0 and again["skipped"] == 1 and again["possible"] == 1
    ign = api.get(f"/api/accounting/ignored?import_id={again['import_id']}")
    possible = next(r for r in ign["rows"] if r["status"] == "possible")
    assert possible["dup_of"]["amount"] == -15.0 and any(r["reason"] == "exact" for r in ign["rows"])
    assert api.post("/api/accounting/ignored/decide", json={"ids": [possible["id"]], "action": "keep"})["done"] == 1
    assert _txns(api, start=f"{y}-03-01", end=f"{y}-03-31", source_id=src["id"])["total"] == 3
    exact = next(r for r in ign["rows"] if r["reason"] == "exact")
    api.post("/api/accounting/ignored/decide", json={"ids": [exact["id"]], "action": "ignore"})  # already ignored: nothing
    assert api.get(f"/api/accounting/ignored?import_id={again['import_id']}&status=restored")["restored"] == 1


def test_check_my_books_partner_accounts_payees_1099_recurring(api, client, admin_headers):
    y = 2047
    src = _source(api)
    ali = api.post("/api/accounting/accounts", json={"name": uid("Ali "), "kind": "cogs"})
    transpo = api.post("/api/accounting/accounts", json={"name": uid("Transpo "), "kind": "expense"})
    owner = api.post("/api/accounting/accounts", json={"name": uid("Partner "), "kind": "owner", "partner": "Anuj"})
    loan = api.post("/api/accounting/accounts", json={"name": uid("Anuj Loan "), "kind": "loan", "partner": "Anuj"})
    p = name("AKBARALIX")
    rows = [(f"{m:02d}/05/{y}", -1000.0 - m, f"WT AXIS /BNF={p} SRF# {m}") for m in range(1, 7)]
    rows += [(f"07/05/{y}", -50000.0, f"WT AXIS /BNF={p} SRF# 99")]
    rows += [(f"08/01/{y}", -2000.0, "ZELLE TO ANUJ OWNER SHARE"), (f"08/02/{y}", 5000.0, "DEPOSIT FROM ANUJ CAPITAL"), (f"08/03/{y}", 3000.0, "LOAN FROM ANUJ")]
    r = _import(client, admin_headers, src["id"], "c.csv", _csv(rows))
    got = sorted(_txns(api, start=f"{y}-01-01", end=f"{y}-12-31", source_id=src["id"], limit=50)["rows"], key=lambda t: t["date"])
    for t in got[:6]:
        api.put(f"/api/accounting/txns/{t['id']}", json={"account_id": ali["id"], "learn": False})
    api.put(f"/api/accounting/txns/{got[6]['id']}", json={"account_id": transpo["id"], "learn": False})   # the odd one out, and unusual
    api.put(f"/api/accounting/txns/{got[7]['id']}", json={"account_id": owner["id"], "learn": False})
    api.put(f"/api/accounting/txns/{got[8]['id']}", json={"account_id": owner["id"], "learn": False})
    api.put(f"/api/accounting/txns/{got[9]['id']}", json={"account_id": loan["id"], "learn": False})
    chk = api.get(f"/api/accounting/check?start={y}-01-01&end={y}-12-31")
    odd = [f for f in chk["findings"] if f["check"] == "odd_one_out" and f["txn"]["id"] == got[6]["id"]]
    assert odd and odd[0]["suggest"]["account_id"] == ali["id"]
    api.post("/api/accounting/check/ok", json={"txn_id": got[6]["id"], "check": "odd_one_out"})
    assert not [f for f in api.get(f"/api/accounting/check?start={y}-01-01&end={y}-12-31")["findings"] if f["key"] == f"odd_one_out:{got[6]['id']}"]
    pa = api.get(f"/api/accounting/partner-accounts?start={y}-01-01&end={y}-12-31")
    anuj = next(x for x in pa["partners"] if x["name"] == "Anuj")
    assert anuj["period"]["withdrawn"] <= -2000 and anuj["period"]["invested"] >= 5000 and anuj["period"]["lent"] >= 3000
    assert any(m["month"] == f"{y}-08" for m in anuj["by_month"])
    pays = api.get(f"/api/accounting/payees?start={y}-01-01&end={y}-12-31")
    assert any(p.title() in x["payee"].upper().title() or p in x["payee"].upper() for x in pays)
    tax = api.get(f"/api/accounting/tax1099?year={y}")
    row = next(x for x in tax["rows"] if p in x["payee"].upper())
    assert row["total"] >= 50000
    api.post("/api/accounting/tax1099/mark", json={"year": y, "payee": row["payee"], "mark": "no"})
    assert next(x for x in api.get(f"/api/accounting/tax1099?year={y}")["rows"] if x["payee"] == row["payee"])["mark"] == "no"
    assert isinstance(api.get("/api/accounting/recurring"), list)


def test_comments_sheets_and_reconcile_with_quickbooks(api, client, admin_headers):
    y = 2048
    c = api.post("/api/accounting/comments", json={"section": "loans", "period_key": f"{y}-04-01..{y + 1}-03-31", "text": "Sandy paid off"})
    assert any(x["id"] == c["id"] for x in api.get(f"/api/accounting/comments?section=loans&period_key={y}-04-01..{y + 1}-03-31"))
    api.put(f"/api/accounting/comments/{c['id']}", json={"section": "loans", "text": "Sandy paid off in Feb"})
    api.delete(f"/api/accounting/comments/{c['id']}")
    sh = api.post("/api/accounting/sheets", json={"kind": "projection", "name": "What if", "doc": {"rows": [{"group": "Cash", "name": "WF", "a": "61000"},
                                                                                                              {"group": "AR", "name": "Hudson", "a": "=R1*2"}]}})
    api.put(f"/api/accounting/sheets/{sh['id']}", json={"doc": {"rows": [{"group": "Cash", "name": "WF", "a": "70000"}]}})
    x = client.get(f"/api/accounting/sheets/{sh['id']}/export.xlsx", headers=admin_headers)
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert "loans" in api.get("/api/accounting/sheets-books")
    mine = api.post("/api/accounting/sheets", json={"kind": "loancalc", "doc": {"rows": []}})
    assert any(s["id"] == mine["id"] for s in api.get("/api/accounting/sheets?kind=loancalc"))
    # reconcile: AT-HUB partner lines vs QuickBooks' Transaction Detail by Account
    src = _source(api)
    anuj = api.post("/api/accounting/accounts", json={"name": name("Anuj"), "kind": "owner"})
    niraj = api.post("/api/accounting/accounts", json={"name": name("Niraj"), "kind": "owner"})
    _import(client, admin_headers, src["id"], "r.csv", _csv([(f"05/01/{y}", -1000.0, "ZELLE TO A ONE"), (f"05/02/{y}", -2000.0, "ZELLE TO B TWO"),
                                                             (f"05/03/{y}", -3000.0, "ZELLE TO C THREE")]))
    ls = sorted(_txns(api, start=f"{y}-05-01", end=f"{y}-05-31", source_id=src["id"])["rows"], key=lambda t: t["date"])
    api.put(f"/api/accounting/txns/{ls[0]['id']}", json={"account_id": anuj["id"], "learn": False})
    api.put(f"/api/accounting/txns/{ls[1]['id']}", json={"account_id": anuj["id"], "learn": False})   # QuickBooks says Niraj
    api.put(f"/api/accounting/txns/{ls[2]['id']}", json={"account_id": niraj["id"], "learn": False})
    qb = ("Transaction Detail by Account\n\nDate\tTransaction Type\tNum\tName\tMemo/Description\tAmount\n"
          f"Partner Distributions:{anuj['name']}\n05/01/{y}\tExpense\t\tA\tone\t1000.00\nTotal for {anuj['name']}\t\t\t\t\t1000.00\n"
          f"Partner Distributions:{niraj['name']}\n05/02/{y}\tExpense\t\tB\ttwo\t2000.00\n05/03/{y}\tExpense\t\tC\tthree\t3000.00\n"
          f"05/20/{y}\tExpense\t\tD\tfour\t444.00\n")
    parsed = client.post("/api/accounting/reconcile/parse", headers=admin_headers, data={"text": qb}).json()
    assert len(parsed["rows"]) == 4 and parsed["rows"][0]["account"].endswith(anuj["name"])
    res = api.post("/api/accounting/reconcile/run", json={"account_ids": [anuj["id"], niraj["id"]], "start": f"{y}-04-01", "end": f"{y + 1}-03-31",
                                                         "rows": parsed["rows"]})
    assert res["counts"]["same"] == 2 and res["counts"]["different"] == 1 and res["counts"]["qb_only"] == 1
    diff = res["different"][0]
    assert diff["athub"]["account_id"] == anuj["id"] and diff["mapped_account_id"] == niraj["id"]
    tot = {t["account_id"]: t for t in res["totals"]}
    assert tot[anuj["id"]]["athub_abs"] == 3000 and tot[anuj["id"]]["qb_abs"] == 1000
    assert any(h["id"] == res["id"] for h in api.get("/api/accounting/reconcile/history"))
