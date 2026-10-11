"""A QuickBooks register (Amount = money out, Deposit = money in, masked numbers, its memo after the bank's text, an
Opening Balance row, its Account column) brought in two ways: by dates only (the months the books don't have), and
replacing the lines already in the books for its dates -- each keeps how it was filed; lines not on the file move to Book
Entries; Undo puts everything back. Made-up data."""
import io
from datetime import datetime

from openpyxl import Workbook

from tests.builders import uid
from tests.test_accounting import _csv, _import, _source, _txns
from tests.test_accounting_rules import name


def _qb(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["Date", "Description", "Amount", "Deposit", "Ref No.", "Payee", "Reconciliation Status", "Balance", "Type", "Account", "Added in Banking"])
    for d, desc, out, inn, acct in rows:
        ws.append([d, desc, out, inn, None, None, "Cleared", 0, "Expense" if out else "Deposit", acct, "Automatically added with rule"])
    ws.append(["01/02/2050", "Opening Balance from Bank", None, 10682.5, None, None, "Reconciled", 10682.5, "Deposit", "Opening balance equity", None])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _post(client, h, path, src_id, data, **form):
    r = client.post(path, headers=h, data={"source_id": str(src_id), **{k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in form.items()}},
                    files={"file": ("Register from QB.xlsx", io.BytesIO(data), "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()


def test_quickbooks_register_dates_and_replace_keeping_filing(api, client, admin_headers):
    y = 2050
    src = _source(api)
    labor = api.post("/api/accounting/accounts", json={"name": uid("Labor "), "kind": "expense"})
    ali = api.post("/api/accounting/accounts", json={"name": uid("Ali "), "kind": "cogs"})
    saul, akb = name("GOMEZSAUL"), name("AKBARALI")
    # the books: the person's own "calculated" lines (their wording), filed
    _import(client, admin_headers, src["id"], "mine.csv", _csv([
        (f"06/12/{y}", -7000.0, f"WT {y % 100}0612-023157 AXIS BANK LIMITED /BNF={akb} ENTERPRISES SRF# OW00004589845058 TRN#{y % 100}0612023157"),
        (f"06/12/{y}", -25.0, f"WIRE TRANS SVC CHARGE - SEQUENCE: {y % 100}0612023157 SRF# OW00004589845058"),
        (f"06/20/{y}", -300.0, f"ZELLE TO {saul} ON 06/20 REF #RP0YAAAA11 SAUL"),
        (f"06/15/{y}", -999.0, "MY OWN CALCULATION LINE"),
    ]))
    mine = {t["description"][:8]: t for t in _txns(api, start=f"{y}-01-01", end=f"{y}-12-31", source_id=src["id"])["rows"]}
    api.put(f"/api/accounting/txns/{mine['WT 50061']['id']}", json={"account_id": ali["id"], "learn": False, "note": "first wire"})
    api.put(f"/api/accounting/txns/{mine['ZELLE TO']['id']}", json={"account_id": labor["id"], "learn": False})
    # QuickBooks: payments positive in Amount, masked numbers, shortened / extra text; Jan lines the books don't have
    qb = _qb([
        (f"01/15/{y}", f"ZELLE TO {saul} ON 01/15 REF #RP0YJAN111 SAUL", 250, None, "Contract labor"),
        (f"02/03/{y}", "CHART INDUSTRIES PAYMENTS 500203", None, 5000, "Sales"),
        (f"06/12/{y}", f"WT {y % 100}0612-023157 AXIS BANK LIMITED   /BNF={akb} ENTERPRISES", 7000, None, "Cost of goods sold"),
        (f"06/12/{y}", "WIRE TRANS SVC CHARGE - SEQUENCE:  XXXXXXXX3157   SRF#    OWXXXXXXXX845058 WIRE TRANS SVC CHARGE - SEQUENCE:  XXXXXXXX3290", 25, None, "Bank fees"),
        (f"06/20/{y}", f"ZELLE TO {saul} ON 06/20 REF #RP0YAAAA11 SAUL ZELLE TO {saul} ON 03/05 REF #RP0YOLD999", 300, None, "Contract labor"),
    ])
    p = _post(client, admin_headers, "/api/accounting/import/preview", src["id"], qb)
    lay = p["layout"]
    assert lay["debit"] == 2 and lay["credit"] == 3 and lay["description"] == 1 and lay["hint"] == 9 and "amount" not in lay
    assert p["rows"] == 5 and not p["warning"] and p["money_in"] == 5000 and p["money_out"] == -7575  # no opening balance row
    assert p["already"] == 3 and p["new"] == 2 and p["books_from"] == f"{y}-06-12"
    # way 1: only the months before the books start
    early = _post(client, admin_headers, "/api/accounting/import/preview", src["id"], qb, date_to=f"{y}-06-11")
    assert early["rows"] == 2 and early["outside"] == 3 and early["new"] == 2
    # way 2: replace the books' lines for the file's dates, keeping the filing
    rp = _post(client, admin_headers, "/api/accounting/import/preview", src["id"], qb, date_from=f"{y}-06-01", replace=True)
    assert rp["replace_keep"] == 3 and rp["replace_unmatched"] == 1
    r = _post(client, admin_headers, "/api/accounting/import", src["id"], qb, replace=True)
    assert r["replaced"] == 3 and r["moved_to_book"] == 1 and r["added"] == 2
    now = _txns(api, start=f"{y}-01-01", end=f"{y}-12-31", source_id=src["id"])["rows"]
    wire = next(t for t in now if t["amount"] == -7000)
    assert wire["account_id"] == ali["id"] and wire["note"] == "first wire" and wire["description"].endswith("ENTERPRISES")   # the file's wording, my filing
    assert next(t for t in now if t["amount"] == -300)["account_id"] == labor["id"]
    jan = next(t for t in now if t["amount"] == -250)
    assert jan["date"] == f"{y}-01-15" and jan["account_id"] in (None, labor["id"])
    assert len(now) == 5 and all(t["amount"] != -999 for t in now)
    book = api.get(f"/api/accounting/txns?all_dates=true&q=MY OWN CALCULATION")["rows"]
    assert book and book[0]["source"] == "Book Entries" and "not on" in book[0]["note"]
    # Undo: the books exactly as before
    api.delete(f"/api/accounting/imports/{r['import_id']}")
    back = _txns(api, start=f"{y}-01-01", end=f"{y}-12-31", source_id=src["id"])["rows"]
    assert len(back) == 4 and next(t for t in back if t["amount"] == -7000)["note"] == "first wire"
    assert next(t for t in back if t["amount"] == -999)["source_id"] == src["id"]


def test_a_quickbooks_layout_does_not_change_how_the_bank_csv_reads(api, client, admin_headers):
    """The bank account remembers the QuickBooks columns, but a bank CSV (other headers) is read by its own."""
    y = 2051
    src = _source(api)
    _post(client, admin_headers, "/api/accounting/import", src["id"], _qb([(f"01/15/{y}", "SOMETHING QB", 10, None, "x")]))
    bank = ("DATE,DESCRIPTION,AMOUNT,CHECK #,STATUS\n" f"02/01/{y},WIRE OUT,-500.00,,Posted\n" f"02/02/{y},DEPOSIT IN,800.00,,Posted\n").encode()
    r = client.post("/api/accounting/import", headers=admin_headers, data={"source_id": str(src["id"])}, files={"file": ("Checking.csv", io.BytesIO(bank), "text/csv")})
    assert r.status_code == 200, r.text
    got = {t["description"]: t["amount"] for t in _txns(api, start=f"{y}-01-01", end=f"{y}-12-31", source_id=src["id"])["rows"]}
    assert got["WIRE OUT"] == -500 and got["DEPOSIT IN"] == 800 and got["SOMETHING QB"] == -10
