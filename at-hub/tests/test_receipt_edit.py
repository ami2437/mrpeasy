"""Correcting a receipt of goods: its date, a line's quantity / lot # / PO line, or undoing it -- stock, the ledger, the
PO's received quantities and status follow; what already went out can't be taken back."""
from datetime import timedelta


def _received(make, api, lines, receive):
    po = make.po(lines=lines)
    api.post(f"/api/purchase-orders/{po['id']}/mark-ordered")
    po = api.post(f"/api/purchase-orders/{po['id']}/receive", json={"lines": [{"line_id": po["lines"][i]["id"], "quantity": q} for i, q in receive]})
    rec = api.get(f"/api/purchase-orders/{po['id']}/receipts")["receipts"][0]
    return po, rec


def _on_hand(api, item):
    return api.get(f"/api/stock-items/{item['id']}")["on_hand"]


def test_change_date_quantity_lot_and_line(make, api, client, admin_headers):
    from app.services import clock
    a = make.item()
    po, rec = _received(make, api, [(a, 100, 2.0), (a, 50, 2.0)], [(0, 60)])
    lot_id = rec["lines"][0]["lot_id"]
    # the date: a calendar day, never in the future
    day = (clock.today() - timedelta(days=3)).strftime("%Y-%m-%d")
    api.put(f"/api/purchase-orders/{po['id']}/receipts/date", json={"lot_ids": [lot_id], "received_date": day})
    got = api.get(f"/api/purchase-orders/{po['id']}/receipts")["receipts"][0]
    assert got["lines"][0]["lot_id"] == lot_id
    future = (clock.today() + timedelta(days=2)).strftime("%Y-%m-%d")
    r = client.put(f"/api/purchase-orders/{po['id']}/receipts/date", headers=admin_headers, json={"lot_ids": [lot_id], "received_date": future})
    assert r.status_code == 400
    # quantity up: stock, the line and the lot follow
    before = _on_hand(api, a)
    po = api.put(f"/api/purchase-orders/{po['id']}/receipts/lots/{lot_id}", json={"quantity": 70, "lot_code": "SUP-LOT-9"})
    assert po["lines"][0]["received_quantity"] == 70 and _on_hand(api, a) == before + 10
    got = api.get(f"/api/purchase-orders/{po['id']}/receipts")["receipts"][0]["lines"][0]
    assert got["quantity"] == 70 and got["left"] == 70 and got["lot_code"] == "SUP-LOT-9"
    # more than the PO line holds -> refused
    r = client.put(f"/api/purchase-orders/{po['id']}/receipts/lots/{lot_id}", headers=admin_headers, json={"quantity": 101})
    assert r.status_code == 400
    # received on the wrong line: move it (the other line is for 50, so 70 doesn't fit -- 40 does)
    r = client.put(f"/api/purchase-orders/{po['id']}/receipts/lots/{lot_id}", headers=admin_headers, json={"po_line_id": po["lines"][1]["id"]})
    assert r.status_code == 400
    api.put(f"/api/purchase-orders/{po['id']}/receipts/lots/{lot_id}", json={"quantity": 40})
    po = api.put(f"/api/purchase-orders/{po['id']}/receipts/lots/{lot_id}", json={"po_line_id": po["lines"][1]["id"]})
    assert [l["received_quantity"] for l in po["lines"]] == [0, 40]


def test_undo_and_what_already_went_out(make, api, client, admin_headers):
    a = make.item()
    po, rec = _received(make, api, [(a, 30, 1.0)], [(0, 30)])
    lot_id = rec["lines"][0]["lot_id"]
    assert po["status"] == "received"
    before = _on_hand(api, a)
    # nothing went out: undo takes it all back and the PO is open again
    po = api.post(f"/api/purchase-orders/{po['id']}/receipts/undo", json={"lot_ids": [lot_id]})
    assert po["status"] == "ordered" and po["lines"][0]["received_quantity"] == 0 and _on_hand(api, a) == before - 30
    assert api.get(f"/api/purchase-orders/{po['id']}/receipts")["receipts"] == []
    # received again, and 10 shipped from it
    po = api.post(f"/api/purchase-orders/{po['id']}/receive", json={"lines": [{"line_id": po["lines"][0]["id"], "quantity": 30}]})
    lot_id = api.get(f"/api/purchase-orders/{po['id']}/receipts")["receipts"][0]["lines"][0]["lot_id"]
    o = make.order(lines=[(a, 10, 5.0)])
    sh = make.ship(o)
    r = client.post(f"/api/purchase-orders/{po['id']}/receipts/undo", headers=admin_headers, json={"lot_ids": [lot_id]})
    assert r.status_code == 400 and sh["code"] in r.json()["detail"]
    r = client.put(f"/api/purchase-orders/{po['id']}/receipts/lots/{lot_id}", headers=admin_headers, json={"quantity": 5})
    assert r.status_code == 400 and "10" in r.json()["detail"]
    # down to what's still on the shelf is fine
    po = api.put(f"/api/purchase-orders/{po['id']}/receipts/lots/{lot_id}", json={"quantity": 12})
    assert po["lines"][0]["received_quantity"] == 12 and po["status"] == "partially_received"


def test_receipt_corrections_need_the_permission(client, api, make):
    from app.services import permissions as P
    assert "receipts.correct" in P.KEYS and "receipts.correct" not in P.MONEY
