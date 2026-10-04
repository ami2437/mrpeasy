"""Packing lists follow the customer order with every nut right under its bolt and on the bolt's pallet;
boxes per pallet print only when asked."""
import io
import re

from pypdf import PdfReader


def test_nut_rides_with_its_bolt(api, client, admin_headers, make):
    from tests.builders import uid
    bolt = make.item(code=uid("B"))
    washer = make.item(code=uid("W"), group="Washer")
    nut = make.item(code=f"{bolt['code']}-NUTS", group="Nut")          # the -NUTS spelling is the same nut
    for it in (bolt, washer, nut):
        make.stock(it, 100)
    o = make.order(lines=[(bolt, 100, 2), (washer, 100, 1), (nut, 100, 0)])   # nut entered last on the order
    sh = api.post(f"/api/customer-orders/{o['id']}/shipments", json={"lines": [{"line_id": l["id"], "quantity": 100} for l in o["lines"]]})
    lid = {l["item_id"]: l["id"] for l in o["lines"]}
    boxes = [{"order_line_id": lid[bolt["id"]], "item_id": bolt["id"], "box_number": 1, "quantity_in_box": 100, "pallet_number": "P1"},
             {"order_line_id": lid[washer["id"]], "item_id": washer["id"], "box_number": 2, "quantity_in_box": 100, "pallet_number": "P2"},
             {"order_line_id": lid[nut["id"]], "item_id": nut["id"], "box_number": 3, "quantity_in_box": 100}]
    saved = api.put(f"/api/shipments/{sh['id']}/boxes", json={"boxes": boxes})
    assert next(b for b in saved["boxes"] if b["item_id"] == nut["id"])["pallet_number"] == "P1"   # stored: labels say P1 too

    r = client.get(f"/api/shipments/{sh['id']}/packing-list.pdf?pallets=true", headers=admin_headers)
    text = "".join(pg.extract_text() for pg in PdfReader(io.BytesIO(r.content)).pages)
    order = [m.start() for m in (re.search(re.escape(c), text) for c in (bolt["code"] + "\n", nut["code"], washer["code"])) if m]
    assert len(order) == 3 and order == sorted(order), text[:600]         # bolt, its nut, then the washer
    assert "Boxes" not in text.split("PALLETS")[-1].split("\n")[0:3].__str__()  # no box count per pallet by default
    r2 = client.get(f"/api/shipments/{sh['id']}/packing-list.pdf?pallets=true&pallet_boxes=true", headers=admin_headers)
    text2 = "".join(pg.extract_text() for pg in PdfReader(io.BytesIO(r2.content)).pages)
    assert "Boxes" in text2.split("PALLETS")[-1]
