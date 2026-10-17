"""Assign / Ask a person on a record, the unofficial Cost Sheet per PO, grouped box labels and dropping a file on a list
page (user, 2026-10-17)."""
from app.config.database import SessionLocal
from app.models import Lot, TodoItem
from app.services.auth import AuthService


def _user(client, admin_headers, name, role):
    client.post("/api/users/", json={"username": name, "full_name": name.title(), "password": "Temp!Passw0rd#1", "role": role}, headers=admin_headers)
    h = {"Authorization": f"Bearer {AuthService.create_access_token({'sub': name})}"}
    client.post("/api/auth/change-password", json={"current_password": "Temp!Passw0rd#1", "new_password": "Str0ng!Passw0rd#"}, headers=h)
    return h


def _todos(owner):
    db = SessionLocal()
    try:
        from app.models import TodoList
        return [t for t in db.query(TodoItem).join(TodoList, TodoList.id == TodoItem.list_id).filter(TodoList.owner == owner).all()]
    finally:
        db.close()


def test_ask_only_people_who_can_see_it(client, admin_headers, api, make):
    _user(client, admin_headers, "ask-emp", "employee")      # no prices -> can't be asked about a PO
    mh = _user(client, admin_headers, "ask-adm", "admin")
    po = make.po(lines=[(make.item(), 10, 1.0)])
    people = {p["username"]: p for p in api.get(f"/api/asks/people?entity_type=purchase_order&entity_id={po['id']}")}
    assert not people["ask-emp"]["ok"] and people["ask-emp"]["why"] and people["ask-adm"]["ok"]
    r = client.post("/api/asks/", json={"entity_type": "purchase_order", "entity_id": po["id"], "user": "ask-emp", "action": "validate"}, headers=admin_headers)
    assert r.status_code == 403 and "doesn't have permission to see this" in r.json()["detail"]
    # the employee can't even list asks on it
    eh = {"Authorization": f"Bearer {AuthService.create_access_token({'sub': 'ask-emp'})}"}
    assert client.get(f"/api/asks/?entity_type=purchase_order&entity_id={po['id']}", headers=eh).status_code == 403

    a = api.post("/api/asks/", json={"entity_type": "purchase_order", "entity_id": po["id"], "user": "ask-adm", "action": "validate",
                                     "comment": "Check the qty against the SO", "due_date": "2026-10-20"})
    assert a["status"] == "open" and a["due_date"] == "2026-10-20"
    todo = [t for t in _todos("ask-adm") if t.entity_type == "purchase_order" and t.entity_id == po["id"]]
    assert len(todo) == 1 and "Validate" in todo[0].title and not todo[0].done
    # a reply from the asked person lands in the asker's To-Do
    a = client.post(f"/api/asks/{a['id']}/reply", json={"text": "On it"}, headers=mh).json()
    assert a["replies"][0]["text"] == "On it"
    assert any("replied" in t.title for t in _todos("admin"))
    assert any(x["id"] == a["id"] for x in client.get("/api/asks/mine", headers=mh).json())
    # validating the PO closes it by itself, and their To-Do line is ticked
    api.post(f"/api/purchase-orders/{po['id']}/mark-ordered")
    rows = api.get(f"/api/asks/?entity_type=purchase_order&entity_id={po['id']}")
    assert rows[0]["status"] == "done" and rows[0]["done_how"] == "validated"
    assert [t for t in _todos("ask-adm") if t.id == todo[0].id][0].done


def test_ask_done_and_take_back(client, admin_headers, api, make):
    _user(client, admin_headers, "ask-mgr", "manager")
    order = make.order(lines=[(make.item(), 5, 2.0)])
    a = api.post("/api/asks/", json={"entity_type": "customer_order", "entity_id": order["id"], "user": "ask-mgr", "action": "review"})
    mh = {"Authorization": f"Bearer {AuthService.create_access_token({'sub': 'ask-mgr'})}"}
    assert client.post(f"/api/asks/{a['id']}/withdraw", headers=mh).status_code == 403   # only the asker takes it back
    d = client.post(f"/api/asks/{a['id']}/done", headers=mh).json()
    assert d["status"] == "done" and d["done_by"] == "ask-mgr"
    assert any("review done" in t.title for t in _todos("admin"))
    b = api.post("/api/asks/", json={"entity_type": "customer_order", "entity_id": order["id"], "user": "ask-mgr", "action": "other"})
    before = len(_todos("ask-mgr"))
    assert api.post(f"/api/asks/{b['id']}/withdraw")["status"] == "withdrawn"
    assert len(_todos("ask-mgr")) == before - 1      # off their To-Do
    assert client.post("/api/asks/", json={"entity_type": "nope", "entity_id": 1, "user": "ask-mgr"}, headers=admin_headers).status_code == 400


def test_cost_sheet_is_scratch_only(client, admin_headers, api, make):
    it = make.item(cost=8.0)
    po = make.po(lines=[(it, 100, 8.0)])
    s = api.get(f"/api/cost-sheets/po/{po['id']}")
    assert s["data"] is None and s["lines"][0]["quantity"] == 100 and s["lines"][0]["unit_cost"] == 8.0
    data = {"rows": [{"on": True, "what": "Lunch for the crew", "expr": "=6*40", "amount": 240}, {"on": False, "what": "Skip", "amount": 99}],
            "split": "value", "include": {"purchase": True, "extras": True, "lc": {}}}
    api.put(f"/api/cost-sheets/po/{po['id']}", json={"data": data})
    assert api.get(f"/api/cost-sheets/po/{po['id']}")["data"]["rows"][0]["expr"] == "=6*40"
    vendor_id = api.get(f"/api/purchase-orders/{po['id']}")["vendor_id"]
    rows = api.get(f"/api/cost-sheets/?vendor_id={vendor_id}")
    assert rows[0]["po_code"] == po["code"] and rows[0]["extras"] == 240.0      # the unticked row isn't counted
    x = api.post(f"/api/cost-sheets/po/{po['id']}/xlsx", json={"table": [["Item", "Real"], [it["code"], 10.4]]})
    assert x.content[:2] == b"PK"
    # receiving the PO: the lot costs the official $8, never the sheet's extras
    po = api.get(f"/api/purchase-orders/{po['id']}")
    api.post(f"/api/purchase-orders/{po['id']}/mark-ordered")
    api.post(f"/api/purchase-orders/{po['id']}/receive", json={"lines": [{"line_id": l["id"], "quantity": l["quantity"]} for l in po["lines"]]})
    db = SessionLocal()
    try:
        assert db.query(Lot).filter(Lot.item_id == it["id"]).one().unit_cost == 8.0
    finally:
        db.close()
    # money permission: a manager can't open it
    mh = _user(client, admin_headers, "cs-mgr", "manager")
    assert client.get(f"/api/cost-sheets/po/{po['id']}", headers=mh).status_code == 403


def test_grouped_label_drops_the_box_number():
    from app.services.template_engine import _grouped
    band = {"blocks": [{"text": "Box {{label.box}} of {{label.boxes}}"}, {"text": "{{label.item}}"}]}
    g = _grouped(band)
    assert g["blocks"][0]["text"] == "{{label.boxes}} Boxes" and g["blocks"][1]["text"] == "{{label.item}}"
    assert band["blocks"][0]["text"].startswith("Box")       # the design itself is untouched


def test_find_record_for_a_dropped_file(api, make):
    po = make.po(lines=[(make.item(), 1, 1.0)])
    assert api.get(f"/api/attachments/find?entity_type=purchase_order&code={po['code'].lower()}")["id"] == po["id"]
    api.get("/api/attachments/find?entity_type=purchase_order&code=NOPE999", expect=404)
    types = api.get("/api/types/?list=attachment&all=true")["options"]
    assert any(o["key"] == "customs" and "purchase_order" in (o["scopes"] or []) for o in types)
