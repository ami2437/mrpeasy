"""Several people on the same record: stale saves are refused (409, who + when), line changes bump the
version, shipping side effects don't, presence lists the others, and parallel bookings never oversell."""
import threading


def _put(client, headers, url, body, version=None, force=False):
    h = dict(headers)
    if version is not None:
        h["X-Row-Version"] = str(version)
    if force:
        h["X-Force-Save"] = "1"
    return client.put(url, json=body, headers=h)


def test_stale_save_is_refused_with_who_and_when(make, api, client, admin_headers):
    a = make.item()
    o = make.order(lines=[(a, 5, 1)])
    v = api.get(f"/api/customer-orders/{o['id']}")["row_version"]
    head = {"customer_id": o["customer_id"], "po_number": o["po_number"]}
    # person 1 saves (with the version they loaded) -> fine, version moves on
    r1 = _put(client, admin_headers, f"/api/customer-orders/{o['id']}", {**head, "job_number": "JOB-1"}, v)
    assert r1.status_code == 200 and r1.json()["row_version"] == v + 1 and r1.json()["updated_by"] == "admin"
    # person 2 still has the old screen (version v) -> refused, nothing overwritten
    r2 = _put(client, admin_headers, f"/api/customer-orders/{o['id']}", {**head, "notes": "from person 2"}, v)
    assert r2.status_code == 409 and r2.json()["detail"].startswith("CONFLICT|") and r2.json()["conflict"]["by"] == "admin"
    assert api.get(f"/api/customer-orders/{o['id']}")["job_number"] == "JOB-1"
    # "save mine anyway"
    r3 = _put(client, admin_headers, f"/api/customer-orders/{o['id']}", {**head, "notes": "from person 2"}, v, force=True)
    assert r3.status_code == 200


def test_line_change_bumps_the_order_but_shipping_does_not(make, api):
    a = make.item()
    make.stock(a, 20)
    o = make.order(lines=[(a, 5, 1)])
    v0 = api.get(f"/api/customer-orders/{o['id']}")["row_version"]
    api.put(f"/api/customer-orders/{o['id']}/lines/{o['lines'][0]['id']}", json={"unit_price": 2})
    v1 = api.get(f"/api/customer-orders/{o['id']}")["row_version"]
    assert v1 == v0 + 1  # a line edit is an edit of the order
    make.ship(o)
    assert api.get(f"/api/customer-orders/{o['id']}")["row_version"] == v1  # shipped quantities are a side effect


def test_presence_lists_the_others(client, admin_headers, api, make):
    from app.services import concurrency
    o = make.order(lines=[(make.item(), 1, 1)])
    key = f"customer-orders/{o['id']}"
    concurrency.heartbeat(key, "maria")  # someone else has it open
    r = client.post("/api/presence", json={"key": key}, headers=admin_headers).json()
    assert r["others"] == ["maria"] and r["version"] >= 1
    client.post("/api/presence", json={"key": key, "leave": True}, headers=admin_headers)
    concurrency.leave(key, "maria")


def test_parallel_bookings_never_oversell(make, api, client, admin_headers):
    a = make.item()
    make.stock(a, 100)
    orders = [make.order(lines=[(a, 100, 1)]) for _ in range(4)]
    out = []
    go = threading.Barrier(len(orders))

    def book(o):
        go.wait()
        r = client.post(f"/api/customer-orders/{o['id']}/shipments", headers=admin_headers,
                        json={"lines": [{"line_id": o["lines"][0]["id"], "quantity": 100}]})
        out.append(r.status_code)

    ts = [threading.Thread(target=book, args=(o,)) for o in orders]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(out) == [200, 400, 400, 400]  # one booking; the others are told there isn't enough stock
    it = api.get(f"/api/stock-items/{a['id']}")
    assert it["booked"] == 100 and it["available"] == 0
