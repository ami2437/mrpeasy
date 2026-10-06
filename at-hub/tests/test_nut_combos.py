"""Bolts and nuts sent together as assembled units (ShipmentCombo): one row of N on the packing, labels and packing list,
while stock, lots and each order line's shipped quantity stay per item -- and the next shipment can send them apart."""
import csv
import io

from tests.builders import uid


def _pair(make, bolt_qty=800, nut_qty=800, nut_price=0, stock=2000, bolt_title="1/2-13 x 4 stud w/ nut"):
    code = uid("CB")
    bolt = make.item(code=code, title=bolt_title, price=3)
    nut = make.item(code=f"{code}-NUT", title="1/2-13 heavy hex nut", group="Nut", price=nut_price)
    make.stock(bolt, stock)
    make.stock(nut, stock * 2)
    o = make.order(lines=[(bolt, bolt_qty, 3), (nut, nut_qty, nut_price)])
    return bolt, nut, o


def _book(api, o, quantities):
    return api.post(f"/api/customer-orders/{o['id']}/shipments",
                    json={"lines": [{"line_id": lid, "quantity": q} for lid, q in quantities.items() if q > 0]})


def _lines(o):
    return o["lines"][0], o["lines"][1]


def _combine(api, sh, lead, member, qty, ratio=1, expect=(200,)):
    return api.post(f"/api/shipments/{sh['id']}/combos", json={"lead_line_id": lead["id"], "member_line_id": member["id"],
                                                               "quantity": qty, "ratio": ratio}, expect=expect)


def _finish(api, sh):
    api.post(f"/api/shipments/{sh['id']}/confirm-booking")
    api.post(f"/api/shipments/{sh['id']}/pick", json={"pick_all": True})
    api.post(f"/api/shipments/{sh['id']}/accept-packing")
    return api.post(f"/api/shipments/{sh['id']}/ship")


def _packing_rows(api, sh):
    r = api.get(f"/api/shipments/{sh['id']}/packing-list.csv?part=lines&notes=true")
    return list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))


def test_suggests_only_free_nut_of_the_same_bolt(make, api):
    bolt, nut, o = _pair(make)
    b, n = _lines(o)
    sh = _book(api, o, {b["id"]: 400, n["id"]: 400})
    sug = api.get(f"/api/shipments/{sh['id']}/combo-suggestions")
    assert [(s["lead_line_id"], s["member_line_id"], s["quantity"], s["ratio"]) for s in sug] == [(b["id"], n["id"], 400, 1)]
    # a nut we charge for is never suggested
    _, _, paid = _pair(make, nut_price=0.25)
    pb, pn = _lines(paid)
    sh2 = _book(api, paid, {pb["id"]: 10, pn["id"]: 10})
    assert api.get(f"/api/shipments/{sh2['id']}/combo-suggestions") == []
    # already combined -> no longer suggested
    _combine(api, sh, b, n, 400)
    assert api.get(f"/api/shipments/{sh['id']}/combo-suggestions") == []


def test_suggestion_ratio_two_nuts_per_bolt(make, api):
    bolt, nut, o = _pair(make, bolt_qty=100, nut_qty=200)
    b, n = _lines(o)
    sh = _book(api, o, {b["id"]: 100, n["id"]: 150})
    [s] = api.get(f"/api/shipments/{sh['id']}/combo-suggestions")
    assert s["ratio"] == 2 and s["quantity"] == 75  # only 75 full sets booked


def test_combined_ships_as_one_row_but_counts_both_lines(make, api):
    bolt, nut, o = _pair(make)
    b, n = _lines(o)
    before = {i["code"]: api.get(f"/api/stock-items/{i['id']}")["on_hand"] for i in (bolt, nut)}
    sh = _book(api, o, {b["id"]: 400, n["id"]: 400})
    sh = _combine(api, sh, b, n, 400)
    assert sh["combos"][0]["quantity"] == 400 and sh["combos"][0]["member_quantity"] == 400
    api.post(f"/api/shipments/{sh['id']}/confirm-booking")
    api.post(f"/api/shipments/{sh['id']}/pick", json={"pick_all": True})  # both items are still picked
    sh = api.post(f"/api/shipments/{sh['id']}/accept-packing")
    assert {x["order_line_id"] for x in sh["boxes"]} == {b["id"]}  # the nut rides in the bolt's boxes
    assert sum(x["quantity_in_box"] for x in sh["boxes"]) == 400
    sh = api.post(f"/api/shipments/{sh['id']}/ship")
    assert sh["status"] == "shipped"
    after = {i["code"]: api.get(f"/api/stock-items/{i['id']}")["on_hand"] for i in (bolt, nut)}
    assert before[bolt["code"]] - after[bolt["code"]] == 400 and before[nut["code"]] - after[nut["code"]] == 400  # both leave stock
    order = api.get(f"/api/customer-orders/{o['id']}")
    ob, on = order["lines"]
    assert ob["shipped_quantity"] == 400 and on["shipped_quantity"] == 400
    assert ob["shipments"][0]["combined"]["role"] == "lead" and on["shipments"][0]["combined"]["role"] == "member"
    assert on["shipments"][0]["combined"]["with_line_no"] == ob["line_no"]
    rows = _packing_rows(api, sh)
    assert len(rows) == 1 and rows[0]["Part #"] == bolt["code"] and rows[0]["Qty shipped"] == "400"
    assert "Bolts and nuts combined" in rows[0]["Line notes"] and nut["code"] in rows[0]["Line notes"]


def test_next_shipment_can_go_separately(make, api):
    bolt, nut, o = _pair(make)
    b, n = _lines(o)
    first = _book(api, o, {b["id"]: 400, n["id"]: 400})
    _combine(api, first, b, n, 400)
    _finish(api, first)
    second = _finish(api, _book(api, o, {b["id"]: 400, n["id"]: 400}))  # no combo this time
    assert second["combos"] == [] and {x["order_line_id"] for x in second["boxes"]} == {b["id"], n["id"]}
    assert len(_packing_rows(api, second)) == 2
    order = api.get(f"/api/customer-orders/{o['id']}")
    assert order["status"] == "shipped" and all(l["shipped_quantity"] == 800 for l in order["lines"])
    tags = {s["shipment_id"]: s["combined"] for s in order["lines"][1]["shipments"]}
    assert tags[first["id"]]["role"] == "member" and tags[second["id"]] is None


def test_partial_combine_leaves_the_rest_as_its_own_row(make, api):
    bolt, nut, o = _pair(make)
    b, n = _lines(o)
    sh = _book(api, o, {b["id"]: 400, n["id"]: 500})
    _combine(api, sh, b, n, 400)
    sh = _finish(api, sh)
    boxed = {}
    for x in sh["boxes"]:
        boxed[x["order_line_id"]] = boxed.get(x["order_line_id"], 0) + x["quantity_in_box"]
    assert boxed == {b["id"]: 400, n["id"]: 100}
    rows = {r["Part #"]: r for r in _packing_rows(api, sh)}
    assert rows[nut["code"]]["Qty shipped"] == "100" and "Plus 400 sent assembled" in rows[nut["code"]]["Line notes"]
    assert api.get(f"/api/customer-orders/{o['id']}")["lines"][1]["shipped_quantity"] == 500  # the order still counts all 500


def test_bolt_part_combined_shows_of(make, api):
    bolt, nut, o = _pair(make)
    b, n = _lines(o)
    sh = _book(api, o, {b["id"]: 500, n["id"]: 400})
    _combine(api, sh, b, n, 400)
    rows = _packing_rows(api, _finish(api, sh))
    assert len(rows) == 1 and rows[0]["Qty shipped"] == "500" and "400 of 500" in rows[0]["Line notes"]


def test_two_nuts_per_bolt(make, api):
    bolt, nut, o = _pair(make, bolt_qty=100, nut_qty=200)
    b, n = _lines(o)
    sh = _book(api, o, {b["id"]: 100, n["id"]: 200})
    _combine(api, sh, b, n, 100, ratio=2)
    sh = _finish(api, sh)
    assert {x["order_line_id"] for x in sh["boxes"]} == {b["id"]}
    rows = _packing_rows(api, sh)
    assert len(rows) == 1 and "2 per bolt" in rows[0]["Line notes"]
    lines = api.get(f"/api/customer-orders/{o['id']}")["lines"]
    assert lines[1]["shipped_quantity"] == 200 and lines[1]["shipments"][0]["combined"]["quantity"] == 200


def test_combine_rules(make, api):
    bolt, nut, o = _pair(make)
    b, n = _lines(o)
    sh = _book(api, o, {b["id"]: 400, n["id"]: 300})
    _combine(api, sh, b, n, 400, expect=400)  # more sets than nuts booked
    _combine(api, sh, b, b, 10, expect=400)  # same line twice
    _combine(api, sh, b, n, 1.5, expect=422)  # whole sets only
    _combine(api, sh, b, n, 0, expect=422)
    _combine(api, sh, b, n, 10, ratio=0, expect=422)
    other = make.order(lines=[(bolt, 5, 3)])
    api.post(f"/api/shipments/{sh['id']}/combos", json={"lead_line_id": other["lines"][0]["id"], "member_line_id": n["id"],
                                                        "quantity": 1}, expect=400)  # line not on this shipment
    _combine(api, sh, b, n, 300)
    _combine(api, sh, n, b, 300, expect=400)  # already in a combo
    sh = _combine(api, sh, b, n, 200)  # same pair: changes the quantity
    assert len(sh["combos"]) == 1 and sh["combos"][0]["quantity"] == 200
    shipped = _finish(api, sh)
    _combine(api, shipped, b, n, 100, expect=400)  # shipped: too late
    api.delete(f"/api/shipments/{sh['id']}/combos/{shipped['combos'][0]['id']}", expect=400)


def test_combining_after_packing_needs_a_recheck(make, api):
    bolt, nut, o = _pair(make)
    b, n = _lines(o)
    sh = _book(api, o, {b["id"]: 400, n["id"]: 400})
    api.post(f"/api/shipments/{sh['id']}/confirm-booking")
    api.post(f"/api/shipments/{sh['id']}/pick", json={"pick_all": True})
    sh = api.post(f"/api/shipments/{sh['id']}/accept-packing")
    assert sh["packed_at"] and {x["order_line_id"] for x in sh["boxes"]} == {b["id"], n["id"]}
    sh = _combine(api, sh, b, n, 400)
    assert sh["packed_at"] is None and all(x["order_line_id"] != n["id"] for x in sh["boxes"])
    api.post(f"/api/shipments/{sh['id']}/ship", expect=400)  # packing must be accepted again
    # boxes still naming the nut line are refused: it has nothing of its own to box
    bad = [{"item_id": bolt["id"], "order_line_id": b["id"], "box_number": 1, "quantity_in_box": 400},
           {"item_id": nut["id"], "order_line_id": n["id"], "box_number": 2, "quantity_in_box": 400}]
    api.put(f"/api/shipments/{sh['id']}/boxes", json={"boxes": bad}, expect=400)
    api.put(f"/api/shipments/{sh['id']}/boxes", json={"boxes": bad[:1]})
    api.post(f"/api/shipments/{sh['id']}/accept-packing")
    assert api.post(f"/api/shipments/{sh['id']}/ship")["status"] == "shipped"


def test_split_again_brings_the_nut_back(make, api):
    bolt, nut, o = _pair(make)
    b, n = _lines(o)
    sh = _combine(api, _book(api, o, {b["id"]: 400, n["id"]: 400}), b, n, 400)
    api.post(f"/api/shipments/{sh['id']}/confirm-booking")
    api.post(f"/api/shipments/{sh['id']}/pick", json={"pick_all": True})
    sh = api.post(f"/api/shipments/{sh['id']}/accept-packing")
    sh = api.delete(f"/api/shipments/{sh['id']}/combos/{sh['combos'][0]['id']}")
    assert sh["combos"] == [] and sh["packed_at"] is None
    assert sh["id"] in [x["id"] for x in api.get("/api/shipments/unpacked/list")]  # the nut needs boxes again
    sh = api.post(f"/api/shipments/{sh['id']}/accept-packing")
    assert {x["order_line_id"] for x in sh["boxes"]} == {b["id"], n["id"]}


def test_unbooking_shrinks_or_drops_the_combo(make, api):
    bolt, nut, o = _pair(make)
    b, n = _lines(o)
    sh = _combine(api, _book(api, o, {b["id"]: 400, n["id"]: 400}), b, n, 400)
    sh = api.post(f"/api/shipments/{sh['id']}/unbook", json={"order_line_id": n["id"], "quantity": 150})
    assert sh["combos"][0]["quantity"] == 250
    sh = api.post(f"/api/shipments/{sh['id']}/unbook", json={"order_line_id": n["id"], "quantity": 250})
    assert sh["combos"] == [] and sh["status"] in ("new", "ready")  # the nut line is gone; no full set left
    sh = api.post(f"/api/shipments/{sh['id']}/unbook-all")
    assert sh["status"] == "cancelled" and sh["combos"] == []


def test_short_pick_of_the_nut_shrinks_the_combo(make, api):
    bolt, nut, o = _pair(make)
    b, n = _lines(o)
    sh = _combine(api, _book(api, o, {b["id"]: 400, n["id"]: 400}), b, n, 400)
    api.post(f"/api/shipments/{sh['id']}/confirm-booking")
    ln = {l["order_line_id"]: l["id"] for l in sh["lines"]}
    sh = api.post(f"/api/shipments/{sh['id']}/pick", json={"lines": [{"shipment_line_id": ln[b["id"]], "quantity": 400},
                                                                      {"shipment_line_id": ln[n["id"]], "quantity": 300}], "unbook_rest": True})
    assert sh["combos"][0]["quantity"] == 300
    sh = api.post(f"/api/shipments/{sh['id']}/accept-packing")
    boxed = {}
    for x in sh["boxes"]:
        boxed[x["order_line_id"]] = boxed.get(x["order_line_id"], 0) + x["quantity_in_box"]
    assert boxed == {b["id"]: 400}  # 300 assembled + 100 bare bolts, all in the bolt's boxes
    assert api.post(f"/api/shipments/{sh['id']}/ship")["status"] == "shipped"


def test_cancel_and_delete_take_the_combo_with_them(make, api):
    bolt, nut, o = _pair(make)
    b, n = _lines(o)
    sh = _combine(api, _book(api, o, {b["id"]: 400, n["id"]: 400}), b, n, 400)
    sh = api.post(f"/api/shipments/{sh['id']}/cancel")
    assert sh["combos"] == []
    assert all(s["combined"] is None for l in api.get(f"/api/customer-orders/{o['id']}")["lines"] for s in l["shipments"])
    sh2 = _combine(api, _book(api, o, {b["id"]: 10, n["id"]: 10}), b, n, 10)
    api.delete(f"/api/shipments/{sh2['id']}")
    api.get(f"/api/shipments/{sh2['id']}", expect=404)


def test_undo_ship_keeps_the_combo(make, api):
    bolt, nut, o = _pair(make)
    b, n = _lines(o)
    sh = _finish(api, _combine(api, _book(api, o, {b["id"]: 400, n["id"]: 400}), b, n, 400))
    sh = api.post(f"/api/shipments/{sh['id']}/unship")
    assert sh["status"] == "new" and sh["combos"][0]["quantity"] == 400
    assert all(l["shipped_quantity"] == 0 for l in api.get(f"/api/customer-orders/{o['id']}")["lines"])
    sh = _finish(api, sh)
    assert sh["status"] == "shipped" and {x["order_line_id"] for x in sh["boxes"]} == {b["id"]}


def test_invoice_free_nut_and_charged_nut(make, api):
    bolt, nut, o = _pair(make)
    b, n = _lines(o)
    sh = _finish(api, _combine(api, _book(api, o, {b["id"]: 400, n["id"]: 400}), b, n, 400))
    inv = make.invoice(sh)
    billed = {l["order_line_id"]: l["quantity"] for l in inv["lines"] if l.get("order_line_id")}
    assert billed.get(b["id"]) == 400 and billed.get(n["id"], 0) in (0, 400)  # the bolt bills 400; a $0 nut bills nothing
    assert abs(inv["total"] - 1200) < 0.01
    # a nut we charge for, combined by hand, still bills as its own line
    _, _, paid = _pair(make, nut_price=0.25)
    pb, pn = _lines(paid)
    sh2 = _finish(api, _combine(api, _book(api, paid, {pb["id"]: 100, pn["id"]: 100}), pb, pn, 100))
    inv2 = make.invoice(sh2)
    billed2 = {l["order_line_id"]: l["quantity"] for l in inv2["lines"] if l.get("order_line_id")}
    assert billed2 == {pb["id"]: 100, pn["id"]: 100} and abs(inv2["total"] - 325) < 0.01


def test_pallet_label_lists_the_bolt_only(make, api):
    from app.config.database import SessionLocal
    from app.models import Shipment
    from app.services.doc_context import pallet_label_contexts
    bolt, nut, o = _pair(make)
    b, n = _lines(o)
    sh = _combine(api, _book(api, o, {b["id"]: 400, n["id"]: 400}), b, n, 400)
    api.post(f"/api/shipments/{sh['id']}/confirm-booking")
    api.post(f"/api/shipments/{sh['id']}/pick", json={"pick_all": True})
    api.put(f"/api/shipments/{sh['id']}/boxes", json={"boxes": [
        {"item_id": bolt["id"], "order_line_id": b["id"], "box_number": 1, "quantity_in_box": 400, "pallet_number": "1"}]})
    with SessionLocal() as db:
        [ctx] = pallet_label_contexts(db, db.get(Shipment, sh["id"]))
        assert ctx["pallet_rows"][0]["items"] == bolt["code"]


def test_assembled_packing_remembered_apart_from_bare_bolts(make, api):
    bolt, nut, o = _pair(make, stock=3000)
    b, n = _lines(o)
    sh = _combine(api, _book(api, o, {b["id"]: 400, n["id"]: 400}), b, n, 400)
    api.post(f"/api/shipments/{sh['id']}/confirm-booking")
    api.post(f"/api/shipments/{sh['id']}/pick", json={"pick_all": True})
    api.put(f"/api/shipments/{sh['id']}/boxes", json={"boxes": [
        {"item_id": bolt["id"], "order_line_id": b["id"], "box_number": i + 1, "quantity_in_box": 50, "pack_size": 50} for i in range(8)]})
    api.post(f"/api/shipments/{sh['id']}/accept-packing")
    api.post(f"/api/shipments/{sh['id']}/ship")
    # bare bolts on the next shipment don't pick up the assembled size; assembled ones do
    sh2 = _book(api, o, {b["id"]: 100, n["id"]: 100})
    sug = api.get(f"/api/shipments/pack-suggestions?ids={sh2['id']}")[str(sh2["id"])]
    assert (sug[str(b["id"])] or {}).get("size") != 50
    _combine(api, sh2, b, n, 100)
    sug = api.get(f"/api/shipments/pack-suggestions?ids={sh2['id']}")[str(sh2["id"])]
    assert sug[str(b["id"])]["size"] == 50 and sug[str(b["id"])]["label"].startswith("Assembled")


def test_combining_bumps_the_shipment_version(make, api):
    bolt, nut, o = _pair(make)
    b, n = _lines(o)
    sh = _book(api, o, {b["id"]: 10, n["id"]: 10})
    after = _combine(api, sh, b, n, 10)
    assert after["row_version"] > sh["row_version"]


def test_recycle_bin_brings_the_combo_back(make, api):
    bolt, nut, o = _pair(make)
    b, n = _lines(o)
    sh = _combine(api, _book(api, o, {b["id"]: 300, n["id"]: 300}), b, n, 300)
    api.delete(f"/api/shipments/{sh['id']}")
    entry = next(e for e in api.get("/api/recycle-bin") if sh["code"] in e["label"])
    api.post(f"/api/recycle-bin/{entry['id']}/restore")
    back = api.get(f"/api/shipments/{sh['id']}")
    assert back["status"] in ("new", "ready") and [(c["lead_line_id"], c["member_line_id"], c["quantity"]) for c in back["combos"]] == [(b["id"], n["id"], 300)]
