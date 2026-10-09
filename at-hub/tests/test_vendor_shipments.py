"""The PO's Shipped stage: vendor shipments (carrier, tracking, ETA) -- in transit until their goods are received,
then they complete themselves; the PO reads "shipped" while something is on the way and nothing is received yet."""


def _ordered_po(make, api, *lines):
    po = make.po(lines=lines)
    api.post(f"/api/purchase-orders/{po['id']}/mark-ordered")
    return api.get(f"/api/purchase-orders/{po['id']}")


def test_shipped_then_received_completes_itself(make, api):
    a, b = make.item(), make.item()
    po = _ordered_po(make, api, (a, 100, 1.0), (b, 50, 2.0))
    la, lb = po["lines"][0]["id"], po["lines"][1]["id"]
    po = api.post(f"/api/purchase-orders/{po['id']}/vendor-shipments", json={
        "shipped_date": "2026-10-09", "eta": "2026-10-15", "carrier": "Old Dominion", "ship_mode": "LTL", "pro_number": "12345",
        "tracking_number": "1Z999", "bol_number": "B-1", "packages": 3, "package_type": "Pallets", "weight": 2100,
        "lines": [{"po_line_id": la, "quantity": 100}]})
    assert po["status"] == "shipped"
    s1 = po["vendor_shipments"][0]
    assert s1["status"] == "in_transit" and s1["carrier"] == "Old Dominion" and s1["packages"] == 3
    assert s1["shipped_date"][:10] == "2026-10-09" and not s1["shipped_date"].endswith("Z")  # a calendar day, never shifted by time zone
    assert po["expected_date"][:10] == "2026-10-15"  # the ETA became the PO's expected date
    # second shipment: the whole rest (no lines)
    po = api.post(f"/api/purchase-orders/{po['id']}/vendor-shipments", json={"shipped_date": "2026-10-10", "carrier": "UPS"})
    assert [s["status"] for s in po["vendor_shipments"]] == ["in_transit", "in_transit"]
    # receiving shipment 1's goods completes it; the PO is part received, shipment 2 still on the way
    po = api.post(f"/api/purchase-orders/{po['id']}/receive", json={"lines": [{"line_id": la, "quantity": 100}]})
    assert po["status"] == "partially_received"
    assert [s["status"] for s in po["vendor_shipments"]] == ["received", "in_transit"]
    # everything in -> every shipment complete
    po = api.post(f"/api/purchase-orders/{po['id']}/receive", json={"lines": [{"line_id": lb, "quantity": 50}]})
    assert po["status"] == "received"
    assert all(s["status"] == "received" and s["received_at"] for s in po["vendor_shipments"])


def test_edit_delete_and_rules(make, api, client, admin_headers):
    a = make.item()
    po = _ordered_po(make, api, (a, 10, 1.0))
    pid = po["id"]
    po = api.post(f"/api/purchase-orders/{pid}/vendor-shipments", json={"carrier": "FedEx"})
    sid = po["vendor_shipments"][0]["id"]
    po = api.put(f"/api/purchase-orders/{pid}/vendor-shipments/{sid}", json={"carrier": "FedEx Freight", "eta": "2026-12-01", "update_expected": False})
    assert po["vendor_shipments"][0]["carrier"] == "FedEx Freight" and (po["expected_date"] or "")[:10] != "2026-12-01"
    r = client.put(f"/api/purchase-orders/{pid}/vendor-shipments/{sid}", headers=admin_headers, json={"shipped_date": "2026-10-09", "eta": "2026-10-01"})
    assert r.status_code == 400  # ETA before the ship date
    po = api.delete(f"/api/purchase-orders/{pid}/vendor-shipments/{sid}")
    assert po["status"] == "ordered" and not po["vendor_shipments"]
    # a draft that ships is ordered; a received PO has nothing left to ship
    d = make.po(lines=[(a, 5, 1.0)])
    po = api.post(f"/api/purchase-orders/{d['id']}/vendor-shipments", json={})
    assert po["status"] == "shipped"
    api.post(f"/api/purchase-orders/{d['id']}/receive", json={"lines": [{"line_id": d["lines"][0]["id"], "quantity": 5}]})
    r = client.post(f"/api/purchase-orders/{d['id']}/vendor-shipments", headers=admin_headers, json={})
    assert r.status_code == 400


def test_shipped_pos_still_count_as_open(make, api):
    """Planning, reports and insights treat a shipped PO like an ordered one (stock on the way)."""
    a = make.item()
    po = _ordered_po(make, api, (a, 7, 1.0))
    api.post(f"/api/purchase-orders/{po['id']}/vendor-shipments", json={"eta": "2030-01-01"})
    item = api.get(f"/api/stock-items/{a['id']}")
    on_order = item.get("on_order", item.get("qty_on_order"))
    if on_order is not None:
        assert on_order >= 7
