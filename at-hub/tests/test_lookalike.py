"""Look-alike orders and POs (same party, same items + quantities) need someone to OK them before they go on;
a vendor SO # already on another PO of the vendor is caught like a customer PO # is."""
import json


def _detail(r):
    return json.loads(r.json()["detail"].split("|", 1)[1])


def test_customer_lookalike_must_be_okd_before_confirming(make, api, client, admin_headers):
    cust, a, b = make.customer(), make.item(price=2), make.item(price=3)
    first = make.order(customer=cust, lines=[(a, 10, 2.0), (b, 5, 3.0)])  # confirmed
    second = make.order(customer=cust, lines=[(a, 10, 2.0), (b, 5, 3.0)], confirm=False)
    got = api.get(f"/api/customer-orders/{second['id']}")
    assert [x["code"] for x in got["lookalikes"]] == [first["code"]]
    la = got["lookalikes"][0]
    assert la["exact"] and la["same_price"] and "same 2 items" in la["what"]
    assert all(l["match"] for l in la["lines"]) and all(l["match"] for l in la["mine"])
    # the list carries it too (short form)
    row = next(o for o in api.get("/api/customer-orders/") if o["id"] == second["id"])
    assert row["lookalikes"][0]["code"] == first["code"]
    # confirming is refused until someone OKs it
    r = client.post(f"/api/customer-orders/{second['id']}/confirm", headers=admin_headers)
    assert r.status_code == 409 and r.json()["detail"].startswith("LOOKALIKE|")
    assert _detail(r)["codes"] == [first["code"]]
    ok = api.post(f"/api/customer-orders/{second['id']}/lookalike-ok", json={"codes": [first["code"]]})
    assert ok["lookalikes"] == [] and json.loads(ok["lookalike_ok"])["by"]
    assert api.post(f"/api/customer-orders/{second['id']}/confirm")["status"] == "confirmed"


def test_what_is_not_a_lookalike(make, api):
    cust, a, b, c = make.customer(), make.item(), make.item(), make.item()
    make.order(customer=cust, lines=[(a, 10, 2.0), (b, 5, 3.0)])
    # different quantity -> not alike
    o1 = make.order(customer=cust, lines=[(a, 12, 2.0), (b, 5, 3.0)], confirm=False)
    assert api.get(f"/api/customer-orders/{o1['id']}")["lookalikes"] == []
    # another customer -> not alike
    o2 = make.order(lines=[(a, 10, 2.0), (b, 5, 3.0)], confirm=False)
    assert api.get(f"/api/customer-orders/{o2['id']}")["lookalikes"] == []
    # one shared line of three -> not alike; most lines shared -> alike
    base = make.order(customer=cust, lines=[(a, 1, 1), (b, 2, 1), (c, 3, 1), (a, 4, 1)])
    near = make.order(customer=cust, lines=[(a, 1, 1), (b, 2, 1), (c, 3, 1), (b, 9, 1)], confirm=False)
    hit = api.get(f"/api/customer-orders/{near['id']}")["lookalikes"]
    assert [x["code"] for x in hit] == [base["code"]] and not hit[0]["exact"] and "3 of 4 lines" in hit[0]["what"]
    # prices differ: still alike, and it says so
    d = make.customer()
    make.order(customer=d, lines=[(a, 7, 2.0)])
    p = make.order(customer=d, lines=[(a, 7, 2.5)], confirm=False)
    assert "prices differ" in api.get(f"/api/customer-orders/{p['id']}")["lookalikes"][0]["what"]


def test_po_lookalike_and_duplicate_vendor_so(make, api, client, admin_headers):
    v, a = make.vendor(), make.item()
    first = make.po(vendor=v, lines=[(a, 100, 1.0)], vendor_so_number="SO-777")
    api.post(f"/api/purchase-orders/{first['id']}/mark-ordered")
    # same vendor SO # -> refused unless OK'd
    r = client.post("/api/purchase-orders/", headers=admin_headers, json={"vendor_id": v["id"], "vendor_so_number": " so-777 ",
                                                                           "lines": [{"item_id": a["id"], "quantity": 5, "unit_cost": 1}]})
    assert r.status_code == 409 and r.json()["detail"].startswith(f"DUPLICATE_SO|{first['id']}|")
    ok = api.post("/api/purchase-orders/", json={"vendor_id": v["id"], "vendor_so_number": "SO-777", "allow_duplicate": True,
                                                  "lines": [{"item_id": a["id"], "quantity": 5, "unit_cost": 1}]})
    assert ok["duplicate_so_ok"]
    # changing a PO's SO # to one already used is caught too
    other = make.po(vendor=v, lines=[(a, 3, 1.0)])
    r = client.put(f"/api/purchase-orders/{other['id']}", headers=admin_headers, json={"vendor_so_number": "SO-777"})
    assert r.status_code == 409
    # look-alike PO: same vendor, same items + quantities -> must be OK'd before it's marked ordered
    twin = make.po(vendor=v, lines=[(a, 100, 1.0)])
    assert [x["code"] for x in api.get(f"/api/purchase-orders/{twin['id']}")["lookalikes"]] == [first["code"]]
    r = client.post(f"/api/purchase-orders/{twin['id']}/mark-ordered", headers=admin_headers)
    assert r.status_code == 409 and "LOOKALIKE" in r.json()["detail"]
    api.post(f"/api/purchase-orders/{twin['id']}/lookalike-ok", json={"codes": []})
    assert api.post(f"/api/purchase-orders/{twin['id']}/mark-ordered")["status"] == "ordered"
