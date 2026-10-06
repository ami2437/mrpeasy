"""Pre-launch bug sweep (2026-10-05): each test is a bug found by driving the real API, kept so it stays fixed."""
from datetime import datetime, timedelta


def _booked(make, api, qty=10):
    """Two lines (bolt + nut) booked, confirmed and fully picked on one shipment."""
    a, b = make.item(), make.item()
    make.stock(a, 50)
    make.stock(b, 50)
    o = make.order(lines=[(a, qty, 1), (b, qty, 1)])
    sh = api.post(f"/api/customer-orders/{o['id']}/shipments",
                  json={"lines": [{"line_id": l["id"], "quantity": qty} for l in o["lines"]]})
    api.post(f"/api/shipments/{sh['id']}/confirm-booking")
    return o, sh


def test_unbooking_after_packing_needs_packing_accepted_again(make, api):
    # unbooking dropped that line's boxes but packing stayed "accepted": it shipped with a line in no box
    o, sh = _booked(make, api)
    first, second = sh["lines"]
    api.post(f"/api/shipments/{sh['id']}/pick", json={"lines": [{"shipment_line_id": first["id"], "quantity": 10},
                                                                {"shipment_line_id": second["id"], "quantity": 9}]})
    api.post(f"/api/shipments/{sh['id']}/accept-packing")
    sh = api.post(f"/api/shipments/{sh['id']}/unbook", json={"shipment_line_id": second["id"], "quantity": 1})
    assert sh["packed_at"] is None
    api.post(f"/api/shipments/{sh['id']}/ship", expect=400)
    sh = api.post(f"/api/shipments/{sh['id']}/accept-packing")  # boxes come back for the line that lost them
    boxed = {}
    for b in sh["boxes"]:
        boxed[b["order_line_id"]] = boxed.get(b["order_line_id"], 0) + b["quantity_in_box"]
    assert boxed == {first["order_line_id"]: 10, second["order_line_id"]: 9}
    assert api.post(f"/api/shipments/{sh['id']}/ship")["status"] == "shipped"


def test_ship_refuses_boxes_that_dont_add_up(make, api):
    o, sh = _booked(make, api)
    api.post(f"/api/shipments/{sh['id']}/pick", json={"pick_all": True})
    api.post(f"/api/shipments/{sh['id']}/accept-packing")
    from app.config.database import SessionLocal
    from app.models import ShipmentBox
    db = SessionLocal()
    db.query(ShipmentBox).filter(ShipmentBox.shipment_id == sh["id"]).delete()  # a box went missing behind the screen's back
    db.commit()
    db.close()
    api.post(f"/api/shipments/{sh['id']}/ship", expect=400)


def test_delivered_date_with_a_time_zone(make, api):
    # "...Z" from an API client crashed comparing it with the naive ship date
    a = make.item()
    make.stock(a, 10)
    o = make.order(lines=[(a, 5, 1)])
    sh = make.ship(o)
    when = (datetime.utcnow() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sh = api.post(f"/api/shipments/{sh['id']}/delivered", json={"delivered_at": when})
    assert sh["status"] == "delivered" and not sh["delivered_at"].endswith("Z")


def test_ship_date_is_the_business_date(make, api):
    from app.services.clock import business_now
    a = make.item()
    make.stock(a, 10)
    sh = make.ship(make.order(lines=[(a, 5, 1)]))
    assert abs(datetime.fromisoformat(sh["ship_date"]) - business_now()) < timedelta(minutes=5)


def test_line_quantities_must_be_positive(make, api):
    a = make.item()
    o = make.order(lines=[(a, 5, 1)])
    line = o["lines"][0]
    api.put(f"/api/customer-orders/{o['id']}/lines/{line['id']}", json={"quantity": 0}, expect=400)
    v = make.vendor()
    api.post("/api/purchase-orders/", json={"vendor_id": v["id"], "lines": [{"item_id": a["id"], "quantity": 0}]}, expect=400)
    api.post("/api/purchase-orders/", json={"vendor_id": v["id"], "lines": [{"item_id": a["id"], "quantity": -3}]}, expect=400)
    api.post("/api/purchase-orders/", json={"vendor_id": v["id"], "lines": [{"item_id": a["id"], "quantity": 3, "unit_cost": -1}]}, expect=400)
    po = make.po(vendor=v, lines=[(a, 3, 1)])
    api.post(f"/api/purchase-orders/{po['id']}/lines", json={"item_id": a["id"], "quantity": 0}, expect=400)
    api.put(f"/api/purchase-orders/{po['id']}/lines/{po['lines'][0]['id']}", json={"quantity": -1}, expect=400)


def test_blank_required_fields_are_ignored_not_500(make, api):
    a = make.item()
    o = make.order(lines=[(a, 5, 1)])
    c, v = make.customer(), make.vendor()
    assert api.put(f"/api/customer-orders/{o['id']}", json={"customer_id": None})["customer_id"] == o["customer_id"]
    api.put(f"/api/customer-orders/{o['id']}/lines/{o['lines'][0]['id']}", json={"quantity": None, "unit_price": None})
    po = make.po(vendor=v, lines=[(a, 3, 1)])
    assert api.put(f"/api/purchase-orders/{po['id']}", json={"vendor_id": None})["vendor_id"] == v["id"]
    assert api.put(f"/api/customers/{c['id']}", json={"is_active": None})["is_active"] is True
    assert api.put(f"/api/vendors/{v['id']}", json={"is_active": None})["is_active"] is True


def test_void_invoice_stays_void_and_records_who(make, api):
    a = make.item(price=2)
    make.stock(a, 10)
    sh = make.ship(make.order(lines=[(a, 5, 2)]))
    inv = make.invoice(sh)
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"})
    api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": 0.004}, expect=400)  # rounds to $0.00
    inv = api.put(f"/api/invoices/{inv['id']}/status", json={"status": "void", "reason": "test"})
    assert inv["voided_by"] == "admin"
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"}, expect=400)
    assert make.invoice(sh)["status"] == "draft"  # its shipment is billed on a new invoice instead


def test_on_hand_correction_cant_go_negative(make, api):
    a = make.item()
    make.stock(a, 5)
    api.put(f"/api/stock-items/{a['id']}", json={"on_hand": -2}, expect=400)
    assert api.put(f"/api/stock-items/{a['id']}", json={"on_hand": 0})["on_hand"] == 0


def test_invoice_funding_cant_be_negative(make, api):
    a = make.item(price=2)
    make.stock(a, 10)
    inv = make.invoice(make.ship(make.order(lines=[(a, 5, 2)])))
    api.put(f"/api/invoices/{inv['id']}/funding", json={"funding_amount": -1}, expect=400)
    assert api.put(f"/api/invoices/{inv['id']}/funding", json={"funding_amount": 8, "funding_discount": 0.4})["funding_amount"] == 8
