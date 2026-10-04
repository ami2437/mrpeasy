"""Generic stock: specific nuts draw from a bulk generic nut by transfer; nothing else changes."""


def test_transfer_book_return(make, api):
    bulk = make.item(title="NUT 5/8-11 A194-2H HDG (bulk)", group="Nut", cost=0.05)
    nut = make.item(title="15420-NUT", group="Nut")
    make.stock(bulk, 100000, cost=0.05)
    api.put(f"/api/stock-items/{nut['id']}", json={"parent_item_id": bulk["id"]})
    fam = api.get(f"/api/stock-items/{nut['id']}/family")
    assert fam["sources"][0]["id"] == bulk["id"] and fam["sources"][0]["free"] == 100000
    assert api.get(f"/api/stock-items/{bulk['id']}")["is_generic"]  # linking marks the bulk item generic

    r = api.post(f"/api/stock-items/{nut['id']}/transfer-from-parent", json={"quantity": 2500})
    assert len(r["lots"]) == 1 and r["lots"][0]["lot_code"].endswith("-T1") and r["lots"][0]["quantity"] == 2500
    nut_now, bulk_now = api.get(f"/api/stock-items/{nut['id']}"), api.get(f"/api/stock-items/{bulk['id']}")
    assert nut_now["on_hand"] == 2500 and bulk_now["on_hand"] == 97500
    assert abs(nut_now["cost_price"] - 0.05) < 1e-9  # cost travels with the stock

    # book and ship the transferred nuts like any other stock
    o = make.order(lines=[(nut, 2000, 0.2)])
    make.ship(o)
    assert api.get(f"/api/stock-items/{nut['id']}")["on_hand"] == 500

    # put the rest back; a second transfer gets the next suffix
    api.post(f"/api/stock-items/{nut['id']}/return-to-parent", json={"quantity": 500})
    assert api.get(f"/api/stock-items/{bulk['id']}")["on_hand"] == 98000
    r2 = api.post(f"/api/stock-items/{nut['id']}/transfer-from-parent", json={"quantity": 10})
    assert r2["lots"][0]["lot_code"].endswith("-T2")
    api.post(f"/api/stock-items/{nut['id']}/transfer-from-parent", json={"quantity": 10**7}, expect=400)  # more than the bulk has


def test_parent_rules(make, api):
    a, b, c = make.item(), make.item(), make.item()
    api.put(f"/api/stock-items/{a['id']}", json={"parent_item_id": a["id"]}, expect=400)  # not itself
    api.put(f"/api/stock-items/{b['id']}", json={"parent_item_id": a["id"]})
    api.put(f"/api/stock-items/{c['id']}", json={"parent_item_id": b["id"]}, expect=400)  # one level only
    api.put(f"/api/stock-items/{a['id']}", json={"parent_item_id": c["id"]}, expect=400)  # a generic can't draw from another
    api.post(f"/api/stock-items/{c['id']}/transfer-from-parent", json={"quantity": 1}, expect=400)  # c has no generic


def test_booking_draws_shortfall_from_matching_generic(make, api, client, admin_headers):
    bulk = make.item(title="5/8-11 A194 GR 2H HVY HEX NUT DOM. HDG", group="Nut", cost=0.30)
    api.put(f"/api/stock-items/{bulk['id']}", json={"is_generic": True})
    make.stock(bulk, 50000, cost=0.30)
    nut = make.item(title="5/8-11 A194 2H HVY HEX NUT HDG", group="Nut")
    make.stock(nut, 200, cost=0.10)
    wax = make.item(title="5/8-11 A194 2H HVY HEX NUT HDG WAX DIPPED", group="Nut")
    src = api.post("/api/stock-items/generic-sources", json={"item_ids": [nut["id"], wax["id"], bulk["id"]]})
    mine = {s["id"]: s for s in src[str(nut["id"])]}  # (other tests' generic 5/8 nuts may match too)
    assert mine[bulk["id"]]["match"] == "exact" and mine[bulk["id"]]["free"] == 50000
    assert str(wax["id"]) not in src and str(bulk["id"]) not in src  # wax-dipped is a different part; generic doesn't draw

    o = make.order(lines=[(nut, 1000, 0.5)])
    line = o["lines"][0]["id"]
    api.post(f"/api/customer-orders/{o['id']}/shipments", json={"lines": [{"line_id": line, "quantity": 1000}]}, expect=400)  # short 800
    sh = api.post(f"/api/customer-orders/{o['id']}/shipments", json={"lines": [{"line_id": line, "quantity": 1000, "draw_from_item_id": bulk["id"]}]})
    assert sum(l["quantity"] for l in sh["lines"]) == 1000
    assert api.get(f"/api/stock-items/{bulk['id']}")["on_hand"] == 49200 and api.get(f"/api/stock-items/{nut['id']}")["on_hand"] == 1000
    assert api.get(f"/api/stock-items/{nut['id']}")["parent_item_id"] == bulk["id"]  # remembered for next time
    order = api.get(f"/api/customer-orders/{o['id']}")
    drawn = [b for b in order["lines"][0]["booking_sources"] if b["from_item_code"]]
    assert drawn and drawn[0]["from_item_code"] == bulk["code"] and drawn[0]["quantity"] == 800
    mv = api.get(f"/api/stock-items/{nut['id']}/movements")["movements"]
    t = next(m for m in mv if m["type"] == "transfer")
    assert t["quantity"] == 800 and t["from_item_code"] == bulk["code"] and abs(t["unit_cost"] - 0.30) < 1e-9 and sh["code"] in t["note"]


def test_generic_stock_switch_off(make, api):
    bulk = make.item(title="3/4-10 A194 2H HVY HEX NUT HDG BULK", group="Nut")
    api.put(f"/api/stock-items/{bulk['id']}", json={"is_generic": True})
    make.stock(bulk, 1000)
    nut = make.item(title="3/4-10 A194 2H HVY HEX NUT HDG", group="Nut")
    try:
        api.put("/api/company/", json={"generic_stock_enabled": False})
        assert api.post("/api/stock-items/generic-sources", json={"item_ids": [nut["id"]]}) == {}
        api.post(f"/api/stock-items/{nut['id']}/transfer-from-parent", json={"quantity": 5, "source_id": bulk["id"]}, expect=400)
    finally:
        api.put("/api/company/", json={"generic_stock_enabled": True})
    assert api.post(f"/api/stock-items/{nut['id']}/transfer-from-parent", json={"quantity": 5, "source_id": bulk["id"]})["lots"]


def test_generic_test_data_builds(api):
    r = api.post("/api/test-data/generic-nuts")
    o = api.get(f"/api/customer-orders/{r['order_id']}")
    assert o["status"] == "draft" and len(o["lines"]) == 3
    nut = next(l for l in o["lines"] if l["quantity"] == 2500 and l["unit_price"] == 0)
    src = api.post("/api/stock-items/generic-sources", json={"item_ids": [nut["item_id"]]})[str(nut["item_id"])]
    assert src[0]["code"] == "TEST-GEN-916-NUT" and src[0]["free"] == 20000 and src[0]["cost"] > 0.1125  # freight landed on it
    assert api.post("/api/test-data/generic-nuts")["order_id"] == r["order_id"]  # untouched: reused, not duplicated


def test_bolt_with_nut_never_draws_from_generic_nuts():
    from app.services.stock_transfer import match, spec
    g = spec("5/8-11 A194 GR 2H HVY HEX NUT DOM. HDG")
    assert match(spec("BOLT_HH_5/8-11x3_A325_TYPE1_HDG_w/A194-2H HEX NUT"), g) is None
    assert match(spec("5/8-11 A194 2H HVY HEX NUT HDG"), g) == "exact"
    assert match(spec("5/8-11 A194-2H HEX NUT"), g) == "check"
    assert match(spec("7/8-9 A194 2H HVY HEX NUT HDG WAX DIPPED"), spec("7/8-9 A194 2H HVY HEX NUT HDG")) is None  # wax-dipped differs
    assert match(spec("5/8-11 A194 2H HVY HEX NUT MECH GALV"), g) is None  # mech galv is not HDG


def test_generic_test_order_renewed_once_used(make, api):
    r = api.post("/api/test-data/generic-nuts")
    o = api.get(f"/api/customer-orders/{r['order_id']}")
    api.post(f"/api/customer-orders/{o['id']}/confirm")
    bolt = o["lines"][0]
    api.post(f"/api/customer-orders/{o['id']}/shipments", json={"lines": [{"line_id": bolt["id"], "quantity": 10}]})  # used now
    r2 = api.post("/api/test-data/generic-nuts")
    assert r2["order_created"] and r2["order_id"] != r["order_id"]  # same TEST PO # is fine for test orders
