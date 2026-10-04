"""Generic stock: specific nuts draw from a bulk generic nut by transfer; nothing else changes."""


def test_transfer_book_return(make, api):
    bulk = make.item(title="NUT 5/8-11 A194-2H HDG (bulk)", group="Nut", cost=0.05)
    nut = make.item(title="15420-NUT", group="Nut")
    make.stock(bulk, 100000, cost=0.05)
    api.put(f"/api/stock-items/{nut['id']}", json={"parent_item_id": bulk["id"]})
    fam = api.get(f"/api/stock-items/{nut['id']}/family")
    assert fam["parent"]["id"] == bulk["id"] and fam["parent"]["free"] == 100000

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
