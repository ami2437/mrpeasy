"""Money edge cases: half-cent payments, editing paid invoices, PO overpayment, float noise in totals."""
import pytest


def _sent(api, inv):
    return api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"})


def test_half_cent_payment_rounds_half_up_and_closes_the_invoice(make, api):
    a = make.item()
    o, sh, inv = make.sold([(a, 1, 2.675)])                       # 1 x 2.675 -> 2.68
    assert inv["total"] == 2.68
    _sent(api, inv)
    inv = api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": 2.675})
    assert inv["amount_paid"] == 2.68 and inv["status"] == "paid"


def test_paid_invoice_cant_be_edited_below_what_was_paid(make, api):
    a = make.item()
    o, sh, inv = make.sold([(a, 10, 5)])
    _sent(api, inv)
    api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": 50})
    lines = [{**l, "unit_price": 4} for l in api.get(f"/api/invoices/{inv['id']}")["lines"]]
    api.put(f"/api/invoices/{inv['id']}", json={"lines": lines}, expect=400)   # would leave the customer $10 overpaid
    assert api.get(f"/api/invoices/{inv['id']}")["balance"] == 0


def test_paid_invoice_cant_be_set_back_to_sent(make, api):
    a = make.item()
    o, sh, inv = make.sold([(a, 2, 5)])
    _sent(api, inv)
    api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": 10})
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"}, expect=400)
    assert api.get(f"/api/invoices/{inv['id']}")["status"] == "paid"


def test_po_payment_is_kept_to_the_cent_and_cant_overpay_the_po(make, api):
    a = make.item()
    po = make.po(lines=[(a, 3, 3.335)])                           # 10.005 -> 10.01
    assert po["order_total"] == 10.01
    po = api.post(f"/api/purchase-orders/{po['id']}/payments", json={"amount": 4.005})
    assert po["amount_paid"] == 4.01 and po["payments"][0]["amount"] == 4.01
    api.post(f"/api/purchase-orders/{po['id']}/payments", json={"amount": 6.01}, expect=400)
    po = api.post(f"/api/purchase-orders/{po['id']}/payments", json={"amount": 6})
    assert po["amount_paid"] == 10.01


def test_vendor_bill_payment_is_rounded_before_the_balance_check(make, api):
    a = make.item()
    po = make.po(lines=[(a, 1, 10)])
    po = api.post(f"/api/purchase-orders/{po['id']}/bills", json={"bill_number": "B-HALF", "amount": 10})
    bill = po["bills"][0]
    po = api.post(f"/api/purchase-orders/{po['id']}/payments", json={"amount": 10.004, "vendor_bill_id": bill["id"]})
    assert po["bills"][0]["balance"] == 0


def test_order_profit_has_no_float_noise(make, api):
    a = make.item()
    make.stock(a, 3, cost=0.1)
    o = make.order(lines=[(a, 3, 0.7)])
    sh = make.ship(o)
    make.invoice(sh, shipping=0.1)
    p = api.get(f"/api/customer-orders/{o['id']}/profit")
    assert p["revenue"] == 2.1 and p["cogs"] == 0.3 and p["gross_profit"] == 1.8 and p["other_charges"] == 0.1
    assert p["net_profit"] == 1.9


def test_shipping_charge_with_fraction_of_a_cent(make, api):
    a = make.item()
    o, sh, inv = make.sold([(a, 3, 1.005)], shipping=10.555)      # 3.015 -> 3.02, 10.555 -> 10.56
    assert inv["total"] == 13.58


def test_landed_cost_amount_kept_to_the_cent(make, api):
    a = make.item()
    po = make.po(lines=[(a, 3, 1)])
    lc = api.post("/api/landed-costs/", json={"description": "Freight", "amount": 10.004, "po_ids": [po["id"]]})
    assert lc["amount"] == 10.0
    assert round(sum(x["amount"] for x in lc["allocations"]), 6) == 10.0


# ---- flows ----

def test_merge_then_split_keeps_every_cent(make, api):
    a = make.item()
    make.stock(a, 30)
    o = make.order(lines=[(a, 30, 1.005)])
    sh1 = make.ship(o, {o["lines"][0]["id"]: 10})
    sh2 = make.ship(api.get(f"/api/customer-orders/{o['id']}"), {o["lines"][0]["id"]: 20})
    i1, i2 = make.invoice(sh1, shipping=5.555), make.invoice(sh2, shipping=4.445)
    t1, t2 = i1["total"], i2["total"]
    assert (t1, t2) == (15.61, 24.55)                             # 10.05 + 5.56 (half up), 20.10 + 4.45
    merged = api.post(f"/api/invoices/{i1['id']}/merge", json={"invoice_ids": [i2["id"]]})
    assert abs(merged["total"] - (t1 + t2)) < 0.001
    api.post(f"/api/invoices/{i1['id']}/split")
    back = [api.get(f"/api/invoices/{i}")["total"] for i in (i1["id"],)]
    others = [i for i in api.get("/api/invoices/") if i["order_id"] == o["id"] and i["status"] != "void" and i["id"] != i1["id"]]
    assert abs(back[0] + sum(i["total"] for i in others) - (t1 + t2)) < 0.001


def test_funding_upload_pays_then_rollback_reopens(make, api, client, admin_headers):
    a = make.item()
    o, sh, inv = make.sold([(a, 3, 33.335)])                      # 100.005 -> 100.01
    assert inv["total"] == 100.01
    _sent(api, inv)
    csv = f"Item Number,Disbursement Date,Funding Amount,Discount\n{inv['code']},10/01/2026,97.01,3.00\n"
    r = client.post("/api/invoice-funding/apply", headers=admin_headers, files={"file": ("f.csv", csv, "text/csv")},
                    data={"record_payments": "true"})
    assert r.status_code == 200, r.text
    got = api.get(f"/api/invoices/{inv['id']}")
    assert got["status"] == "paid" and got["amount_paid"] == 100.01 and got["balance"] == 0
    api.post(f"/api/invoice-funding/imports/{r.json()['import_id']}/rollback")
    got = api.get(f"/api/invoices/{inv['id']}")
    assert got["status"] == "sent" and got["balance"] == 100.01 and got["funding_amount"] is None


def test_manual_funding_kept_to_the_cent(make, api):
    a = make.item()
    o, sh, inv = make.sold([(a, 1, 10)])
    got = api.put(f"/api/invoices/{inv['id']}/funding", json={"funding_amount": 9.505, "funding_discount": 0.495})
    assert got["funding_amount"] == 9.51 and got["funding_discount"] == 0.5


def test_vendor_payment_split_over_two_pos_and_unapply(make, api):
    v = make.vendor()
    a = make.item()
    p1, p2 = make.po(vendor=v, lines=[(a, 1, 60)]), make.po(vendor=v, lines=[(a, 1, 40.5)])
    vp = api.post("/api/vendor-payments/", json={"vendor_id": v["id"], "amount": 100.5})
    vp = api.post(f"/api/vendor-payments/{vp['id']}/apply", json={"po_id": p1["id"], "amount": 60})
    api.post(f"/api/vendor-payments/{vp['id']}/apply", json={"po_id": p2["id"], "amount": 40.51}, expect=400)  # only 40.50 owed
    vp = api.post(f"/api/vendor-payments/{vp['id']}/apply", json={"po_id": p2["id"], "amount": 40.5})
    assert vp["unapplied"] == 0
    api.post(f"/api/purchase-orders/{p1['id']}/payments", json={"amount": 0.01}, expect=400)          # p1 already paid in full
    app1 = next(x for x in vp["applications"] if x["po_id"] == p1["id"])
    vp = api.delete(f"/api/vendor-payments/{vp['id']}/applications/{app1['id']}")
    assert vp["unapplied"] == 60 and api.get(f"/api/purchase-orders/{p1['id']}")["amount_paid"] == 0


def test_vendor_billing_more_than_the_po_can_still_be_paid(make, api):
    a = make.item()
    po = make.po(lines=[(a, 10, 10)])                                       # PO says 100
    po = api.post(f"/api/purchase-orders/{po['id']}/bills", json={"bill_number": "OVER-1", "amount": 104})  # vendor billed 104
    po = api.post(f"/api/purchase-orders/{po['id']}/payments", json={"amount": 104, "vendor_bill_id": po["bills"][0]["id"]})
    assert po["bills"][0]["balance"] == 0


def test_deleting_a_vendor_bill_takes_its_shipping_charge_off_the_po(make, api):
    a = make.item()
    po = make.po(lines=[(a, 10, 10)])
    po = api.post(f"/api/purchase-orders/{po['id']}/bills", json={"bill_number": "SH-1", "amount": 112.5, "shipping_amount": 12.5})
    assert po["order_total"] == 112.5
    po = api.delete(f"/api/purchase-orders/{po['id']}/bills/{po['bills'][0]['id']}")
    assert po["order_total"] == 100 and po["charges_total"] == 0


def test_landed_cost_edit_and_delete_recost_shipped_lots_and_profit(make, api):
    a = make.item()
    po = make.po(lines=[(a, 4, 2)])
    api.post(f"/api/purchase-orders/{po['id']}/mark-ordered")
    api.post(f"/api/purchase-orders/{po['id']}/receive", json={"lines": [{"line_id": po["lines"][0]["id"], "quantity": 4}]})
    o = make.order(lines=[(a, 4, 5)])
    make.ship(o)
    lc = api.post("/api/landed-costs/", json={"description": "Freight", "amount": 2, "po_ids": [po["id"]]})
    assert api.get(f"/api/customer-orders/{o['id']}/profit")["cogs"] == 10        # 4 x (2 + 0.50)
    api.put(f"/api/landed-costs/{lc['id']}", json={"description": "Freight", "cost_type": lc["cost_type"], "amount": 4, "po_ids": [po["id"]]})
    assert api.get(f"/api/customer-orders/{o['id']}/profit")["cogs"] == 12
    api.delete(f"/api/landed-costs/{lc['id']}")
    assert api.get(f"/api/customer-orders/{o['id']}/profit")["cogs"] == 8


def test_quote_lines_follow_the_order_rules(make, api):
    a = make.item()
    c = make.customer()
    api.post("/api/quotes/", json={"customer_id": c["id"], "lines": [{"item_id": a["id"], "quantity": 2.5, "unit_price": 1}]}, expect=422)  # same whole-number rule as orders
    api.post("/api/quotes/", json={"customer_id": c["id"], "lines": [{"item_id": a["id"], "quantity": 2, "unit_price": -1}]}, expect=400)
    q = api.post("/api/quotes/", json={"customer_id": c["id"], "lines": [{"item_id": a["id"], "quantity": 3, "unit_price": 1.0050049}]})
    assert q["lines"][0]["unit_price"] == 1.005 and q["total"] == 3.02


def test_order_lines_refuse_negative_prices(make, api):
    a = make.item()
    c = make.customer()
    api.post("/api/customer-orders/", json={"customer_id": c["id"], "po_number": "NEG-1",
                                            "lines": [{"item_id": a["id"], "quantity": 1, "unit_price": -5}]}, expect=400)
    po = make.po(lines=[(a, 1, 1)])
    api.post(f"/api/purchase-orders/{po['id']}/lines", json={"item_id": a["id"], "quantity": 1, "unit_cost": -2}, expect=400)
