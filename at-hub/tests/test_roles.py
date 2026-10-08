"""Roles and permissions: custom roles mix and match permissions, the server enforces them, money is blanked
without "money.view", and the built-in roles keep what they had."""
from app.services import permissions as P


def _user(client, admin_headers, role):
    from app.services.auth import AuthService
    name = f"u-{role}"
    client.post("/api/users/", json={"username": name, "password": "Temp!Passw0rd#1", "role": role}, headers=admin_headers)
    h = {"Authorization": f"Bearer {AuthService.create_access_token({'sub': name})}"}
    # like a real first login: the temporary password is swapped for their own (until then only My Account opens)
    client.post("/api/auth/change-password", json={"current_password": "Temp!Passw0rd#1", "new_password": "Str0ng!Passw0rd#"}, headers=h)
    return h


def test_builtin_roles_keep_the_old_ladder(client, admin_headers):
    roles = {r["key"]: r for r in client.get("/api/roles/", headers=admin_headers).json()}
    assert {"employee", "manager", "admin", "super_admin", "driver"} <= set(roles)
    assert "money.view" not in roles["employee"]["permissions"] and "shipments.work" in roles["employee"]["permissions"]
    # money ($) permissions start with Admin and Super admin only; Manager gets them only when ticked on the Roles screen
    assert not set(roles["manager"]["permissions"]) & P.MONEY and "orders.edit" in roles["manager"]["permissions"]
    assert P.MONEY <= set(roles["admin"]["permissions"]) and "templates" not in roles["manager"]["permissions"]
    assert set(roles["super_admin"]["permissions"]) == set(P.KEYS)
    me = client.get("/api/auth/me", headers=admin_headers).json()
    assert "users" in me["permissions"]


def test_custom_role_is_enforced(client, admin_headers, make):
    a = make.item(price=7)
    make.stock(a, 5)
    o = make.order(lines=[(a, 5, 7)])
    # a manager without money: orders and shipping, no prices, no invoices
    perms = [p for p in P.defaults_for("manager") if p not in P.MONEY]
    r = client.post("/api/roles/", json={"name": "Floor Lead", "permissions": perms}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["key"] == "floor_lead" and r.json()["money"] == []
    h = _user(client, admin_headers, "floor_lead")
    order = client.get(f"/api/customer-orders/{o['id']}", headers=h).json()
    assert order["lines"][0]["unit_price"] is None                      # money blanked
    assert client.get("/api/invoices/", headers=h).status_code == 403
    assert client.get(f"/api/stock-items/{a['id']}/price-history", headers=h).status_code == 403
    assert client.get("/api/stock-items/", headers=h).status_code == 200
    # the driver preset: the deliveries only -- items, boxes, pallets, where to; no orders, customers, stock or prices
    d = _user(client, admin_headers, "driver")
    for url in ("/api/shipments/", "/api/customers/", "/api/customer-orders/", "/api/stock-items/", "/api/lots/", "/api/mtrs/"):
        assert client.get(url, headers=d).status_code == 403, url
    sh = make.ship(o)
    rows = client.get("/api/pod/deliveries", headers=d).json()
    mine = next(r for r in rows if r["id"] == sh["id"])
    assert set(mine) == {"id", "code", "status", "ship_date", "delivered_at", "customer", "order_code", "po_number", "job_number",
                         "ship_to", "carrier", "lines", "boxes", "pallets", "pods"}
    assert set(mine["lines"][0]) == {"item_code", "description", "quantity"} and mine["boxes"] >= 1
    assert client.get("/api/vendors/", headers=d).status_code == 403
    assert client.get("/api/quotes/", headers=d).status_code == 403
    assert client.post(f"/api/customer-orders/{o['id']}/shipments", json={"lines": []}, headers=d).status_code == 403
    # give the role money and it sees prices
    client.put("/api/roles/floor_lead", json={"name": "Floor Lead", "permissions": perms + ["money.view"]}, headers=admin_headers)
    assert client.get(f"/api/customer-orders/{o['id']}", headers=h).json()["lines"][0]["unit_price"] == 7


def test_role_rules(client, admin_headers):
    assert client.put("/api/roles/super_admin", json={"name": "x", "permissions": []}, headers=admin_headers).status_code == 400
    assert client.delete("/api/roles/manager", headers=admin_headers).status_code == 400
    assert client.post("/api/roles/", json={"name": "Bad", "permissions": ["nope"]}, headers=admin_headers).status_code == 400
    client.post("/api/roles/", json={"name": "Temp Role", "permissions": []}, headers=admin_headers)
    _user(client, admin_headers, "temp_role")
    assert client.delete("/api/roles/temp_role", headers=admin_headers).status_code == 400   # still has a user
    emp = _user(client, admin_headers, "employee")
    assert client.post("/api/roles/", json={"name": "Sneaky", "permissions": P.KEYS}, headers=emp).status_code == 403


def test_view_as_is_read_only(client, admin_headers):
    """A super admin can see AT-HUB as someone else -- their menus and data -- but nothing can be changed that way."""
    _user(client, admin_headers, "driver")
    uid = next(u["id"] for u in client.get("/api/users/", headers=admin_headers).json() if u["username"] == "u-driver")
    r = client.post(f"/api/users/{uid}/view-as", headers=admin_headers).json()
    assert r["user"]["permissions"] == ["pod.upload"] and r["user"]["view_as_by"] == "admin"
    h = {"Authorization": f"Bearer {r['access_token']}"}
    assert client.get("/api/auth/me", headers=h).json()["username"] == "u-driver"
    assert client.get("/api/pod/deliveries", headers=h).status_code == 200
    assert client.get("/api/invoices/", headers=h).status_code == 403            # the driver's own limits
    blocked = client.post("/api/attachments/", headers=h)
    assert blocked.status_code == 403 and "read-only" in blocked.json()["detail"]
    emp = _user(client, admin_headers, "employee")
    assert client.post(f"/api/users/{uid}/view-as", headers=emp).status_code == 403
