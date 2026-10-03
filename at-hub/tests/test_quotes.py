"""Quotes: paste RFQ text -> lines with items and prices from history; PDF; convert to an order."""
from app.services.quotes import split_qty


def test_split_qty():
    assert split_qty("500 pcs 5/8-11 x 2 A325 HDG hex bolt") == (500, "5/8-11 x 2 A325 HDG hex bolt")
    assert split_qty("1,000 - 5/8 F436 washers HDG") == (1000, "5/8 F436 washers HDG")
    assert split_qty("Item 15343 qty 200")[0] == 200
    assert split_qty("5/8-11 x 2 hex bolt\t250")[0] == 250
    assert split_qty("5/8-11 x 2 hex bolt") == (None, "5/8-11 x 2 hex bolt")  # a size is never a quantity


def test_paste_price_convert(make, api, client, admin_headers):
    a = make.item(title="BOLT HH 5/8-11 X 2 A325 HDG", price=1.10)
    c = make.customer()
    make.order(customer=c, lines=[(a, 10, 1.25)])  # this customer last paid 1.25
    rows = api.post("/api/quotes/parse", json={"customer_id": c["id"], "text": f"Hi,\nplease quote\n300 pcs {a['code']} hex bolts\nThanks"})
    assert len(rows) == 1 and rows[0]["item_id"] == a["id"] and rows[0]["quantity"] == 300
    assert rows[0]["price"]["price"] == 1.25 and "this customer" in rows[0]["price"]["basis"]
    q = api.post("/api/quotes/", json={"customer_id": c["id"], "customer_ref": "RFQ 77", "lines": [
        {"item_id": a["id"], "quantity": 300, "unit_price": 1.25, "source_text": rows[0]["source"]},
        {"description": "Custom eye bolt (new item)", "quantity": 5, "unit_price": 9}]})
    assert q["code"].startswith("Q") and q["total"] == 420.0
    assert client.get(f"/api/quotes/{q['id']}/pdf", headers=admin_headers).content[:4] == b"%PDF"
    api.post(f"/api/quotes/{q['id']}/convert", json={}, expect=400)  # line 2 has no item yet
    q["lines"][1]["item_id"] = make.item()["id"]
    api.put(f"/api/quotes/{q['id']}", json={"customer_id": c["id"], "lines": q["lines"]})
    r = api.post(f"/api/quotes/{q['id']}/convert", json={"po_number": "PO-FROM-Q"})
    order = api.get(f"/api/customer-orders/{r['order_id']}")
    assert [l["quantity"] for l in order["lines"]] == [300, 5] and order["po_number"] == "PO-FROM-Q"
    assert api.get(f"/api/quotes/{q['id']}")["status"] == "converted"
