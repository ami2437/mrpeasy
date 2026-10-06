"""To-Do lists, sticky notes on records, the calendar and Go-Live Cleanup (2026-10-06)."""
from datetime import datetime, timedelta

from app.services import clock


def test_todo_smart_lists(api):
    today = clock.today().strftime("%Y-%m-%d")
    lists = api.get("/api/todo/lists")
    assert lists and lists[0]["name"] == "Reminders"
    shared = api.post("/api/todo/lists", json={"name": "Warehouse", "shared": True})
    a = api.post("/api/todo/items", json={"title": "Call Hudson about PO", "due_date": today, "due_time": "14:30", "priority": 3})
    b = api.post("/api/todo/items", json={"title": "Count bolts", "list_id": shared["id"], "flagged": True})
    assert a["due_at"].endswith("Z") and a["due_date"].startswith(today)
    assert clock.local(datetime.fromisoformat(a["due_at"][:-1]), "America/Chicago").strftime("%H:%M") == "14:30"
    assert a["id"] in [i["id"] for i in api.get("/api/todo/items?view=today")]
    assert b["id"] in [i["id"] for i in api.get("/api/todo/items?view=flagged")]
    assert b["id"] not in [i["id"] for i in api.get("/api/todo/items?view=today")]
    api.post(f"/api/todo/items/{a['id']}/toggle")
    assert a["id"] in [i["id"] for i in api.get("/api/todo/items?view=completed")]
    api.post("/api/todo/items", json={"title": "  "}, expect=400)


def test_sticky_note_on_an_order_reminds(make, api):
    o = make.order(lines=[(make.item(), 5, 1)])
    today = clock.today().strftime("%Y-%m-%d")
    n = api.post("/api/notes", json={"entity_type": "customer_order", "entity_id": o["id"], "text": "Customer wants a call before shipping",
                                     "color": "pink", "remind_date": today})
    assert [x["id"] for x in api.get(f"/api/notes?entity_type=customer_order&entity_id={o['id']}")] == [n["id"]]
    assert api.get("/api/notes/counts?entity_type=customer_order")[str(o["id"])] == 1
    assert any(i["kind"] == "note" and i["id"] == n["id"] for i in api.get("/api/todo/items?view=today"))
    cal = api.get(f"/api/calendar?start={today}&end={today}")
    assert any(e["kind"] == "note" and e["link"].endswith(f"id={o['id']}") for e in cal)
    api.post(f"/api/notes/{n['id']}/done")
    assert not api.get("/api/notes/counts?entity_type=customer_order").get(str(o["id"]))
    api.post("/api/notes", json={"entity_type": "invoice", "entity_id": 1, "text": "x"}, expect=400)


def test_calendar_shows_due_deliveries(make, api):
    day = (datetime.utcnow() + timedelta(days=3)).strftime("%Y-%m-%d")
    o = make.order(lines=[(make.item(), 5, 1)], delivery_date=day)
    cal = api.get(f"/api/calendar?start={day}&end={day}")
    assert any(e["kind"] == "delivery" and e["link"].endswith(f"id={o['id']}") for e in cal)
    api.get("/api/calendar?start=2026-01-01&end=2026-06-01", expect=400)


def test_golive_cutoff_and_dismiss(make, api):
    o = make.order(lines=[(make.item(), 5, 1)], delivery_date="2020-01-01")  # long past due -> late_orders
    late = lambda: [r for s in api.get("/api/reports/action-items") if s["key"] == "late_orders" for r in s["rows"] if r["id"] == o["id"]]
    assert late()
    api.put("/api/golive/cutoff", json={"date": (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d"), "sections": ["late_orders"]})
    assert not late()
    api.put("/api/golive/cutoff", json={"date": None, "sections": ["late_orders"]})
    assert late()
    api.post("/api/golive/dismiss", json={"items": [{"key": "late_orders", "id": o["id"]}]})
    assert not late()
    d = [x for x in api.get("/api/golive/")["dismissed"] if x["record_id"] == o["id"]]
    api.post("/api/golive/restore", json={"ids": [d[0]["id"]]})
    assert late()
