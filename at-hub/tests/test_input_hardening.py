"""Input hardening found by fuzzing (2026-10-06): names can't be blank, codes can't carry markup, task links can't run
script, pack size 0 means none -- and records already stored that break today's input rules still READ fine."""
import pytest

from tests.builders import uid


@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
def test_customer_and_vendor_need_a_real_name(api, name):
    api.post("/api/customers/", json={"name": name}, expect=422)
    api.post("/api/vendors/", json={"name": name}, expect=422)


def test_rename_to_blank_refused_and_spaces_tidied(api, make):
    c = make.customer(name=f"  Smith  &  Sons {uid()} ")
    assert c["name"].startswith("Smith & Sons ")  # inner runs of spaces collapse, ends trimmed; & stays
    api.put(f"/api/customers/{c['id']}", json={"name": "  "}, expect=422)


@pytest.mark.parametrize("code", ["A<b>", 'x"y', "o'k", "t`k", "a\nb"])
def test_item_codes_carry_no_markup(api, code):
    api.post("/api/stock-items/", json={"code": code, "title": "t", "category": "Bolt"}, expect=422)


def test_ordinary_item_codes_still_fine(make):
    for code in (f"{uid('C')}-NUT", f"1/2-13 {uid('S')}", f"{uid('C')}_HDG"):
        assert make.item(code=code)["id"]


def test_pack_size_zero_means_none(api, make):
    it = make.item()
    assert api.put(f"/api/stock-items/{it['id']}", json={"default_pack_size": 0})["default_pack_size"] is None
    api.put(f"/api/stock-items/{it['id']}", json={"default_pack_size": -5}, expect=422)


@pytest.mark.parametrize("link", ["javascript:alert(1)", " JaVaScript:alert(1)", "java\nscript:alert(1)", "data:text/html,x", "vbscript:x"])
def test_task_links_cant_run_script(api, link):
    api.post("/api/tasks/", json={"title": "t", "link": link}, expect=422)


def test_task_links_that_are_fine(api):
    for link in ("https://example.com/x?y=1", "customer-orders.html?id=3", "/api/x"):
        assert api.post("/api/tasks/", json={"title": "t", "link": link})


def test_stored_records_that_break_todays_rules_still_read(api, make):
    """Older / imported data: a fractional box quantity, a lot code with a quote -- the shipment must still open."""
    from app.config.database import SessionLocal
    from app.models import ShipmentBox
    a = make.item(price=2)
    make.stock(a, 10)
    o = make.order(lines=[(a, 10, 2)])
    sh = make.ship(o)
    with SessionLocal() as db:
        box = db.query(ShipmentBox).filter(ShipmentBox.shipment_id == sh["id"]).first()
        box.quantity_in_box, box.lot_code, box.pallet_number = 2.5, 'L"1', "P<1>"
        db.commit()
    got = api.get(f"/api/shipments/{sh['id']}")
    assert any(b["lot_code"] == 'L"1' and b["quantity_in_box"] == 2.5 for b in got["boxes"])
    assert api.get("/api/shipments/")  # the list too


def test_temporary_password_only_opens_my_account(api, client):
    """A user an admin just created (or reset) can only see who they are and change the password -- the server
    refuses everything else until they do, not just the screens."""
    from app.services.auth import AuthService
    name = uid("temp")
    u = api.post("/api/users/", json={"username": name, "password": "Temp-Pass-1", "role": "manager", "full_name": "Temp"})
    assert u["must_change_password"]
    h = {"Authorization": f"Bearer {AuthService.create_access_token({'sub': name})}"}
    assert client.get("/api/auth/me", headers=h).status_code == 200
    for path in ("/api/customers/", "/api/customer-orders/", "/api/shipments/", "/api/stock-items/", "/api/todo/counts"):
        r = client.get(path, headers=h)
        assert r.status_code == 403 and "password" in r.json()["detail"], (path, r.status_code)
    assert client.post("/api/customers/", headers=h, json={"name": "Sneaky"}).status_code == 403
    assert client.post("/api/auth/change-password", headers=h, json={"current_password": "Temp-Pass-1", "new_password": "Temp-Pass-1"}).status_code == 400
    assert client.post("/api/auth/change-password", headers=h, json={"current_password": "Temp-Pass-1", "new_password": "Short1"}).status_code == 400
    assert client.post("/api/auth/change-password", headers=h, json={"current_password": "Temp-Pass-1", "new_password": "My-Own-Pass-22"}).status_code == 200
    assert client.get("/api/customers/", headers=h).status_code == 200  # now everything their role allows
    # a reset puts the lock back on
    api.post(f"/api/users/{u['id']}/reset-password", json={"password": "Reset-Pass-9"})
    assert client.get("/api/customers/", headers=h).status_code == 403


@pytest.mark.parametrize("pw", ["", "1234", "seven77"])
def test_admin_set_passwords_need_eight_characters(api, pw):
    api.post("/api/users/", json={"username": uid("short"), "password": pw, "role": "employee"}, expect=(400, 422))
    u = api.post("/api/users/", json={"username": uid("ok"), "password": "Long-Enough-1", "role": "employee"})
    api.post(f"/api/users/{u['id']}/reset-password", json={"password": pw}, expect=(400, 422))


def test_my_account_works_on_a_temporary_password(api, client):
    """Time zone and two-step login are on My Account too: open while the password is temporary; View As isn't blocked."""
    from app.services.auth import AuthService
    name = uid("tmpacct")
    u = api.post("/api/users/", json={"username": name, "password": "Temp-Pass-1", "role": "employee"})
    h = {"Authorization": f"Bearer {AuthService.create_access_token({'sub': name})}"}
    assert client.put("/api/auth/timezone", json={"timezone": "America/New_York"}, headers=h).status_code == 200
    assert client.post("/api/auth/2fa/setup", headers=h).status_code == 200
    view = client.post(f"/api/users/{u['id']}/view-as", headers={"Authorization": f"Bearer {AuthService.create_access_token({'sub': 'admin'})}"}).json()
    assert client.get("/api/shipments/", headers={"Authorization": f"Bearer {view['access_token']}"}).status_code == 200


def test_usernames_ignore_case(api, client):
    name = uid("CaseUser")
    api.post("/api/users/", json={"username": name, "password": "Temp-Pass-1", "role": "employee"})
    r = client.post("/api/auth/login", json={"username": name.lower(), "password": "Temp-Pass-1"})
    assert r.status_code == 200 and r.json()["user"]["username"] == name
    api.post("/api/users/", json={"username": name.upper(), "password": "Temp-Pass-1", "role": "employee"}, expect=400)
