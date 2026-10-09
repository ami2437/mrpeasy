"""Stock in / booked / out, customer order rules, and purchasing totals."""


def item_state(api, item):
    i = api.get(f"/api/stock-items/{item['id']}")
    return i["on_hand"], i["booked"], i["available"]


# ---------------- stock ----------------
def test_receive_book_ship_moves_stock(make, api):
    a = make.item()
    make.stock(a, 100, cost=1.5)
    assert item_state(api, a) == (100, 0, 100)
    o = make.order(lines=[(a, 30, 2)])
    sh = api.post(f"/api/customer-orders/{o['id']}/shipments", json={"lines": [{"line_id": o["lines"][0]["id"], "quantity": 30}]})
    assert item_state(api, a) == (100, 30, 70)                 # booked, not gone yet
    api.post(f"/api/shipments/{sh['id']}/confirm-booking")
    api.post(f"/api/shipments/{sh['id']}/pick", json={"pick_all": True})
    assert item_state(api, a) == (100, 30, 70)                 # picked, not shipped yet
    api.post(f"/api/shipments/{sh['id']}/ship", expect=400)    # packing not accepted
    api.post(f"/api/shipments/{sh['id']}/accept-packing")
    assert api.post(f"/api/shipments/{sh['id']}/ship")["status"] == "shipped"
    assert item_state(api, a) == (70, 0, 70)                   # shipped = left on-hand


def test_accept_packing_proposes_boxes_from_pack_size(make, api):
    a = make.item()
    api.put(f"/api/stock-items/{a['id']}", json={"default_pack_size": 40})
    make.stock(a, 100)
    o = make.order(lines=[(a, 90, 1)])
    sh = api.post(f"/api/customer-orders/{o['id']}/shipments", json={"lines": [{"line_id": o["lines"][0]["id"], "quantity": 90}]})
    api.post(f"/api/shipments/{sh['id']}/confirm-booking")
    api.post(f"/api/shipments/{sh['id']}/pick", json={"lines": [{"shipment_line_id": sh["lines"][0]["id"], "quantity": 50}]})
    sh = api.post(f"/api/shipments/{sh['id']}/accept-packing")
    assert [b["quantity_in_box"] for b in sh["boxes"]] == [40, 40, 10] and sh["packed_by"]
    api.post(f"/api/shipments/{sh['id']}/ship", expect=400)    # 40 still to pick


def test_cannot_book_more_than_available(make, api):
    a = make.item()
    make.stock(a, 10)
    o = make.order(lines=[(a, 25, 1)])
    api.post(f"/api/customer-orders/{o['id']}/shipments", json={"lines": [{"line_id": o["lines"][0]["id"], "quantity": 25}]}, expect=400)
    assert item_state(api, a) == (10, 0, 10)


def test_cancelling_a_shipment_releases_its_booking(make, api):
    a = make.item()
    make.stock(a, 10)
    o = make.order(lines=[(a, 6, 1)])
    sh = api.post(f"/api/customer-orders/{o['id']}/shipments", json={"lines": [{"line_id": o["lines"][0]["id"], "quantity": 6}]})
    api.post(f"/api/shipments/{sh['id']}/cancel")
    assert item_state(api, a) == (10, 0, 10)


def test_order_status_follows_shipping(make, api):
    a = make.item()
    make.stock(a, 10)
    o = make.order(lines=[(a, 10, 1)])
    make.ship(o, {o["lines"][0]["id"]: 4})
    assert api.get(f"/api/customer-orders/{o['id']}")["status"] == "confirmed"
    make.ship(api.get(f"/api/customer-orders/{o['id']}"))
    assert api.get(f"/api/customer-orders/{o['id']}")["status"] == "shipped"


# ---------------- order rules ----------------
def test_duplicate_customer_po_is_flagged_unless_allowed(make, api):
    c, a = make.customer(), make.item()
    make.order(customer=c, lines=[(a, 1, 1)], po_number="SAMEPO-1")
    r = api.post("/api/customer-orders/", json={"customer_id": c["id"], "po_number": "samepo-1 ", "lines": [{"item_id": a["id"], "quantity": 1, "unit_price": 1}]}, expect=409)
    assert r["detail"].startswith("DUPLICATE_PO|")
    ok = api.post("/api/customer-orders/", json={"customer_id": c["id"], "po_number": "SAMEPO-1", "allow_duplicate": True,
                                                 "lines": [{"item_id": a["id"], "quantity": 1, "unit_price": 1}]})
    assert ok["duplicate_po_ok"]


def test_line_order_replace_and_nut_placement(make, api):
    bolt, washer, other = make.item(code=make_code("BLT")), make.item(), make.item()
    nut = make.item(code=f"{bolt['code']}-NUT", group="Nut", price=0)
    o = make.order(lines=[(bolt, 10, 2), (washer, 10, 1)], confirm=False)
    o = api.post(f"/api/customer-orders/{o['id']}/lines", json={"item_id": nut["id"], "quantity": 10, "unit_price": 0})
    assert [l["item_id"] for l in o["lines"]] == [bolt["id"], nut["id"], washer["id"]]      # nut lands under its bolt
    ids = [l["id"] for l in o["lines"]][::-1]
    nos = [l["line_no"] for l in o["lines"]][::-1]
    o = api.put(f"/api/customer-orders/{o['id']}/line-order", json={"line_ids": ids})
    assert [l["id"] for l in o["lines"]] == ids
    api.put(f"/api/customer-orders/{o['id']}/line-order", json={"line_ids": ids[:1]}, expect=400)
    o = api.put(f"/api/customer-orders/{o['id']}/lines/{ids[0]}", json={"item_id": other["id"]})
    assert o["lines"][0]["item_id"] == other["id"]
    assert [l["line_no"] for l in o["lines"]] == [1, 2, 3]  # the # follows the place on the order (links are by line id)


def test_line_numbers_follow_the_order_everywhere(make, api, client, admin_headers):
    import io
    from pypdf import PdfReader
    a, b, c = make.item(price=1), make.item(price=2), make.item(price=3)
    for it in (a, b, c):
        make.stock(it, 5)
    o = make.order(lines=[(a, 5, 1), (b, 5, 2), (c, 5, 3)])
    ids = [l["id"] for l in o["lines"]]
    o = api.put(f"/api/customer-orders/{o['id']}/line-order", json={"line_ids": [ids[2], ids[0], ids[1]]})  # c to the top
    assert [(l["line_no"], l["item_id"]) for l in o["lines"]] == [(1, c["id"]), (2, a["id"]), (3, b["id"])]
    sh = make.ship(o)
    inv = make.invoice(sh)
    text = lambda r: " ".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
    for t in (text(client.get(f"/api/shipments/{sh['id']}/packing-list.pdf", headers=admin_headers)),
              text(client.get(f"/api/invoices/{inv['id']}/pdf", headers=admin_headers))):
        assert t.index(c["code"]) < t.index(a["code"]) < t.index(b["code"])       # documents list lines as the order does
    assert [l["order_line_no"] for l in api.get(f"/api/invoices/{inv['id']}")["lines"] if l["order_line_no"]] == [1, 2, 3]
    # removing a line closes the gap
    o2 = make.order(lines=[(a, 1, 1), (b, 1, 1), (c, 1, 1)], confirm=False)
    o2 = api.delete(f"/api/customer-orders/{o2['id']}/lines/{o2['lines'][0]['id']}")
    o2 = api.get(f"/api/customer-orders/{o2['id']}") if not isinstance(o2, dict) else o2
    assert [l["line_no"] for l in o2["lines"]] == [1, 2]


def test_shipped_line_item_cannot_be_replaced(make, api):
    a, b = make.item(), make.item()
    make.stock(a, 5)
    o = make.order(lines=[(a, 5, 1)])
    make.ship(o)
    api.put(f"/api/customer-orders/{o['id']}/lines/{o['lines'][0]['id']}", json={"item_id": b["id"]}, expect=400)


def test_only_cancelled_unshipped_orders_can_be_deleted(make, api):
    a = make.item()
    o = make.order(lines=[(a, 1, 1)])
    api.delete(f"/api/customer-orders/{o['id']}", expect=400)
    api.post(f"/api/customer-orders/{o['id']}/cancel")
    api.delete(f"/api/customer-orders/{o['id']}", expect=204)
    api.get(f"/api/customer-orders/{o['id']}", expect=404)


# ---------------- purchasing ----------------
def test_po_totals_round_per_line_and_include_charges(make, api):
    a, b = make.item(), make.item()
    po = make.po(lines=[(a, 650, 2.55588), (b, 3, 0.335)])
    assert po["lines_total"] == 1662.33                         # 1661.32 + 1.01
    po = api.post(f"/api/purchase-orders/{po['id']}/charges", json={"charge_type": "Freight", "amount": 187.81})
    assert po["order_total"] == 1850.14


def test_po_receive_then_cancel_rules(make, api):
    a = make.item()
    po = make.po(lines=[(a, 10, 1)])
    api.post(f"/api/purchase-orders/{po['id']}/mark-ordered")
    api.post(f"/api/purchase-orders/{po['id']}/receive", json={"lines": [{"line_id": po["lines"][0]["id"], "quantity": 4}]})
    assert api.get(f"/api/purchase-orders/{po['id']}")["status"] == "partially_received"
    api.post(f"/api/purchase-orders/{po['id']}/cancel", expect=400)   # stock already in


def make_code(prefix):
    from tests.builders import uid
    return uid(prefix)


def test_draft_order_must_be_confirmed_before_shipment(make, api):
    a = make.item()
    make.stock(a, 5)
    o = make.order(lines=[(a, 5, 1)], confirm=False)
    body = {"lines": [{"line_id": o["lines"][0]["id"], "quantity": 5}]}
    r = api.post(f"/api/customer-orders/{o['id']}/shipments", json=body, expect=400)
    assert "isn't confirmed" in r["detail"]
    api.post(f"/api/customer-orders/{o['id']}/confirm")
    assert api.post(f"/api/customer-orders/{o['id']}/shipments", json=body)["code"].startswith("SH")
