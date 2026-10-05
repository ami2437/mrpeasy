"""Pack-size memory (what was actually packed pre-fills the next packing), presets, and Bulk Operations' one-PDF / one-file-each
/ per-customer email choices."""
import io
import zipfile

from pypdf import PdfReader


def _book(api, make, customer, item, qty):
    o = make.order(customer=customer, lines=[(item, qty, 1)])
    return api.post(f"/api/customer-orders/{o['id']}/shipments", json={"lines": [{"line_id": o["lines"][0]["id"], "quantity": qty}]})


def _pack(api, sh, item, pack):
    line = sh["lines"][0]["order_line_id"]
    qty, boxes, n = sum(l["quantity"] for l in sh["lines"]), [], 0
    while qty > 0:
        n += 1
        boxes.append({"order_line_id": line, "item_id": item["id"], "box_number": n, "quantity_in_box": min(pack, qty), "pack_size": pack})
        qty -= pack
    api.put(f"/api/shipments/{sh['id']}/boxes", json={"boxes": boxes})
    api.post(f"/api/shipments/{sh['id']}/accept-packing")


def _suggest(api, sh):
    return api.get(f"/api/shipments/pack-suggestions?ids={sh['id']}")[str(sh["id"])][str(sh["lines"][0]["order_line_id"])]


def test_memory_prefills_from_what_was_packed(api, make):
    item = make.item(default_pack_size=10)
    make.stock(item, 2000)
    a, b, c = make.customer(), make.customer(), make.customer()
    _pack(api, _book(api, make, a, item, 100), item, 25)          # customer A packs 25s
    _pack(api, _book(api, make, b, item, 100), item, 40)          # then customer B packs 40s
    _pack(api, _book(api, make, b, item, 20), item, 50)           # one box of 20, recorded as a 50 pack -> still counts

    next_a, next_c = _book(api, make, a, item, 60), _book(api, make, c, item, 60)
    assert _suggest(api, next_a)["size"] == 25 and _suggest(api, next_a)["source"] == "customer"   # A's own last
    assert _suggest(api, next_c)["size"] == 50 and _suggest(api, next_c)["source"] == "last"       # nobody's packed for C: last anyone
    uses = api.get(f"/api/stock-items/pack-sizes/usage?item_ids={item['id']}")[str(item["id"])]
    assert [u["pack_size"] for u in uses[:3]] == [50, 40, 25] and all(u["sure"] for u in uses[:3])

    try:
        api.put("/api/stock-items/pack-sizes/rule", json={"rule": "default"})
        assert _suggest(api, next_a)["size"] == 10                                                 # old behaviour: item default only
        api.put("/api/stock-items/pack-sizes/rule", json={"rule": "customer"})
        assert _suggest(api, next_c)["size"] == 10                                                 # C has no history -> default
    finally:
        api.put("/api/stock-items/pack-sizes/rule", json={"rule": "smart"})

    # someone sets a new default by hand -> it's newer than any packing, so smart uses it
    api.post("/api/stock-items/pack-sizes/bulk", json={"entries": [{"code": item["code"], "pack_size": 30}], "source": "bulk paste"})
    assert _suggest(api, next_a)["size"] == 30
    # accepting a packing with no boxes typed makes them from the pre-filled size, and records it
    sh = api.post(f"/api/shipments/{next_a['id']}/accept-packing")
    assert [bx["quantity_in_box"] for bx in sh["boxes"]] == [30, 30] and {bx["pack_size"] for bx in sh["boxes"]} == {30}
    assert _suggest(api, next_c)["size"] == 30


def test_presets(api, make):
    cust = make.customer()
    p = api.post("/api/stock-items/pack-sizes/presets", json={"name": "Tulsa pallets", "customer_id": cust["id"], "sizes": {"A1": 50, "B2": 0}})
    assert p["sizes"] == {"A1": 50}                                                                   # blanks / zeros dropped
    again = api.post("/api/stock-items/pack-sizes/presets", json={"name": "tulsa PALLETS", "sizes": {"A1": 60}})
    assert again["id"] == p["id"] and again["sizes"] == {"A1": 60}                                    # same name -> replaced
    api.post("/api/stock-items/pack-sizes/presets", json={"name": " ", "sizes": {"A1": 5}}, expect=400)
    api.delete(f"/api/stock-items/pack-sizes/presets/{p['id']}")
    assert all(x["id"] != p["id"] for x in api.get("/api/stock-items/pack-sizes/presets"))


def test_one_pdf_or_one_file_each_and_per_customer_emails(api, client, admin_headers, make):
    item = make.item()
    make.stock(item, 100)
    cust = make.customer(email="dock@example.com")
    o1, o2 = make.order(customer=cust, lines=[(item, 10, 1)]), make.order(customer=cust, lines=[(item, 15, 1)])
    s1, s2 = make.ship(o1), make.ship(o2)
    ids = f"{s1['id']},{s2['id']}"

    r = client.get(f"/api/shipments/packing-lists.pdf?ids={ids}&split=true", headers=admin_headers)
    assert r.headers["content-type"] == "application/zip"
    assert sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist()) == sorted([f"Packing-List-{s1['code']}.pdf", f"Packing-List-{s2['code']}.pdf"])
    r = client.get(f"/api/shipments/packing-lists.pdf?ids={ids}", headers=admin_headers)
    assert len(PdfReader(io.BytesIO(r.content)).pages) >= 2
    r = client.get(f"/api/bulk/documents.pdf?shipment_ids={ids}&kinds=labels,packing_list&split=true", headers=admin_headers)
    assert len(zipfile.ZipFile(io.BytesIO(r.content)).namelist()) == 4

    body = {"shipment_ids": [s1["id"], s2["id"]], "kinds": ["packing_list", "labels"]}
    assert len(api.post("/api/bulk/documents/plan", json={**body, "group_by": "order"})["groups"]) == 2
    g = api.post("/api/bulk/documents/plan", json={**body, "group_by": "customer", "attach": "combined"})["groups"]
    assert len(g) == 1 and len(g[0]["attachments"]) == 4                                    # one email for the customer
    assert g[0]["attach"] == "combined" and g[0]["combined_name"].endswith(".pdf")
    assert o1["code"] in g[0]["order"] and o2["code"] in g[0]["order"] and "your POs" in g[0]["subject"]
    from app.config.database import SessionLocal
    from app.services import bulk_docs
    db = SessionLocal()
    try:
        files = bulk_docs._files_for(db, g[0])
    finally:
        db.close()
    assert len(files) == 1 and len(PdfReader(io.BytesIO(files[0][0])).pages) >= 4         # merged into one attachment


def test_unpack_clears_packing_until_shipped(api, make):
    item = make.item(default_pack_size=10)
    make.stock(item, 100)
    sh = _book(api, make, make.customer(), item, 30)
    api.put(f"/api/shipments/{sh['id']}/pallet-weights", json={"pallets": [{"pallet_number": "P1", "weight": 50}]})
    _pack(api, sh, item, 10)
    packed = api.get(f"/api/shipments/{sh['id']}")
    assert packed["packed_at"] and len(packed["boxes"]) == 3
    un = api.post(f"/api/shipments/{sh['id']}/unpack")
    assert not un["packed_at"] and un["boxes"] == [] and un["pallets"] == []
    assert sum(l["quantity"] for l in un["lines"]) == 30                      # bookings untouched
    shipped = make.ship(make.order(lines=[(item, 5, 1)]))
    api.post(f"/api/shipments/{shipped['id']}/unpack", expect=400)           # gone: can't unpack


def test_unpick_until_shipped(api, make):
    item = make.item()
    make.stock(item, 50)
    sh = _book(api, make, make.customer(), item, 20)
    api.post(f"/api/shipments/{sh['id']}/confirm-booking")
    api.post(f"/api/shipments/{sh['id']}/pick", json={"pick_all": True})
    _pack(api, sh, item, 10)
    un = api.post(f"/api/shipments/{sh['id']}/unpick")
    assert all(l["picked_quantity"] == 0 for l in un["lines"]) and un["status"] == "ready"
    assert not un["packed_at"] and len(un["boxes"]) == 2                      # boxes stay; packing re-accepted after picking
    assert api.get(f"/api/stock-items/{item['id']}")["on_hand"] == 50       # picking never moved stock
    shipped = make.ship(make.order(lines=[(item, 5, 1)]))
    api.post(f"/api/shipments/{shipped['id']}/unpick", expect=400)


def test_unconfirm_booking(api, make):
    item = make.item()
    make.stock(item, 50)
    sh = _book(api, make, make.customer(), item, 20)
    api.post(f"/api/shipments/{sh['id']}/confirm-booking")
    back = api.post(f"/api/shipments/{sh['id']}/unconfirm-booking")
    assert back["status"] == "new" and sum(l["quantity"] for l in back["lines"]) == 20   # still booked
    api.post(f"/api/shipments/{sh['id']}/unconfirm-booking", expect=400)                  # already new
    api.post(f"/api/shipments/{sh['id']}/confirm-booking")
    api.post(f"/api/shipments/{sh['id']}/pick", json={"pick_all": True})
    api.post(f"/api/shipments/{sh['id']}/unconfirm-booking", expect=400)                  # picked: unpick first
    api.post(f"/api/shipments/{sh['id']}/unpick")
    assert api.post(f"/api/shipments/{sh['id']}/unconfirm-booking")["status"] == "new"


def test_unbook_all(api, make):
    a, b = make.item(), make.item()
    make.stock(a, 50)
    make.stock(b, 50)
    o = make.order(lines=[(a, 20, 1), (b, 10, 1)])
    sh = api.post(f"/api/customer-orders/{o['id']}/shipments", json={"lines": [{"line_id": l["id"], "quantity": l["quantity"]} for l in o["lines"]]})
    api.post(f"/api/shipments/{sh['id']}/confirm-booking")
    first = next(l for l in sh["lines"] if l["item_id"] == a["id"])
    api.post(f"/api/shipments/{sh['id']}/pick", json={"lines": [{"shipment_line_id": first["id"], "quantity": 5}]})
    after = api.post(f"/api/shipments/{sh['id']}/unbook-all")
    assert after["status"] == "ready" and [(l["quantity"], l["picked_quantity"]) for l in after["lines"]] == [(5, 5)]   # picked stays
    assert api.get(f"/api/stock-items/{b['id']}")["booked"] == 0
    api.post(f"/api/shipments/{sh['id']}/unbook-all", expect=400)                           # nothing unpicked left
    sh2 = _book(api, make, make.customer(), a, 10)
    assert api.post(f"/api/shipments/{sh2['id']}/unbook-all")["status"] == "cancelled"      # nothing picked -> cancelled


def test_going_back_before_packing_needs_the_packing_reaccepted(api, make):
    item = make.item()
    make.stock(item, 50)
    shipped = make.ship(make.order(lines=[(item, 20, 1)]))            # picked, packed (accepted), shipped
    assert shipped["packed_at"] and shipped["boxes"]
    back = api.post(f"/api/shipments/{shipped['id']}/unship", json={})
    assert back["status"] == "new" and not back["packed_at"] and back["boxes"]   # boxes kept as a proposal
    api.post(f"/api/shipments/{back['id']}/confirm-booking")
    api.post(f"/api/shipments/{back['id']}/pick", json={"pick_all": True})
    api.post(f"/api/shipments/{back['id']}/ship", expect=400)                     # can't ship until re-accepted
    api.post(f"/api/shipments/{back['id']}/accept-packing")
    assert api.post(f"/api/shipments/{back['id']}/ship")["status"] == "shipped"


def test_part_pick_then_unbook_the_rest(api, make):
    a, b = make.item(), make.item()
    make.stock(a, 50)
    make.stock(b, 50)
    o = make.order(lines=[(a, 40, 1), (b, 10, 1)])
    sh = api.post(f"/api/customer-orders/{o['id']}/shipments", json={"lines": [{"line_id": l["id"], "quantity": l["quantity"]} for l in o["lines"]]})
    api.post(f"/api/shipments/{sh['id']}/confirm-booking")
    la = next(l for l in sh["lines"] if l["item_id"] == a["id"])
    api.post(f"/api/shipments/{sh['id']}/pick", json={"lines": [], "unbook_rest": True}, expect=400)   # nothing picked: refused
    kept = api.post(f"/api/shipments/{sh['id']}/pick", json={"lines": [{"shipment_line_id": la["id"], "quantity": 10}]})
    assert sorted((l["quantity"], l["picked_quantity"]) for l in kept["lines"]) == [(10, 0), (40, 10)]   # keep booked: only 10 picked
    after = api.post(f"/api/shipments/{sh['id']}/pick", json={"lines": [{"shipment_line_id": la["id"], "quantity": 5}], "unbook_rest": True})
    assert [(l["item_id"], l["quantity"], l["picked_quantity"]) for l in after["lines"]] == [(a["id"], 15, 15)]   # rest released
    assert api.get(f"/api/stock-items/{a['id']}")["booked"] == 15 and api.get(f"/api/stock-items/{b['id']}")["booked"] == 0
    api.post(f"/api/shipments/{after['id']}/accept-packing")
    assert api.post(f"/api/shipments/{after['id']}/ship")["status"] == "shipped"
    line_a = next(l for l in api.get(f"/api/customer-orders/{o['id']}")["lines"] if l["item_id"] == a["id"])
    assert line_a["shipped_quantity"] == 15   # the other 25 stays open on the order
