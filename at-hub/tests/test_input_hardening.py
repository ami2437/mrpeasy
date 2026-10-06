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
