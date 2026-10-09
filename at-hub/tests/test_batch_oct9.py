"""The 2026-10-09 batch: payment terms + due dates, Print Options, credit memos, overdue reminders, duplicate vendor
bills, profit from what was invoiced, Recently Viewed and saved filters."""
import io
from datetime import datetime, timedelta

from pypdf import PdfReader

from app.services.money import cents


def _text(pdf_bytes):
    return " ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf_bytes)).pages)


def _day(iso):
    return datetime.fromisoformat(iso.rstrip("Z")).date()


# ---------------- payment terms -> due date ----------------
def test_new_invoice_due_date_follows_customer_terms(make, api):
    cust = make.customer(details={"payment_terms": "Net 45", "phones": [], "emails": [], "addresses": [], "people": []})
    assert api.get(f"/api/customers/{cust['id']}")["payment_terms"] == "Net 45"
    a = make.item(price=2)
    make.stock(a, 5)
    o = make.order(customer=cust, lines=[(a, 5, 2)])
    inv = make.invoice(make.ship(o))
    assert (_day(inv["due_date"]) - _day(inv["invoice_date"])).days == 45
    # no terms on the card = Net 30
    o2, sh2, inv2 = make.sold([(make.item(price=1), 3, 1)])
    assert (_day(inv2["due_date"]) - _day(inv2["invoice_date"])).days == 30


def test_blank_due_dates_are_backfilled(make, api):
    from app.config.database import SessionLocal
    from app.models import Invoice
    from app.services.terms import backfill_due_dates
    o, sh, inv = make.sold([(make.item(price=1), 2, 1)])
    db = SessionLocal()
    try:
        db.get(Invoice, inv["id"]).due_date = None
        db.commit()
        assert backfill_due_dates(db) >= 1
        got = db.get(Invoice, inv["id"])
        assert (got.due_date - got.invoice_date).days == 30
    finally:
        db.close()


# ---------------- Print Options ----------------
def test_print_options_saved_and_due_date_toggle(make, api, client, admin_headers):
    o, sh, inv = make.sold([(make.item(price=3), 4, 3)])
    opts = api.get("/api/auth/print-options")
    assert {o["key"] for o in opts["docs"]["invoice"]} == {"due_date", "payments", "zero_lines", "notes"}
    assert "Due date" in _text(client.get(f"/api/invoices/{inv['id']}/pdf", headers=admin_headers).content)
    assert "Due date" not in _text(client.get(f"/api/invoices/{inv['id']}/pdf?due_date=false", headers=admin_headers).content)
    # a screen's record print remembers whatever it sends
    api.put("/api/auth/print-options/record_customer_orders", json={"off": ["Money summary"], "prices": False, "notes": True})
    assert api.get("/api/auth/print-options")["other"]["record_customer_orders"]["off"] == ["Money summary"]
    api.put("/api/auth/print-options/Bad-Type!", json={}, expect=400)


def test_packing_list_follows_saved_options(make, api, client, admin_headers):
    a = make.item(price=1)
    make.stock(a, 4)
    o = make.order(lines=[(a, 4, 1)])
    sh = make.ship(o)
    api.put("/api/auth/print-options/packing_list", json={"boxes": True, "pallets": True, "pallet_boxes": False, "lots": True, "notes": True})
    try:
        r = client.get(f"/api/shipments/{sh['id']}/packing-list.pdf", headers=admin_headers)
        assert r.status_code == 200 and "Lot" in _text(r.content)
    finally:
        api.put("/api/auth/print-options/packing_list", json={"boxes": True, "pallets": True, "pallet_boxes": False, "lots": False, "notes": True})


# ---------------- credit memos ----------------
def test_credit_memo_squares_over_billing_and_pays_an_invoice(make, api, client, admin_headers):
    a = make.item(price=5)
    o, sh, inv = make.sold([(a, 10, 5)])                    # $50
    lines = [{**l, "quantity": 12} for l in inv["lines"]]   # billed 12 of 10 shipped
    api.put(f"/api/invoices/{inv['id']}", json={"lines": lines, "accept_qty_differences": True, "qty_note": "test"})
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"})
    ledger = api.get(f"/api/customer-orders/{o['id']}/billing")["lines"][0]
    assert "over_ordered_billed" in ledger["problems"]
    ol = inv["lines"][0]["order_line_id"]
    m = api.post("/api/credit-memos/", json={"invoice_id": inv["id"], "reason": "billed 2 too many",
                                             "lines": [{"item_id": a["id"], "order_line_id": ol, "description": "bolts", "quantity": 2, "unit_price": 5}]})
    assert m["status"] == "draft" and m["total"] == 10 and m["customer_id"] == inv["customer_id"]
    # a draft doesn't count yet
    assert api.get(f"/api/customer-orders/{o['id']}/billing")["lines"][0]["billed"] == 12
    m = api.post(f"/api/credit-memos/{m['id']}/issue")
    row = api.get(f"/api/customer-orders/{o['id']}/billing")["lines"][0]
    assert row["billed"] == 10 and row["credited"] == 2 and row["problems"] == []
    # aging / statement: the credit lowers what's owed
    aging = next(r for r in api.get("/api/analytics/ar-aging") if r["customer_id"] == inv["customer_id"])
    assert aging["credit"] == -10 and aging["total"] == 50
    # use it on the invoice: a payment "credit memo"
    m = api.post(f"/api/credit-memos/{m['id']}/apply", json={"invoice_id": inv["id"]})
    assert m["applied"] == 10 and m["remaining"] == 0
    got = api.get(f"/api/invoices/{inv['id']}")
    assert got["balance"] == 50 and any(p["method"] == "credit memo" and p["reference"] == m["code"] for p in got["payments"])
    api.post(f"/api/credit-memos/{m['id']}/void", json={}, expect=400)          # used: can't void
    assert "CREDIT MEMO" in _text(client.get(f"/api/credit-memos/{m['id']}/pdf", headers=admin_headers).content)


def test_credit_memo_rules(make, api):
    o, sh, inv = make.sold([(make.item(price=2), 5, 2)])
    m = api.post("/api/credit-memos/", json={"invoice_id": inv["id"], "lines": []})
    api.post(f"/api/credit-memos/{m['id']}/issue", expect=400)                  # $0
    api.post("/api/credit-memos/", json={"invoice_id": inv["id"], "lines": [{"description": "x", "quantity": 1, "unit_price": -3}]}, expect=400)
    m = api.put(f"/api/credit-memos/{m['id']}", json={"lines": [{"description": "Price adjustment", "quantity": 1, "unit_price": 1.5}]})
    m = api.post(f"/api/credit-memos/{m['id']}/issue")
    api.put(f"/api/credit-memos/{m['id']}", json={"reason": "x"}, expect=400)    # issued: locked
    api.post(f"/api/credit-memos/{m['id']}/apply", json={"invoice_id": inv["id"]}, expect=400)  # invoice still a draft
    other = make.sold([(make.item(price=1), 1, 1)])[2]
    api.put(f"/api/invoices/{other['id']}/status", json={"status": "sent"})
    api.post(f"/api/credit-memos/{m['id']}/apply", json={"invoice_id": other["id"]}, expect=400)  # another customer
    m = api.post(f"/api/credit-memos/{m['id']}/void", json={"reason": "entered by mistake"})
    assert m["status"] == "void" and m["remaining"] == 0
    api.delete(f"/api/credit-memos/{m['id']}")


# ---------------- reminders ----------------
def test_overdue_reminder_draft_and_task(make, api):
    from app.config.database import SessionLocal
    from app.models import Invoice
    o, sh, inv = make.sold([(make.item(price=4), 5, 4)])
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"})
    db = SessionLocal()
    try:
        db.get(Invoice, inv["id"]).due_date = datetime.utcnow() - timedelta(days=5)
        db.commit()
    finally:
        db.close()
    rows = api.get("/api/reminders/overdue")
    me = next(r for r in rows if r["customer_id"] == inv["customer_id"])
    assert me["amount"] == 20 and me["invoices"][0]["code"] == inv["code"] and me["reminder_due"]
    d = api.get(f"/api/reminders/draft/{inv['customer_id']}")
    assert inv["code"] in d["body"] and "overdue" in d["subject"].lower()
    keys = [t["key"] for t in api.get("/api/tasks/")]
    assert f"overdue:{inv['customer_id']}" in keys
    r = api.post(f"/api/reminders/send/{inv['customer_id']}", json={"to": "a@b.com", "subject": "s", "body": "b"}, expect=400)
    assert "Email isn't set up" in r["detail"]                                 # no SMTP in tests


# ---------------- duplicate vendor invoice # ----------------
def test_same_vendor_invoice_number_on_two_pos_asks_first(make, api):
    v = make.vendor()
    a = make.item()
    p1, p2 = make.po(vendor=v, lines=[(a, 5, 1)]), make.po(vendor=v, lines=[(a, 5, 1)])
    api.post(f"/api/purchase-orders/{p1['id']}/bills", json={"bill_number": "VB-77", "amount": 5})
    r = api.post(f"/api/purchase-orders/{p2['id']}/bills", json={"bill_number": "vb-77", "amount": 5}, expect=409)
    assert p1["code"] in r["detail"]
    api.post(f"/api/purchase-orders/{p2['id']}/bills", json={"bill_number": "VB-77", "amount": 5, "allow_duplicate": True})


# ---------------- profit from what was invoiced ----------------
def test_profit_uses_invoiced_price_and_credits(make, api):
    a = make.item(price=10)
    o, sh, inv = make.sold([(a, 10, 10)])                  # order price 10
    lines = [{**l, "unit_price": 9} for l in inv["lines"]]  # invoiced at 9
    api.put(f"/api/invoices/{inv['id']}", json={"lines": lines})
    p = api.get(f"/api/customer-orders/{o['id']}/profit")
    assert p["shipped"]["revenue"] == 90 and any("invoiced" in w for w in p["warnings"])
    m = api.post("/api/credit-memos/", json={"invoice_id": inv["id"], "lines": [{"description": "goodwill", "quantity": 1, "unit_price": 5}]})
    api.post(f"/api/credit-memos/{m['id']}/issue")
    assert api.get(f"/api/customer-orders/{o['id']}/profit")["other_charges"] == -5


# ---------------- Recently Viewed / saved filters ----------------
def test_recent_and_saved_filters(api):
    api.post("/api/auth/recent", json={"kind": "Order", "id": 1, "label": "C1", "url": "customer-orders.html?id=1"})
    api.post("/api/auth/recent", json={"kind": "Invoice", "id": 2, "label": "INV-2", "url": "invoices.html?id=2"})
    rows = api.post("/api/auth/recent", json={"kind": "Order", "id": 1, "label": "C1", "url": "customer-orders.html?id=1"})
    assert [r["id"] for r in rows[:2]] == [1, 2] and len([r for r in rows if r["kind"] == "Order" and r["id"] == 1]) == 1
    api.post("/api/auth/recent", json={"kind": "Order", "id": 3, "label": "x", "url": "https://evil.example/?id=3"}, expect=400)
    saved = api.put("/api/auth/filters/invoices", json=[{"name": "Hudson", "state": {"fields": {"search": "Hudson"}}}, {"name": " ", "state": {}}])
    assert [f["name"] for f in saved] == ["Hudson"]
    assert api.get("/api/auth/filters/invoices")[0]["state"]["fields"]["search"] == "Hudson"
