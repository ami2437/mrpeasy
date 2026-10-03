"""Admin tasks: suggested from the data, closed by the data, plus manual ones."""


def test_suggested_payment_task_opens_and_closes(make, api):
    a = make.item()
    po = make.po(lines=[(a, 10, 2)])
    api.post(f"/api/purchase-orders/{po['id']}/bills", json={"bill_number": "INV-T1", "amount": 20})
    tasks = api.get("/api/tasks/")
    t = next(t for t in tasks if t["key"] == f"po-payment:{po['code']}")
    assert t["status"] == "open" and "$20.00 billed" in t["detail"]
    api.post(f"/api/purchase-orders/{po['id']}/payments", json={"amount": 20})
    t = next(t for t in api.get("/api/tasks/") if t["key"] == f"po-payment:{po['code']}")
    assert t["status"] == "done" and "data shows" in t["done_by"]


def test_manual_task(api):
    t = api.post("/api/tasks/", json={"title": "Call Hudson about PO 4108494", "category": "Follow up"})
    t = api.put(f"/api/tasks/{t['id']}", json={"status": "done", "note": "left voicemail"})
    assert t["status"] == "done" and t["done_by"] == "admin"
    api.delete(f"/api/tasks/{t['id']}")
