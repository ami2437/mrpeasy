"""Vendor Accounts (wire + cash vendors), the go-live close-out, and imports keeping vendor payments.

The vendor in the user's case (2026-10-16) bills part by wire and part in cash, then collects old cash by adding it to
later invoices -- sometimes more than was owed. Made-up numbers here, same shape as their sheet: no real data in the repo."""
import io
import json
from datetime import datetime, timedelta

from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (AppSetting, Base, PurchaseOrder, PurchaseOrderPayment, VacctAccount, VacctAlloc, VacctBill, VacctPayment, Vendor,
                        VendorBill, VendorPayment)
from tests.builders import uid


def _account(api, make, so_numbers=()):
    v = make.vendor()
    item = make.item()
    pos = {so: make.po(vendor=v, lines=[(item, 10, 100)], vendor_so_number=so) for so in so_numbers}
    acc = api.post("/api/vendor-accounts/", json={"vendor_id": v["id"], "cash_share": 50})
    return v, pos, acc["account"]["id"]


def _bill(view, number):
    return next(b for b in view["bills"] if b["bill_number"] == number)


def test_recovery_on_a_freight_invoice_settles_old_cash(api, make):
    """Invoice A $4,000 (wire 2,500 / cash 1,500 as he billed it). Freight invoice B is really $400 but says $1,900:
    the $1,900 wire pays B and A's cash. Then he over-collects: the extra stays a visible credit."""
    v, pos, aid = _account(api, make, ["A1/25-26", "B1/25-26"])
    view = api.post(f"/api/vendor-accounts/{aid}/bills", json={"so_number": "A1/25-26", "bill_number": "AE/1/25-26", "bill_date": "2026-01-10",
                                                               "goods": 4000, "cash_due": 1500})
    a = _bill(view, "AE/1/25-26")
    assert a["wire_due"] == 2500 and a["cash_due"] == 1500 and a["po_code"] == pos["A1/25-26"]["code"]
    view = api.post(f"/api/vendor-accounts/{aid}/payments", json={"kind": "wire", "amount": 2500, "paid_date": "2026-01-12", "reference": "OW1",
                                                                  "for_bill_ids": [a["id"]]})
    assert view["totals"]["cash_left"] == 1500 and view["totals"]["wire_left"] == 0
    # the inflated freight invoice: true $400, paper $1,900
    view = api.post(f"/api/vendor-accounts/{aid}/bills", json={"so_number": "B1/25-26", "bill_number": "AE/2/25-26", "bill_date": "2026-02-01",
                                                               "freight": 400, "paper_amount": 1900})
    b = _bill(view, "AE/2/25-26")
    assert b["total"] == 400 and b["wire_due"] == 400 and b["cash_due"] == 0 and b["recovery_in"] == 1500
    s = api.post(f"/api/vendor-accounts/{aid}/suggest", json={"kind": "wire", "amount": 1900, "came_with_bill_id": b["id"]})
    assert [(p["bill_id"], p["part"], p["amount"]) for p in s["parts"]] == [(b["id"], "wire", 400), (a["id"], "cash", 1500)] and s["credit"] == 0
    view = api.post(f"/api/vendor-accounts/{aid}/payments", json={"kind": "wire", "amount": 1900, "paid_date": "2026-02-03", "reference": "OW2",
                                                                  "came_with_bill_id": b["id"]})
    t = view["totals"]
    assert t["owed"] == 0 and t["recovered"] == 1500 and t["total"] == 4400 and t["paper"] == 5900   # true cost counted once
    paid_a = _bill(view, "AE/1/25-26")["paid_by"]
    assert any(r["recovery"] and r["came_with"] == "AE/2/25-26" and r["amount"] == 1500 for r in paid_a)
    # over-collection: $200 more than anything owed -> credit, not lost, not a negative bill
    view = api.post(f"/api/vendor-accounts/{aid}/payments", json={"kind": "wire", "amount": 200, "paid_date": "2026-03-01"})
    assert view["totals"]["credit"] == 200 and view["totals"]["owed"] == -200
    assert all(b["balance"] >= 0 for b in view["bills"])
    # a later cash invoice uses the credit by editing that payment
    view = api.post(f"/api/vendor-accounts/{aid}/bills", json={"bill_number": "AE/3/25-26", "goods": 1000, "cash_due": 1000})
    c = _bill(view, "AE/3/25-26")
    credit_pay = next(p for p in view["payments"] if p["credit"] > 0)
    view = api.put(f"/api/vendor-accounts/{aid}/payments/{credit_pay['id']}", json={"kind": "wire", "amount": 200,
                                                                                    "parts": [{"bill_id": c["id"], "part": "cash", "amount": 200}]})
    assert view["totals"]["credit"] == 0 and _bill(view, "AE/3/25-26")["cash_left"] == 800
    # a part can't be more than what's open on it; the parts can't be more than the payment
    api.put(f"/api/vendor-accounts/{aid}/payments/{credit_pay['id']}", json={"kind": "wire", "amount": 200,
            "parts": [{"bill_id": c["id"], "part": "cash", "amount": 2000}]}, expect=400)
    api.post(f"/api/vendor-accounts/{aid}/payments", json={"kind": "cash", "amount": 100,
             "parts": [{"bill_id": c["id"], "part": "cash", "amount": 150}]}, expect=400)
    # a bill with payments on it can't be deleted; its true amount can't be more than the paper
    api.delete(f"/api/vendor-accounts/{aid}/bills/{a['id']}", expect=400)
    api.post(f"/api/vendor-accounts/{aid}/bills", json={"bill_number": "X", "goods": 500, "paper_amount": 400}, expect=400)


def test_the_pos_follow_the_account(api, make):
    """On the account's POs the import's lump payment is replaced by what the account paid; the vendor bill gets the true
    amount; other POs of the vendor are left alone."""
    from app.config.database import SessionLocal
    v, pos, aid = _account(api, make, ["S7/26-27", "S8/26-27"])
    other = pos["S8/26-27"]
    db = SessionLocal()
    for po in pos.values():
        db.add(PurchaseOrderPayment(po_id=po["id"], amount=999, reference="mrpeasy-po-export", created_by="import"))
    db.add(VendorBill(po_id=pos["S7/26-27"]["id"], bill_number="47", amount=0))  # MRPeasy kept the short number, $0
    db.commit()
    db.close()
    view = api.post(f"/api/vendor-accounts/{aid}/bills", json={"so_number": "S7/26-27", "bill_number": "IF/47/26-27", "goods": 1000, "cash_due": 400})
    b = _bill(view, "IF/47/26-27")
    api.post(f"/api/vendor-accounts/{aid}/payments", json={"kind": "wire", "amount": 600, "reference": "OW9", "for_bill_ids": [b["id"]]})
    db = SessionLocal()
    pays = db.query(PurchaseOrderPayment).filter(PurchaseOrderPayment.po_id == pos["S7/26-27"]["id"]).all()
    assert [(round(p.amount, 2), p.reference) for p in pays] == [(600, "vendor-account")] and "OW9" in pays[0].note
    bills = db.query(VendorBill).filter(VendorBill.po_id == pos["S7/26-27"]["id"]).all()
    assert [(x.bill_number, x.amount) for x in bills] == [("47", 1000)] and pays[0].vendor_bill_id == bills[0].id
    assert [p.amount for p in db.query(PurchaseOrderPayment).filter(PurchaseOrderPayment.po_id == other["id"])] == [999]  # not in the account
    db.close()


def _sheet(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["SO #", "SO Amount.", "All Invoices Billed", "Invoice Sub Total", "Freight ", "Invoice Total", "Wire Due", "Cash Due",
               "Wire Recd", "Cash Recd", "Cash Adjustment", "Balance", "Date Recvd", "Comments "])
    for r in rows:
        ws.append(r)
    ws.append([None] * 11 + [0])
    ws.append([None, 99999, None, 1, 1, 1, 1, 1, 1, 1, None, 1])  # their totals row
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


SHEET = [
    # SO, SO amt, invoice, sub, freight, total, wire due, cash due, wire recd, cash recd, adj, balance, date, comments
    ["10/25-26", 3000, "AB/1/25-26", 3000, 0, 3000, 1500, 1500, 1500, " $1,500.00 ", None, 0, "Dec 15 2025",
     "Wire sent for 1500\nSent $1000 along with  freight invoice  AB12\nSent $500 on 05/24/2026 with inv 14 to cover this order"],
    [None, None, "AB/2/25-26", 800, 200, 1000, 1000, 0, 1000, None, None, 0, "10-Nov-25", None],
    ["11/25-26", 2000, "AB/3/25-26", 2000, 0, 2000, 1000, 1000, 1000, 700, None, 300, None, "Paid Nov 21 in india"],
    ["12/25-26", 300, "AB/12/25-26", 1300, 0, 1300, 1300, " $-   ", 1300, None, None, 0, "22-Mar", "$1000 Includes from cash payment"],
    ["13/26-27", 100, "AB/14/26-27", 100, 0, 100, 100, 0, 100, None, None, 0, "5/24/2026", "Wire # OW00001234567890 for 100 + 500"],
    ["14/26-27", 4000, "AB/20/26-27", 4000, None, 4000, 2000, 2000, 2000, 2200, None, -200, "6/16/2026\n7/13/2026",
     "Wire # OW00000000000111 for $1500\nWire # OW00000000000222 for $500\n- Applied $2200 towards cash from Invoice 21 ($2300) -x"],
    ["15/26-27", 100, "AB/21/26-27", 2300, None, 2300, 2300, 0, 2300, None, None, 0, None, None],
    ["16/26-27", 500, None, None, 0, 500, 0, 500, 0, None, None, 500, None, None],
]


def test_their_sheet_loads_with_every_recovery_worked_out(api, make, client, admin_headers):
    v, pos, aid = _account(api, make, ["10/25-26", "11/25-26", "12/25-26", "13/26-27"])
    data = _sheet(SHEET)
    files = lambda: {"file": ("sheet.xlsx", io.BytesIO(data), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    r = client.post(f"/api/vendor-accounts/{aid}/sheet/preview", headers=admin_headers, files=files())
    assert r.status_code == 200, r.text
    plan = r.json()
    c = plan["check"]
    # the sheet: billed 14,600 incl. the two recoveries counted twice (1,000 + 2,200); received 12,400 + 2,200 cash recd ...
    assert c["matches"] and c["owed"] == c["sheet_owed"] and c["double_counted"] == 3200 and c["true_billed"] == c["sheet_billed"] - 3200
    assert c["credit"] == 200          # the over-collection on AB/20 (cash due 2,000, 2,200 came in)
    assert len(plan["bills"]) == 8 and not plan["warnings"]
    by_row = {b["row"]: b for b in plan["bills"]}
    assert by_row[4]["paper_amount"] == 1300 and by_row[4]["wire_due"] == 300 and by_row[4]["goods"] == 300   # AB/12: $1,000 of old cash inside
    assert by_row[7]["paper_amount"] == 2300 and by_row[7]["wire_due"] == 100
    wires = [p for p in plan["payments"] if p["kind"] == "wire"]
    ab14 = next(p for p in wires if p["row"] == 5)
    assert ab14["amount"] == 600 and {(x["row"], x["part"], x["amount"]) for x in ab14["parts"]} == {(5, "wire", 100), (1, "cash", 500)}
    ab20 = [p for p in wires if p["row"] == 6]
    assert [(p["amount"], p["reference"], p["date"]) for p in ab20] == [(1500, "OW00000000000111", "2026-06-16"), (500, "OW00000000000222", "2026-07-13")]
    cash = [p for p in plan["payments"] if p["kind"] == "cash"]
    assert [(p["row"], p["amount"]) for p in cash] == [(3, 700)]
    assert by_row[2]["bill_date"] is None and next(p for p in wires if p["row"] == 2)["date"] == "2025-11-10"
    r = client.post(f"/api/vendor-accounts/{aid}/sheet/apply", headers=admin_headers, files=files())
    assert r.status_code == 200, r.text
    view = r.json()["view"]
    assert view["totals"]["owed"] == c["sheet_owed"] and view["totals"]["credit"] == 200
    assert view["open_cash"] and _bill(view, "AB/3/25-26")["cash_left"] == 300
    # the same sheet compared back: every invoice the same (the credit counts as their -200)
    r = client.post(f"/api/vendor-accounts/{aid}/compare", headers=admin_headers, files=files())
    cmpd = r.json()
    assert cmpd["counts"] == {"same": 8} and cmpd["difference"] == 0
    # our own statement, read back the same way: also the same
    x = client.get(f"/api/vendor-accounts/{aid}/statement.xlsx", headers=admin_headers)
    assert x.status_code == 200 and x.headers["content-type"].startswith("application/vnd.openxml")
    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ["Statement", "Payments"]
    ws = wb["Statement"]
    head = [c.value for c in next(r for r in ws.iter_rows(min_row=1, max_row=10) if r[0].value == "SO #")]
    assert "Their Notes" in head and "Old Cash Included" in head
    assert any(str(c.value or "").startswith("=J") for row in ws.iter_rows() for c in row)   # live formulas
    r = client.post(f"/api/vendor-accounts/{aid}/compare", headers=admin_headers,
                    files={"file": ("ours.xlsx", io.BytesIO(x.content), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.json()["counts"] == {"same": 8}
    pdf = client.get(f"/api/vendor-accounts/{aid}/statement.pdf", headers=admin_headers)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    # a second load replaces, never doubles
    r = client.post(f"/api/vendor-accounts/{aid}/sheet/apply", headers=admin_headers, files=files())
    assert len(api.get(f"/api/vendor-accounts/{aid}")["bills"]) == 8


def test_money_permission(client, make, api):
    from app.services.permissions import MONEY, defaults_for
    assert "vendor_accounts" in MONEY and "vendor_accounts" not in defaults_for("manager")
    u = api.post("/api/users/", json={"username": uid("mgr").lower(), "password": "Passw0rd!long", "role": "manager", "full_name": "M"})
    from app.services.auth import AuthService
    h = {"Authorization": f"Bearer {AuthService.create_access_token({'sub': u['username']})}"}
    assert client.get("/api/vendor-accounts/", headers=h).status_code == 403


def _db(path):
    eng = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


def test_fresh_import_keeps_vendor_payments_and_vendor_accounts(tmp_path):
    """The payment the user typed on the site was lost by an import (2026-10-15): now vendor payments and Vendor Accounts
    survive, found again by vendor / PO code; the account's PO payments are put back over the import's lump."""
    from app.importers.mrpeasy.keep import counts, keep_independent
    live_p, new_p = tmp_path / "live.db", tmp_path / "import.db"
    e1, live = _db(live_p)
    e2, new = _db(new_p)
    live.add_all([Vendor(id=1, code="V00005", name="Akbar"), Vendor(id=2, code="V00009", name="Gone Vendor"),
                  PurchaseOrder(id=7, code="PO-7", vendor_id=1, vendor_so_number="90/25-26"), PurchaseOrder(id=8, code="PO-GONE", vendor_id=1)])
    live.add_all([VendorPayment(id=1, code="VP-0001", vendor_id=1, amount=5000, paid_date=datetime(2026, 10, 14), method="wire", reference="OW5"),
                  VendorPayment(id=2, code="VP-0002", vendor_id=2, amount=10)])
    live.add_all([PurchaseOrderPayment(po_id=7, amount=1200, vendor_payment_id=1), PurchaseOrderPayment(po_id=8, amount=300, vendor_payment_id=1)])
    live.add(VacctAccount(id=1, vendor_code="V00005", vendor_name="Akbar", cash_share=50))
    live.add(VacctBill(id=1, account_id=1, so_number="90/25-26", bill_number="IF/47/25-26", goods=1000, wire_due=500, cash_due=500))
    live.add(VacctPayment(id=1, account_id=1, kind="wire", amount=500, reference="OW7"))
    live.add(VacctAlloc(payment_id=1, bill_id=1, part="wire", amount=500))
    live.commit()
    # the import: same vendor / PO under new ids, MRPeasy's lump payment, no vendor payments
    new.add_all([Vendor(id=11, code="V00005", name="Akbar"), PurchaseOrder(id=70, code="PO-7", vendor_id=11, vendor_so_number="90/25-26")])
    new.add(PurchaseOrderPayment(po_id=70, amount=777, reference="mrpeasy-po-export"))
    new.commit()
    live.close(); new.close(); e1.dispose(); e2.dispose()

    kept = keep_independent(new_p, live_p)
    assert kept["vendor_payments"] == 1 and kept["vacct_bills"] == 1 and kept["vacct_payments"] == 1
    e3, chk = _db(new_p)
    vp = chk.query(VendorPayment).one()
    assert vp.code == "VP-0001" and vp.vendor_id == 11 and vp.reference == "OW5"
    pays = sorted((p.amount, p.reference, p.vendor_payment_id) for p in chk.query(PurchaseOrderPayment).filter(PurchaseOrderPayment.po_id == 70))
    assert pays == [(500.0, "vendor-account", None), (1200.0, None, 1)]    # lump replaced; the applied part re-pointed; PO-GONE's part unapplied
    assert chk.query(VendorBill).filter(VendorBill.po_id == 70).one().amount == 1000
    assert counts(new_p)["vendor_payments"] == 1
    chk.close(); e3.dispose()


def test_golive_closeout_receives_old_pos_and_delivers_paid_shipments(api, make):
    from app.config.database import SessionLocal
    from app.models import Shipment
    item = make.item()
    po = make.po(lines=[(item, 40, 2.5)])
    api.post(f"/api/purchase-orders/{po['id']}/mark-ordered")
    api.post(f"/api/purchase-orders/{po['id']}/receive", json={"lines": [{"line_id": po["lines"][0]["id"], "quantity": 10}]})
    db = SessionLocal()
    db.get(PurchaseOrder, po["id"]).order_date = datetime.utcnow() - timedelta(days=200)
    db.commit()
    db.close()
    o, sh, inv = make.sold([(make.item(), 5, 10)])
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"})
    api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": inv["total"]})
    on_hand = api.get(f"/api/stock-items/{item['id']}")["on_hand"]
    prev = api.get("/api/golive/closeout?months=3")
    if prev["done"] and not prev["done"].get("undone_at"):
        api.post("/api/golive/closeout/undo")
        prev = api.get("/api/golive/closeout?months=3")
    p = next(x for x in prev["pos"] if x["po_id"] == po["id"])
    assert p["quantity"] == 30 and p["value"] == 75
    assert any(s["shipment_id"] == sh["id"] for s in prev["shipments"])
    done = api.post("/api/golive/closeout", json={"months": 3, "po_ids": [po["id"]], "shipment_ids": [sh["id"]]})
    assert [x["code"] for x in done["pos"]] == [po["code"]] and [x["code"] for x in done["shipments"]] == [sh["code"]]
    after = api.get(f"/api/purchase-orders/{po['id']}")
    assert after["status"] == "received" and "Closed at go-live" in (after["notes"] or "")
    assert api.get(f"/api/stock-items/{item['id']}")["on_hand"] == on_hand + 30
    db = SessionLocal()
    s = db.get(Shipment, sh["id"])
    assert s.delivered_at == s.ship_date
    db.close()
    api.post("/api/golive/closeout", json={"months": 3, "po_ids": [], "shipment_ids": []}, expect=400)   # once
    undone = api.post("/api/golive/closeout/undo")
    assert not undone["undo_problems"]
    assert api.get(f"/api/stock-items/{item['id']}")["on_hand"] == on_hand
    back = api.get(f"/api/purchase-orders/{po['id']}")
    assert back["status"] == "partially_received" and "Closed at go-live" not in (back["notes"] or "")
    db = SessionLocal()
    assert db.get(Shipment, sh["id"]).delivered_at is None
    db.close()
