"""The other money paths: un-shipping, order profit, vendor invoices with S&H, landed costs."""
from app.services.money import cents


def test_unship_puts_stock_back_and_is_blocked_once_invoiced(make, api):
    a = make.item(price=3)
    make.stock(a, 10)
    o = make.order(lines=[(a, 10, 3)])
    sh = make.ship(o)
    assert api.get(f"/api/stock-items/{a['id']}")["on_hand"] == 0
    inv = make.invoice(sh)
    api.post(f"/api/shipments/{sh['id']}/unship", expect=400)          # an invoice bills it
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "void"})
    api.post(f"/api/shipments/{sh['id']}/unship")
    assert api.get(f"/api/stock-items/{a['id']}")["on_hand"] == 10
    assert api.get(f"/api/customer-orders/{o['id']}")["lines"][0]["shipped_quantity"] == 0


def test_order_profit_is_revenue_minus_lot_cost(make, api):
    a = make.item(price=5)
    make.stock(a, 10, cost=2.25)
    o = make.order(lines=[(a, 10, 5)])
    sh = make.ship(o)
    make.invoice(sh, shipping=15)
    p = api.get(f"/api/customer-orders/{o['id']}/profit")
    assert p["revenue"] == 50 and p["cogs"] == 22.50 and p["gross_profit"] == 27.50
    assert p["other_charges"] == 15


def test_vendor_invoice_with_shipping_adds_a_locked_charge_and_owed_follows_payments(make, api):
    a = make.item()
    po = make.po(lines=[(a, 100, 1.255)])                          # 125.50
    po = api.post(f"/api/purchase-orders/{po['id']}/bills", json={"bill_number": "VB-1", "amount": 150.75, "shipping_amount": 25.25})
    assert po["charges_total"] == 25.25 and po["order_total"] == 150.75
    bill = po["bills"][0]
    assert bill["balance"] == 150.75
    po = api.post(f"/api/purchase-orders/{po['id']}/payments", json={"amount": 100, "vendor_bill_id": bill["id"]})
    assert po["bills"][0]["balance"] == 50.75 and po["amount_paid"] == 100


def test_landed_cost_is_spread_to_the_cent(make, api):
    a, b, c = make.item(), make.item(), make.item()
    po1 = make.po(lines=[(a, 3, 1), (b, 7, 1)])
    po2 = make.po(lines=[(c, 11, 1)])
    lc = api.post("/api/landed-costs/", json={"description": "Ocean freight", "amount": 100.00, "po_ids": [po1["id"], po2["id"]]})
    assert cents(sum(x["amount"] for x in lc["allocations"])) == 100.00   # nothing lost to rounding
    by_qty = {x["quantity"]: x["amount"] for x in lc["allocations"]}
    assert abs(by_qty[7] - 100 * 7 / 21) < 0.011 and abs(by_qty[11] - 100 * 11 / 21) < 0.011
