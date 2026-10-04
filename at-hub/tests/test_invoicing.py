"""Customer invoicing: every dollar adds up -- lines, shipping, partials, combines, payments, PDFs."""
import io
import re

import pytest
from pypdf import PdfReader

from app.services.money import cents, line_amount


def total_of(inv):
    return cents(sum(line_amount(l["quantity"], l["unit_price"]) for l in inv["lines"]))


# ---------------- the rounding rule ----------------
@pytest.mark.parametrize("qty,price,expect", [
    (650, 2.55588, 1661.32),   # 5-decimal price
    (3, 0.335, 1.01),          # half up: 1.005 -> 1.01 (float would give 1.00)
    (1, 2.675, 2.68),          # 2.675 is 2.67499... as a float
    (8378, 0.19, 1591.82),
    (1, 0, 0.0),
    (7, 1 / 3, 2.33),
])
def test_line_amount_rounds_half_up_to_the_cent(qty, price, expect):
    assert line_amount(qty, price) == expect


# ---------------- one shipment ----------------
def test_invoice_total_is_sum_of_rounded_lines_plus_shipping(make):
    a, b = make.item(price=2.55588), make.item(price=0.335)
    o, sh, inv = make.sold([(a, 650, 2.55588), (b, 3, 0.335)], shipping=187.81)
    assert inv["status"] == "draft"
    assert [l["amount"] for l in inv["lines"] if l["item_id"]] == [1661.32, 1.01]
    assert inv["total"] == cents(1661.32 + 1.01 + 187.81) == 1850.14
    assert inv["balance"] == inv["total"] and inv["amount_paid"] == 0


def test_invoice_bills_only_what_shipped(make):
    a = make.item(price=4)
    make.stock(a, 100)
    o = make.order(lines=[(a, 100, 4)])
    line = o["lines"][0]
    sh1 = make.ship(o, {line["id"]: 40})
    inv1 = make.invoice(sh1)
    assert inv1["lines"][0]["quantity"] == 40 and inv1["total"] == 160.00
    o = make.api.get(f"/api/customer-orders/{o['id']}")
    sh2 = make.ship(o)                                   # the other 60
    inv2 = make.invoice(sh2)
    assert inv2["total"] == 240.00
    assert inv1["total"] + inv2["total"] == 100 * 4      # nothing billed twice, nothing missed


def test_shipment_cannot_be_invoiced_twice(make, api):
    a = make.item(price=1)
    o, sh, inv = make.sold([(a, 5, 1)])
    r = api.post(f"/api/invoices/from-shipment/{sh['id']}", json={}, expect=400)
    assert "already on invoice" in r["detail"]


def test_zero_price_nut_lines_dont_change_the_total(make):
    bolt, nut = make.item(price=3.85), make.item(group="Nut", price=0)
    o, sh, inv = make.sold([(bolt, 50, 3.85), (nut, 50, 0)])
    assert inv["total"] == 192.50
    assert len(inv["lines"]) == 2  # the $0 nut is on the invoice (hidden on the PDF unless asked)


# ---------------- several shipments ----------------
def test_combined_invoice_from_two_shipments(make):
    a = make.item(price=1.25)
    make.stock(a, 30)
    o = make.order(lines=[(a, 30, 1.25)])
    lid = o["lines"][0]["id"]
    s1 = make.ship(o, {lid: 10})
    s2 = make.ship(make.api.get(f"/api/customer-orders/{o['id']}"), {lid: 20})
    inv = make.invoice([s1, s2])
    assert inv["is_combined"] and sorted(inv["shipment_ids"]) == sorted([s1["id"], s2["id"]])
    assert inv["total"] == 37.50 == total_of(inv)
    assert {l["shipment_id"] for l in inv["lines"]} == {s1["id"], s2["id"]}  # each line still traces to its shipment


def test_merging_drafts_sums_shipping_into_one_line_and_split_undoes_it(make, api):
    a = make.item(price=2)
    make.stock(a, 20)
    o = make.order(lines=[(a, 20, 2)])
    lid = o["lines"][0]["id"]
    s1 = make.ship(o, {lid: 5})
    s2 = make.ship(api.get(f"/api/customer-orders/{o['id']}"), {lid: 15})
    i1, i2 = make.invoice(s1, shipping=10.10), make.invoice(s2, shipping=20.25)
    before = i1["total"] + i2["total"]
    merged = api.post(f"/api/invoices/{i1['id']}/merge", json={"invoice_ids": [i2["id"]]})
    shipping = [l for l in merged["lines"] if l["item_id"] is None]
    assert len(shipping) == 1 and shipping[0]["amount"] == 30.35
    assert merged["total"] == cents(before) == 70.35
    assert api.get(f"/api/invoices/{i2['id']}", expect=404)  # the emptied draft is gone
    back = api.post(f"/api/invoices/{merged['id']}/split")
    assert back, "split should give back the folded-in draft"
    after = api.get(f"/api/invoices/{merged['id']}")["total"] + sum(b["total"] for b in back)
    assert cents(after) == 70.35  # splitting never loses or doubles money


# ---------------- payments ----------------
def test_payments_partial_then_full_marks_paid(make, api):
    a = make.item(price=99.99)
    o, sh, inv = make.sold([(a, 3, 99.99)])
    api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": 100}, expect=400)  # draft: not yet
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"})
    inv = api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": 100.00})
    assert inv["status"] == "sent" and inv["balance"] == 199.97
    r = api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": 200}, expect=400)
    assert "exceeds" in r["detail"]
    inv = api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": 199.97})
    assert inv["status"] == "paid" and inv["balance"] == 0 and inv["amount_paid"] == 299.97


def test_payments_adding_up_with_float_noise_still_close_the_invoice(make, api):
    a = make.item(price=0.1)
    o, sh, inv = make.sold([(a, 3, 0.1)])  # 0.30
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"})
    api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": 0.1})
    api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": 0.2})
    inv = api.get(f"/api/invoices/{inv['id']}")
    assert inv["status"] == "paid" and inv["balance"] == 0  # 0.1 + 0.2 != 0.3 in floats


def test_editing_a_paid_invoice_up_reopens_it(make, api):
    a = make.item(price=10)
    o, sh, inv = make.sold([(a, 2, 10)])
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"})
    api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": 20})
    lines = [{k: l[k] for k in ("item_id", "order_line_id", "shipment_id", "description", "quantity", "unit_price")} for l in inv["lines"]]
    lines[0]["unit_price"] = 12.5
    inv = api.put(f"/api/invoices/{inv['id']}", json={"lines": lines})
    assert inv["total"] == 25.00 and inv["balance"] == 5.00 and inv["status"] == "sent"


def test_void_frees_the_shipment_and_is_refused_once_paid(make, api):
    a = make.item(price=5)
    o, sh, inv = make.sold([(a, 4, 5)])
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "void"})
    assert api.get(f"/api/shipments/{sh['id']}")["status"] == "shipped"
    inv2 = make.invoice(sh)                    # billable again
    api.put(f"/api/invoices/{inv2['id']}/status", json={"status": "sent"})
    api.post(f"/api/invoices/{inv2['id']}/payments", json={"amount": 5})
    api.put(f"/api/invoices/{inv2['id']}/status", json={"status": "void"}, expect=400)


# ---------------- the PDF says the same thing ----------------
def test_pdf_total_matches_invoice_total(make, client, admin_headers):
    a, b = make.item(price=2.55588), make.item(price=0.335)
    o, sh, inv = make.sold([(a, 650, 2.55588), (b, 3, 0.335)], shipping=187.81)
    r = client.get(f"/api/invoices/{inv['id']}/pdf", headers=admin_headers)
    assert r.status_code == 200
    text = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
    amounts = {m.replace(",", "") for m in re.findall(r"\$?\s?(\d{1,3}(?:,\d{3})*\.\d{2})", text)}
    for must in ("1661.32", "1.01", "187.81", "1850.14"):
        assert must in amounts, f"{must} not printed on the invoice PDF: {sorted(amounts)}"


def test_cannot_mark_paid_while_money_is_owed(make, api):
    a = make.item(price=10)
    o, sh, inv = make.sold([(a, 1, 10)])
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"})
    r = api.put(f"/api/invoices/{inv['id']}/status", json={"status": "paid"}, expect=400)
    assert "still open" in r["detail"]


def test_billing_a_different_qty_needs_accepting_and_is_tracked(api, client, admin_headers, make):
    """Billing more or less than delivered is refused until accepted; the accepted difference is kept on the order,
    and once the order is fully billed a task flags that billed != shipped. A $0 line never counts."""
    from app.config.database import SessionLocal
    from app.models import BillingVariance
    from app.services import billing
    a, nut = make.item(price=2), make.item(price=0)
    make.stock(a, 10)
    make.stock(nut, 10)
    o = make.order(lines=[(a, 10, 2), (nut, 10, 0)])
    inv = make.invoice(make.ship(o))
    lines = [{k: l[k] for k in ("item_id", "order_line_id", "shipment_id", "description", "quantity", "unit_price")} for l in inv["lines"]]
    for l in lines:
        if l["item_id"] == a["id"]:
            l["quantity"] = 12
        if l["item_id"] == nut["id"]:
            l["quantity"] = 3   # $0 line: no warning
    diffs = client.post(f"/api/invoices/{inv['id']}/qty-check", json={"lines": lines}, headers=admin_headers).json()
    assert [(d["delivered"], d["billed"]) for d in diffs] == [(10, 12)]
    r = client.put(f"/api/invoices/{inv['id']}", json={"lines": lines}, headers=admin_headers)
    assert r.status_code == 400 and "delivered 10, billing 12" in r.json()["detail"]
    r = client.put(f"/api/invoices/{inv['id']}", json={"lines": lines, "accept_qty_differences": True, "qty_note": "agreed extra"}, headers=admin_headers)
    assert r.status_code == 200
    db = SessionLocal()
    v = db.query(BillingVariance).filter(BillingVariance.invoice_id == inv["id"]).one()
    assert (v.delivered_qty, v.billed_qty, v.reason) == (10, 12, "agreed extra")
    flagged = {row["order"].id: row["lines"] for row in billing.unbalanced_orders(db)}
    db.close()
    assert [(l["shipped"], l["billed"]) for l in flagged[o["id"]]] == [(10, 12)]
    tasks = client.get("/api/tasks/", headers=admin_headers).json()
    t = next(t for t in tasks if t["key"] == f"billing:{o['code']}")
    assert "over by 2" in t["detail"] and "agreed extra" in t["detail"]
    # voiding the invoice drops the record
    client.put(f"/api/invoices/{inv['id']}/status", json={"status": "void"}, headers=admin_headers)
    db = SessionLocal()
    assert not db.query(BillingVariance).filter(BillingVariance.invoice_id == inv["id"]).count()
    db.close()
