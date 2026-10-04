"""Cancel / delete an order that went all the way: the plan lists its invoices (void, delete) and shipments
(un-ship, delete), the steps run in order, stock comes back, and invoices can be deleted only when safe."""


def test_remove_a_shipped_and_invoiced_order(api, client, admin_headers, make):
    a = make.item(price=3)
    make.stock(a, 50)
    o = make.order(lines=[(a, 50, 3)])
    sh = make.ship(o)
    inv = make.invoice(sh)
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"})

    plan = api.get(f"/api/customer-orders/{o['id']}/removal-plan")
    assert plan["invoices"][0]["steps"] == ["void", "delete"] and plan["shipments"][0]["steps"] == ["unship", "delete"]
    assert not plan["can_delete_now"]

    # a sent invoice can't just be deleted, and one with payments can't even be planned away
    assert client.delete(f"/api/invoices/{inv['id']}", headers=admin_headers).status_code == 400

    # the pop-up's steps, in order
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "void", "reason": "Order cancelled"})
    assert api.get(f"/api/invoices/{inv['id']}")["void_reason"] == "Order cancelled"
    api.delete(f"/api/invoices/{inv['id']}")
    api.post(f"/api/shipments/{sh['id']}/unship", json={"reason": "Order cancelled", "customer_notified": True})
    api.delete(f"/api/shipments/{sh['id']}")
    api.post(f"/api/customer-orders/{o['id']}/cancel")
    api.delete(f"/api/customer-orders/{o['id']}")
    it = api.get(f"/api/stock-items/{a['id']}")
    assert it["on_hand"] == 50 and it["booked"] == 0                      # everything came back
    assert client.get(f"/api/customer-orders/{o['id']}", headers=admin_headers).status_code == 404


def test_cancelled_order_with_a_void_invoice_deletes(api, make):
    """C89136: invoice voided, shipment deleted, order cancelled -- deleting the order takes the void invoice with it."""
    a = make.item(price=2)
    make.stock(a, 10)
    o = make.order(lines=[(a, 10, 2)])
    sh = make.ship(o)
    inv = make.invoice(sh)
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "void"})
    api.post(f"/api/shipments/{sh['id']}/unship", json={})
    api.delete(f"/api/shipments/{sh['id']}")
    api.post(f"/api/customer-orders/{o['id']}/cancel")
    assert api.get(f"/api/customer-orders/{o['id']}/removal-plan")["can_delete_now"]
    api.delete(f"/api/customer-orders/{o['id']}")


def test_invoice_with_payments_is_blocked(api, client, admin_headers, make):
    _o, sh, inv = make.sold([(make.item(price=5), 4, 5)])
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"})
    api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": 5})
    plan = api.get(f"/api/customer-orders/{inv['order_id']}/removal-plan")
    assert plan["blocked"] and "payment" in plan["blocked"][0]
    assert client.delete(f"/api/invoices/{inv['id']}", headers=admin_headers).status_code == 400


def test_deleted_invoice_restores_whole(api, client, admin_headers, make):
    _o, sh, inv = make.sold([(make.item(price=5), 4, 5)])
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "void"})
    api.delete(f"/api/invoices/{inv['id']}")
    entry = next(e for e in client.get("/api/recycle-bin", headers=admin_headers).json() if inv["code"] in e["label"])
    assert client.post(f"/api/recycle-bin/{entry['id']}/restore", headers=admin_headers).status_code == 200
    back = api.get(f"/api/invoices/{inv['id']}")
    assert back["status"] == "void" and len(back["lines"]) == len(inv["lines"]) and back["shipment_ids"] == [sh["id"]]


def test_restored_open_shipment_is_booked_again(api, client, admin_headers, make):
    """Restore an open shipment (or one undone by Cancel / Delete Order): its stock is booked again and a cancelled
    order is reopened -- refused only when the stock was used meanwhile."""
    a = make.item(price=2)
    make.stock(a, 30)
    o = make.order(lines=[(a, 30, 2)])
    sh = make.ship(o)
    api.post(f"/api/shipments/{sh['id']}/unship", json={})        # what Cancel / Delete Order does
    api.delete(f"/api/shipments/{sh['id']}")
    api.post(f"/api/customer-orders/{o['id']}/cancel")
    assert api.get(f"/api/stock-items/{a['id']}")["booked"] == 0
    entry = next(e for e in client.get("/api/recycle-bin", headers=admin_headers).json() if sh["code"] in e["label"])
    r = client.post(f"/api/recycle-bin/{entry['id']}/restore", headers=admin_headers)
    assert r.status_code == 200 and any("reopened" in n for n in r.json()["notes"]), r.text
    assert api.get(f"/api/stock-items/{a['id']}")["booked"] == 30
    assert api.get(f"/api/customer-orders/{o['id']}")["status"] == "confirmed"
    assert api.get(f"/api/shipments/{sh['id']}")["status"] == "new"


def test_restore_refused_when_stock_is_gone(api, client, admin_headers, make):
    a = make.item(price=2)
    make.stock(a, 10)
    o = make.order(lines=[(a, 10, 2)])
    sh = api.post(f"/api/customer-orders/{o['id']}/shipments", json={"lines": [{"line_id": o["lines"][0]["id"], "quantity": 10}]})
    api.delete(f"/api/shipments/{sh['id']}")
    o2 = make.order(lines=[(a, 10, 2)])
    api.post(f"/api/customer-orders/{o2['id']}/shipments", json={"lines": [{"line_id": o2["lines"][0]["id"], "quantity": 10}]})  # takes it
    entry = next(e for e in client.get("/api/recycle-bin", headers=admin_headers).json() if sh["code"] in e["label"])
    r = client.post(f"/api/recycle-bin/{entry['id']}/restore", headers=admin_headers)
    assert r.status_code == 400 and "used since" in r.json()["detail"]
