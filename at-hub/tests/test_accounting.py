"""Accounting: the user's categorized workbook in (accounts made, lines filed, year-end AP / AR as book entries), a bank
statement in the Wells Fargo CSV layout (no header) -- duplicates skipped, look-alikes filed by themselves, the rest
suggested; a person's pick files the look-alikes; typed-in lines matched (not doubled) when the statement brings them;
splits; P&L / partners / loans; AP / AR from AT-HUB with a person's word on top; undo; exports; money permission.
All made up here -- no real bank data in the repo. Each test uses its own far-off fiscal year so they don't mix."""
import io
from datetime import datetime

from openpyxl import Workbook

from tests.builders import uid


def _xlsx(rows, head=("Date ", "Amount", "Description", "Account", "Category")) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "WF 0424-0325"
    ws.append(list(head))
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _csv(rows) -> bytes:  # Wells Fargo: "date","amount","*","","description" -- no header
    return "\n".join(f'"{d}","{a:.2f}","*","","{desc}"' for d, a, desc in rows).encode()


def _source(api, name=None, kind="bank"):
    return api.post("/api/accounting/sources", json={"name": name or uid("Bank "), "kind": kind})


def _import(client, h, source_id, name, data, layout=None):
    form = {"source_id": str(source_id)}
    if layout:
        import json
        form["layout"] = json.dumps(layout)
    r = client.post("/api/accounting/import", headers=h, data=form, files={"file": (name, io.BytesIO(data), "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()


def _txns(api, **q):
    from urllib.parse import urlencode
    return api.get(f"/api/accounting/txns?{urlencode(q)}")


def test_workbook_then_statement_learns_and_files(api, client, admin_headers):
    y = 2031  # FY2031-32 (April - March)
    zelle = "ZELLE TO SHAH NIRAJ ON 0{m}/1{d} REF #RP0Y{m}{d}QQ{tail}"
    wb_rows = [
        (datetime(y, 4, 5), 50000, "Wells Fargo Bank EDI PYMNTS 13999 American Traders LLC", "chart", "Payments"),
        (datetime(y, 4, 9), -7000, "WT 31 AXIS BANK LIMITED /BNF=AKBARALI ENTERPRISES SRF# OW1 TRN#1", "ali", "COGS"),
        (datetime(y, 5, 9), -9000, "WT 32 AXIS BANK LIMITED /BNF=AKBARALI ENTERPRISES SRF# OW2 TRN#2", "ali", "COGS"),
        (datetime(y, 4, 9), -25, "WIRE TRANS SVC CHARGE - SEQUENCE: 31 SRF# OW1", "bank fee", "Expense"),
        (datetime(y, 5, 9), -25, "WIRE TRANS SVC CHARGE - SEQUENCE: 32 SRF# OW2", "bank fee", "Expense"),
        (datetime(y, 4, 20), -1000, zelle.format(m=4, d=1, tail=""), "niraj", "Shares"),
        (datetime(y, 5, 20), -1500, zelle.format(m=5, d=2, tail=""), "niraj", "Shares"),
        (datetime(y, 6, 20), -300, zelle.format(m=6, d=3, tail=" MAALIKS"), "richard", "Shares"),
        (datetime(y, 7, 20), -400, zelle.format(m=7, d=4, tail=" FOR MAALIKS"), "richard", "Shares"),
        (datetime(y, 6, 1), 20000, "Sandy Spring Ban Transfer 310601 ANUJ MITTAL", "sandy", "Loans"),
        (datetime(y, 9, 1), -5000, "ONLINE TRANSFER TO SANDY SPRING BANK CHK XX LOAN PAYMENT", "sandy", "Loans"),
        (datetime(y + 1, 3, 20), 12000, "Unpaid invoices at year end", "AR - Unpaid", "Pending"),
        (datetime(y + 1, 3, 20), -4000, "Superior at year end", "AP-Superior", "Pending"),
        (None, 999, "To be added", "interest sandy", "Loans"),  # no date: left out, reported
    ]
    src = _source(api, uid("WF Checking "))
    prev = client.post("/api/accounting/import/preview", headers=admin_headers, data={"source_id": str(src["id"])},
                       files={"file": ("FY_Input.xlsx", io.BytesIO(_xlsx(wb_rows)), "application/octet-stream")}).json()
    assert prev["workbook"] and prev["rows"] == 13 and prev["bad_count"] == 1
    r = _import(client, admin_headers, src["id"], "FY_Input.xlsx", _xlsx(wb_rows))
    assert r["added"] == 13 and r["left"] == 0

    meta = api.get("/api/accounting/meta")
    kinds = {a["name"]: a["kind"] for a in meta["accounts"]}
    assert kinds["Chart"] == "income" and kinds["Ali"] == "cogs" and kinds["Bank Fee"] == "expense" and kinds["Sandy"] == "loan"
    assert kinds["Niraj"] == "owner" and kinds["AR - Unpaid"] == "pending"
    book = next(s for s in meta["sources"] if s["kind"] == "book")
    pend = _txns(api, start=f"{y}-04-01", end=f"{y + 1}-03-31", kind="pending")["rows"]
    assert {t["source_id"] for t in pend} == {book["id"]}  # year-end AP / AR aren't bank lines

    # the statement for the next months: same payees again
    stmt = [(f"10/0{i}/{y}", a, d) for i, (a, d) in enumerate([
        (-8000, "WT 41 AXIS BANK LIMITED /BNF=AKBARALI ENTERPRISES SRF# OW9 TRN#9"),       # wire to Ali: sure
        (-25, "WIRE TRANS SVC CHARGE - SEQUENCE: 41 SRF# OW9"),                            # fee: sure
        (-700, "ZELLE TO SHAH NIRAJ ON 10/03 REF #RP0ZZZ1 FOR MAALIKS"),                   # note says Richard: sure
        (-650, "ZELLE TO SHAH NIRAJ ON 10/04 REF #RP0ZZZ2 KEGS FOR PACKING"),              # new note: a guess at most
        (-99, "CUBEWORK RENTAL XXXXX2502 American Traders LLC"),                           # never seen: to file
        (-99, "CUBEWORK RENTAL XXXXX2502 American Traders LLC"),                           # same again same day: 2 lines
    ], start=1)]
    src2 = src["id"]
    r = _import(client, admin_headers, src2, "WF.csv", _csv(stmt))
    assert r["added"] == 6 and r["auto"] == 3 and r["left"] == 3, r
    again = _import(client, admin_headers, src2, "WF.csv", _csv(stmt))
    assert again["added"] == 0 and again["skipped"] == 6  # the same file twice is safe

    got = _txns(api, start=f"{y}-10-01", end=f"{y}-10-31", source_id=src2)["rows"]
    line = lambda bit: next(t for t in got if bit in t["description"])
    ali = line("AXIS BANK")
    assert ali["account"] == "Ali" and ali["how"] == "auto" and not ali["reviewed"]
    assert line("RP0ZZZ1")["account"] == "Richard"
    kegs = line("RP0ZZZ2")
    assert kegs["account"] is None
    cube = [t for t in _txns(api, start=f"{y}-10-01", end=f"{y}-10-31", source_id=src2, status="unfiled")["rows"] if "CUBEWORK" in t["description"]]
    assert len(cube) == 2

    # a person files one Cubework line in a new account: the other one follows
    rent = api.post("/api/accounting/accounts", json={"name": uid("Rent Cube "), "kind": "expense"})
    out = api.put(f"/api/accounting/txns/{cube[0]['id']}", json={"account_id": rent["id"]})
    assert out["account"] == rent["name"] and out["how"] == "person"
    assert out["filed_more"] >= 1
    assert all(t["account_id"] == rent["id"] for t in _txns(api, start=f"{y}-10-01", end=f"{y}-10-31", source_id=src2, q="CUBEWORK")["rows"])

    # split the kegs Zelle: part expense, part salary
    sal = api.post("/api/accounting/accounts", json={"name": uid("Salary "), "kind": "expense"})
    bad = client.put(f"/api/accounting/txns/{kegs['id']}", headers=admin_headers, json={"splits": [{"account_id": rent["id"], "amount": -100}]})
    assert bad.status_code == 400 and "add up" in bad.json()["detail"]
    api.put(f"/api/accounting/txns/{kegs['id']}", json={"splits": [{"account_id": rent["id"], "amount": -150}, {"account_id": sal["id"], "amount": -500, "note": "salary"}]})
    # a statement line's amount can't be changed; a note can
    assert client.put(f"/api/accounting/txns/{kegs['id']}", headers=admin_headers, json={"amount": -1}).status_code == 400
    api.put(f"/api/accounting/txns/{kegs['id']}", json={"note": "half salary"})

    # P&L for the year: income - COGS - expenses; draws, loans below the line
    p = api.get(f"/api/accounting/pnl?start={y}-04-01&end={y + 1}-03-31&by=quarter")
    sec = {s["kind"]: s for s in p["sections"]}
    assert sec["income"]["total"] == 50000 and sec["cogs"]["total"] == -24000
    assert p["profit_total"] == round(50000 - 24000 - 75 - 99 * 2 - 650, 2)
    assert p["columns"] == [f"FY{y}-{str(y + 1)[2:]} Q{i}" for i in (1, 2, 3, 4)]
    assert sec["owner"]["total"] == -(1000 + 1500 + 300 + 400 + 700) and sec["loan"]["total"] == 15000

    # partners: equal thirds of (income + cogs + expenses + loans + year-end AR/AP + bank balance counted)
    api.put("/api/accounting/settings", json={"year": f"FY{y}-{str(y + 1)[2:]}", "bank_balance": 1000, "adjust": {"Anuj": 50}})
    pt = api.get(f"/api/accounting/partners?start={y}-04-01&end={y + 1}-03-31")
    total = 50000 - 24000 - 75 - 198 - 650 - 15000 + (12000 - 4000) + 1000  # net borrowed 15000 is taken off
    assert pt["total"] == total and pt["apar"] is None
    by = {x["name"]: x for x in pt["partners"]}
    assert by["Niraj"]["taken"] == -2500 and by["Richard"]["taken"] == -1400
    assert abs(by["Anuj"]["remaining"] - (total / 3 + 50)) < 0.02

    loans = {x["name"]: x for x in api.get(f"/api/accounting/loans?end={y + 1}-03-31")}
    assert loans["Sandy"]["borrowed"] >= 20000 and loans["Sandy"]["owed"] == loans["Sandy"]["borrowed"] + loans["Sandy"]["repaid"]

    x = client.get(f"/api/accounting/export.xlsx?start={y}-04-01&end={y + 1}-03-31", headers=admin_headers)
    assert x.status_code == 200 and x.content[:2] == b"PK"
    q = client.get(f"/api/accounting/export/quickbooks.csv?source_id={src2}&start={y}-10-01&end={y}-10-31", headers=admin_headers)
    assert q.status_code == 200 and q.text.startswith("Date,Description,Amount,Account") and "AKBARALI" in q.text


def test_typed_in_line_is_matched_not_doubled_and_undo(api, client, admin_headers):
    y = 2035
    src = _source(api)
    acc = api.post("/api/accounting/accounts", json={"name": uid("Ziegler "), "kind": "cogs"})
    t = api.post("/api/accounting/txns", json={"source_id": src["id"], "date": f"{y}-05-02", "amount": -4321.5, "description": "Check 1043 to Ziegler",
                                              "account_id": acc["id"], "expected": True})
    assert t["expected"] and t["origin"] == "manual" and t["account_id"] == acc["id"]
    r = _import(client, admin_headers, src["id"], "wf.csv", _csv([(f"05/06/{y}", -4321.5, "CHECK # 1043"), (f"05/07/{y}", 100.0, "DEPOSIT")]))
    assert r["matched"] == 1 and r["added"] == 1
    rows = _txns(api, start=f"{y}-05-01", end=f"{y}-05-31", source_id=src["id"])["rows"]
    assert len(rows) == 2
    m = next(x for x in rows if x["id"] == t["id"])
    assert not m["expected"] and m["bank_description"] == "CHECK # 1043" and m["date"] == f"{y}-05-06" and m["account_id"] == acc["id"]
    # typed-in lines can be changed / deleted; statement lines only by undoing their import
    stmt = next(x for x in rows if x["id"] != t["id"])
    assert client.delete(f"/api/accounting/txns/{stmt['id']}", headers=admin_headers).status_code == 400
    imp = next(i for i in api.get("/api/accounting/imports") if i["id"] == r["import_id"])
    assert imp["still_there"] == 1
    api.delete(f"/api/accounting/imports/{r['import_id']}")
    rows = _txns(api, start=f"{y}-05-01", end=f"{y}-05-31", source_id=src["id"])["rows"]
    assert [x["id"] for x in rows] == [t["id"]]
    # a book entry (never on a statement) is never "expected"
    b = api.post("/api/accounting/txns", json={"date": f"{y}-06-01", "amount": -500, "description": "Interest to add", "expected": True})
    assert b["source"] == "Book Entries" and not b["expected"]
    api.delete(f"/api/accounting/txns/{b['id']}")


def test_card_statement_debit_credit_columns_and_rule(api, client, admin_headers):
    y = 2037
    card = _source(api, uid("Cap One "), kind="card")
    acc = api.post("/api/accounting/accounts", json={"name": uid("Amazon Supplies "), "kind": "expense"})
    api.post("/api/accounting/rules", json={"contains": "amazon mktpl", "account_id": acc["id"], "sign": "out"})
    data = (f"Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
            f"{y}-02-01,{y}-02-02,1234,AMAZON MKTPL*AB12,Merchandise,45.10,\n"
            f"{y}-02-05,{y}-02-05,1234,CAPITAL ONE AUTOPAY PYMT,Payment/Credit,,500.00\n").encode()
    prev = client.post("/api/accounting/import/preview", headers=admin_headers, data={"source_id": str(card["id"])},
                       files={"file": ("cap.csv", io.BytesIO(data), "text/csv")}).json()
    assert prev["layout"]["has_header"] and prev["layout"]["debit"] == 5 and prev["layout"]["credit"] == 6
    r = _import(client, admin_headers, card["id"], "cap.csv", data)
    assert r["added"] == 2 and r["auto"] == 1
    rows = {t["description"]: t for t in _txns(api, start=f"{y}-01-01", end=f"{y}-03-31", source_id=card["id"])["rows"]}
    assert rows["AMAZON MKTPL*AB12"]["amount"] == -45.1 and rows["AMAZON MKTPL*AB12"]["how"] == "rule"
    assert rows["CAPITAL ONE AUTOPAY PYMT"]["amount"] == 500.0 and rows["CAPITAL ONE AUTOPAY PYMT"]["account_id"] is None
    assert any(x["hits"] >= 1 for x in api.get("/api/accounting/rules") if x["account_id"] == acc["id"])
    # an account with lines can't be deleted, only merged
    other = api.post("/api/accounting/accounts", json={"name": uid("Supplies "), "kind": "expense"})
    assert client.delete(f"/api/accounting/accounts/{acc['id']}", headers=admin_headers).status_code == 400
    api.post(f"/api/accounting/accounts/{acc['id']}/merge?into_id={other['id']}")
    assert _txns(api, start=f"{y}-01-01", end=f"{y}-03-31", source_id=card["id"], account_id=other["id"])["total"] == 1


def test_ap_ar_from_athub_with_a_persons_word_on_top(make, api):
    item = make.item(price=10)
    order, sh, inv = make.sold([(item, 3, 10.0)])
    vendor = make.vendor()
    po = make.po(vendor=vendor, lines=[(item, 5, 4.0)])
    api.post(f"/api/purchase-orders/{po['id']}/mark-ordered")
    d = api.get("/api/accounting/open-items")
    ar = next(r for r in d["items"] if r["source"] == "invoice" and r["source_id"] == inv["id"])
    ap = next(r for r in d["items"] if r["source"] == "po" and r["source_id"] == po["id"])
    assert ar["side"] == "ar" and ar["group"] == "Invoices Not Sent" and ar["amount"] == inv["total"]
    assert ap["side"] == "ap" and ap["amount"] == 20.0
    # "we already paid that" -- AT-HUB's PO isn't touched, the item drops out of AP
    api.post("/api/accounting/open-items", json={"source": "po", "source_id": po["id"], "status": "paid", "note": "paid by wire"})
    d2 = api.get("/api/accounting/open-items")
    ap2 = next(r for r in d2["items"] if r["key"] == ap["key"])
    assert ap2["status"] == "paid" and ap2["paid_date"] and round(d["ap"] - d2["ap"], 2) == 20.0
    assert api.get(f"/api/purchase-orders/{po['id']}")["amount_paid"] == 0
    # a different amount on an invoice; then back to AT-HUB's figure
    api.post("/api/accounting/open-items", json={"source": "invoice", "source_id": inv["id"], "amount": 5})
    d3 = api.get("/api/accounting/open-items")
    ar3 = next(r for r in d3["items"] if r["key"] == ar["key"])
    assert ar3["amount"] == 5 and ar3["athub_amount"] == inv["total"]
    api.delete(f"/api/accounting/open-items/{ar3['override_id']}")
    assert next(r for r in api.get("/api/accounting/open-items")["items"] if r["key"] == ar["key"])["amount"] == inv["total"]
    # typed in
    m = api.post("/api/accounting/open-items", json={"source": "manual", "side": "ap", "party": "Jill", "ref": "4 visits", "amount": 1800, "due_date": "2020-01-31"})
    d4 = api.get("/api/accounting/open-items")
    assert any(r.get("id") == m["id"] and r["group"] == "Typed In" and r["overdue"] for r in d4["items"])
    assert round(d4["ap"] - d2["ap"], 2) == 1800
    api.delete(f"/api/accounting/open-items/{m['id']}")


def test_mapper_reads_payee_and_note():
    from app.services.acct_mapper import Mapper, payee, payee_label
    assert payee("ZELLE TO SHAH NIRAJ ON 01/02 REF #RP0YDJJPHD") == "zelle:SHAH NIRAJ"
    assert payee("WT 1 AXIS BANK /BNF=AKBARALI ENTERPRISES SRF# OW1") == "wire:AKBARALI ENTERPRISES"
    assert payee_label("BUSINESS TO BUSINESS ACH CAPITAL ONE CRCARDPMT 240819 3Y1Z ANUJ MITTAL") == "Capital One Crcardpmt (ACH)"
    ex = [("ZELLE TO SHAH NIRAJ ON 01/02 REF #A1", -1, 1), ("ZELLE TO SHAH NIRAJ ON 01/03 REF #A2", -1, 1), ("ZELLE TO SHAH NIRAJ ON 01/04 REF #A3", -1, 1),
          ("ZELLE TO SHAH NIRAJ ON 02/02 REF #B1 MAALIKS", -1, 2), ("ZELLE TO SHAH NIRAJ ON 02/03 REF #B2 FOR MAALIKS", -1, 2),
          ("ZELLE TO SHAH NIRAJ ON 03/03 REF #C1 SALARY", -1, 3)]
    m = Mapper(ex)
    assert m.suggest("ZELLE TO SHAH NIRAJ ON 05/05 REF #Z9 MAALIKS", -5)[:2] == (2, "sure")
    assert m.suggest("ZELLE TO SHAH NIRAJ ON 05/05 REF #Z9", -5)[0] == 1
    assert m.suggest("ZELLE TO SHAH NIRAJ ON 05/05 REF #Z9 SALARY", -5)[1] != "sure"  # one line before: a guess
    assert m.suggest("SOMETHING NEW ENTIRELY", -5) == (None, None, "new payee")


def test_accounting_is_a_money_permission():
    from app.services import permissions as P
    assert "accounting" in P.MONEY and dict((k, d) for k, _m, _l, _money, d in P.CATALOG)["accounting"] == "admin"
