"""Undoing a shipment voids its invoice: a draft at once, a sent one only after the steps; PODs stay on record."""


def _shipped(make):
    a = make.item(price=2)
    make.stock(a, 50)
    o = make.order(lines=[(a, 10, 2)])
    return o, make.ship(o)


def test_draft_invoice_voided_with_the_undo(make, api):
    o, sh = _shipped(make)
    inv = make.invoice(sh)
    plan = api.get(f"/api/shipments/{sh['id']}/undo-plan")
    assert plan["invoice"]["code"] == inv["code"] and plan["steps"] == []
    assert api.post(f"/api/shipments/{sh['id']}/unship")["status"] == "new"
    v = api.get(f"/api/invoices/{inv['id']}")
    assert v["status"] == "void" and sh["code"] in v["void_reason"] and v["voided_by"]


def test_sent_invoice_needs_the_steps(make, api):
    o, sh = _shipped(make)
    inv = make.invoice(sh)
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"})
    api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": 5})
    plan = api.get(f"/api/shipments/{sh['id']}/undo-plan")
    assert plan["steps"] == ["remove_payments", "notify_customer", "reason"] and plan["invoice"]["payments"][0]["amount"] == 5
    api.post(f"/api/shipments/{sh['id']}/unship", json={"reason": "wrong items", "customer_notified": True}, expect=400)  # payment first
    api.delete(f"/api/invoices/{inv['id']}/payments/{plan['invoice']['payments'][0]['id']}")
    api.post(f"/api/shipments/{sh['id']}/unship", json={}, expect=400)  # customer not told, no reason
    api.post(f"/api/shipments/{sh['id']}/unship", json={"reason": "wrong items", "customer_notified": True})
    v = api.get(f"/api/invoices/{inv['id']}")
    assert v["status"] == "void" and v["void_reason"] == "wrong items"


def test_pods_kept_when_undone_and_deleted(make, api, client, admin_headers):
    o, sh = _shipped(make)
    r = client.post("/api/attachments/", headers=admin_headers, data={"entity_type": "shipment", "entity_id": str(sh["id"]), "category": "pod"},
                    files={"files": ("pod.pdf", b"%PDF-1.4 test", "application/pdf")})
    assert r.status_code == 200
    inv = make.invoice(sh)
    api.post(f"/api/shipments/{sh['id']}/unship")
    assert len(api.get(f"/api/attachments/?entity_type=shipment&entity_id={sh['id']}")) == 1  # still on the shipment
    api.delete(f"/api/shipments/{sh['id']}")  # only a void invoice referenced it
    moved = api.get(f"/api/attachments/?entity_type=customer_order&entity_id={o['id']}")
    assert [a["category"] for a in moved] == ["pod"] and sh["code"] in moved[0]["note"]
    assert api.get(f"/api/invoices/{inv['id']}")["status"] == "void"  # history kept
