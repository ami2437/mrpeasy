"""Quick Insights: each figure moves by exactly the cents of what happened (the database is shared, so deltas)."""
from tests.test_roles import _user


def _figs(api):
    out = {}
    for s in api.get("/api/insights/")["sections"]:
        for f in [s["headline"]] + s["figures"] + s.get("after", []):
            out[f"{s['key']}.{f['key']}"] = f["amount"]
    return out


def _delta(before, after):
    return {k: round(after[k] - before.get(k, 0), 2) for k in after if abs(after[k] - before.get(k, 0)) > 0.001}


def test_sales_flow_moves_the_right_figures(make, api):
    a = make.item()
    make.stock(a, 10)
    b0 = _figs(api)
    o = make.order(lines=[(a, 10, 3.335)])                                   # 33.35
    b1 = _figs(api)
    assert _delta(b0, b1) == {"orders.open": 33.35, "orders.unbooked": 33.35}

    sh = api.post(f"/api/customer-orders/{o['id']}/shipments", json={"lines": [{"line_id": o["lines"][0]["id"], "quantity": 4}]})
    b2 = _figs(api)                                                          # 4 x 3.335 = 13.34, 6 x 3.335 = 20.01
    assert _delta(b1, b2) == {"orders.booked": 13.34, "orders.unbooked": -13.34, "shipments.process": 13.34, "shipments.booked": 13.34}

    api.post(f"/api/shipments/{sh['id']}/confirm-booking")
    api.post(f"/api/shipments/{sh['id']}/pick", json={"pick_all": True})
    api.post(f"/api/shipments/{sh['id']}/accept-packing")
    assert _delta(b2, _figs(api)) == {"shipments.booked": -13.34, "shipments.packed": 13.34}
    b3 = _figs(api)
    api.post(f"/api/shipments/{sh['id']}/ship")
    b4 = _figs(api)
    assert _delta(b3, b4) == {"orders.booked": -13.34, "orders.shipped": 13.34, "shipments.process": -13.34,
                              "shipments.packed": -13.34, "shipments.not_billed": 13.34, "shipments.in_transit": 13.34}

    api.post(f"/api/shipments/{sh['id']}/delivered", json={})
    b5 = _figs(api)
    assert _delta(b4, b5) == {"shipments.in_transit": -13.34, "shipments.delivered": 13.34}

    inv = make.invoice(sh)
    b6 = _figs(api)
    assert _delta(b5, b6) == {"shipments.not_billed": -13.34, "shipments.delivered": -13.34,
                              "shipments.draft_invoice": 13.34, "invoices.drafts": 13.34}

    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"})
    api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": 10})
    d = _delta(b6, _figs(api))
    assert d["invoices.unpaid"] == 3.34 and d["invoices.current"] == 3.34 and d["invoices.drafts"] == -13.34
    assert d["shipments.draft_invoice"] == -13.34 and d["invoices.paid_total"] == 10


def test_purchasing_figures(make, api):
    a = make.item()
    b0 = _figs(api)
    po = make.po(lines=[(a, 5, 2)])
    api.post(f"/api/purchase-orders/{po['id']}/mark-ordered")
    api.post(f"/api/purchase-orders/{po['id']}/receive", json={"lines": [{"line_id": po["lines"][0]["id"], "quantity": 2}]})
    b1 = _figs(api)
    assert _delta(b0, b1) == {"purchasing.open": 10, "purchasing.received": 4, "purchasing.not_received": 6,
                              "purchasing.owed": 10, "purchasing.rec_not_billed": 4}
    api.post(f"/api/purchase-orders/{po['id']}/payments", json={"amount": 3})
    assert _delta(b1, _figs(api)) == {"purchasing.owed": -3, "purchasing.paid_total": 3}


def test_needs_money_view_and_shows_only_screens_you_can_open(client, admin_headers):
    assert client.get("/api/insights/", headers=_user(client, admin_headers, "employee")).status_code == 403
    secs = [s["key"] for s in client.get("/api/insights/", headers=admin_headers).json()["sections"]]
    assert secs == ["orders", "shipments", "invoices", "purchasing"]
