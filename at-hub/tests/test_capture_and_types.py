"""Quick capture (a PO document now, the details later) and the types & tags people can add to."""
import pytest

from tests.builders import uid

PDF = ("po.pdf", b"%PDF-1.4 quick capture test", "application/pdf")


def _capture_order(client, admin_headers, customer, po_number=None):
    r = client.post("/api/customer-orders/capture", headers=admin_headers, files={"files": PDF},
                    data={"customer_id": str(customer["id"]), **({"po_number": po_number} if po_number else {})})
    assert r.status_code == 200, r.text
    return r.json()


def _capture_po(client, admin_headers, vendor, category="vendor_quote"):
    r = client.post("/api/purchase-orders/capture", headers=admin_headers, files={"files": PDF},
                    data={"vendor_id": str(vendor["id"]), "vendor_so_number": "SO-1", "category": category})
    return r


# ---- quick capture: customer orders ----
def test_captured_order_waits_for_validation(make, api, client, admin_headers):
    c = make.customer()
    o = _capture_order(client, admin_headers, c, po_number=uid("CAP"))
    assert o["status"] == "validation" and o["lines"] == []
    files = api.get(f"/api/attachments/?entity_type=customer_order&entity_id={o['id']}")
    assert [f["category"] for f in files] == ["customer_po"]
    api.post(f"/api/customer-orders/{o['id']}/confirm", expect=400)  # not before it's checked
    api.post(f"/api/customer-orders/{o['id']}/validate", json={}, expect=400)  # no lines yet
    a = make.item(price=2)
    make.stock(a, 10)
    api.post(f"/api/customer-orders/{o['id']}/lines", json={"item_id": a["id"], "quantity": 5, "unit_price": 2})
    api.post(f"/api/customer-orders/{o['id']}/shipments", json={"lines": [{"line_id": api.get(f"/api/customer-orders/{o['id']}")["lines"][0]["id"],
                                                                           "quantity": 5}]}, expect=400)
    assert api.get(f"/api/customer-orders/{o['id']}")["status"] == "validation"  # adding lines doesn't move it on
    v = api.post(f"/api/customer-orders/{o['id']}/validate", json={})
    assert v["status"] == "draft" and v["validated_by"] == "admin" and v["validated_at"]
    assert api.post(f"/api/customer-orders/{o['id']}/confirm")["status"] == "confirmed"


def test_validate_and_confirm_in_one_go(make, api, client, admin_headers):
    o = _capture_order(client, admin_headers, make.customer())
    a = make.item(price=2)
    api.post(f"/api/customer-orders/{o['id']}/lines", json={"item_id": a["id"], "quantity": 1, "unit_price": 2})
    assert api.post(f"/api/customer-orders/{o['id']}/validate", json={"confirm": True})["status"] == "confirmed"
    api.post(f"/api/customer-orders/{o['id']}/validate", json={}, expect=400)  # only once


def test_validating_catches_a_duplicate_customer_po(make, api, client, admin_headers):
    c, a = make.customer(), make.item(price=2)
    po = uid("DUP")
    make.order(customer=c, lines=[(a, 1, 2)], po_number=po)
    o = _capture_order(client, admin_headers, c, po_number=po)
    api.post(f"/api/customer-orders/{o['id']}/lines", json={"item_id": a["id"], "quantity": 1, "unit_price": 2})
    r = client.post(f"/api/customer-orders/{o['id']}/validate", headers=admin_headers, json={})
    assert r.status_code == 409 and "DUPLICATE_PO" in r.json()["detail"]


def test_capture_needs_a_customer_and_a_proper_file(make, client, admin_headers):
    r = client.post("/api/customer-orders/capture", headers=admin_headers, files={"files": PDF}, data={"customer_id": "999999"})
    assert r.status_code == 400
    r = client.post("/api/customer-orders/capture", headers=admin_headers, files={"files": ("x.exe", b"MZ", "application/octet-stream")},
                    data={"customer_id": str(make.customer()["id"])})
    assert r.status_code == 400


def test_captured_order_is_listed_to_validate(make, api, client, admin_headers):
    o = _capture_order(client, admin_headers, make.customer())
    section = next(s for s in api.get("/api/reports/action-items") if s["key"] == "captured_orders")
    assert o["id"] in [r["id"] for r in section["rows"]]


# ---- quick capture: purchase orders ----
def test_captured_po_waits_for_validation(make, api, client, admin_headers):
    v = make.vendor()
    r = _capture_po(client, admin_headers, v)
    assert r.status_code == 200, r.text
    po = r.json()
    assert po["status"] == "validation" and po["vendor_so_number"] == "SO-1"
    assert [f["category"] for f in api.get(f"/api/attachments/?entity_type=purchase_order&entity_id={po['id']}")] == ["vendor_quote"]
    api.post(f"/api/purchase-orders/{po['id']}/mark-ordered", expect=400)
    a = make.item()
    po = api.post(f"/api/purchase-orders/{po['id']}/lines", json={"item_id": a["id"], "quantity": 10, "unit_cost": 1})
    api.post(f"/api/purchase-orders/{po['id']}/receive", json={"lines": [{"line_id": po["lines"][0]["id"], "quantity": 10}]}, expect=400)
    api.post(f"/api/purchase-orders/{po['id']}/email", json={"to": "v@example.com", "subject": "PO", "body": "x"}, expect=400)
    assert api.get(f"/api/purchase-orders/{po['id']}")["status"] == "validation"
    assert api.post(f"/api/purchase-orders/{po['id']}/validate", json={"ordered": True})["status"] == "ordered"
    section = next(s for s in api.get("/api/reports/action-items") if s["key"] == "captured_pos")
    assert po["id"] not in [r["id"] for r in section["rows"]]


def test_po_capture_document_type_must_fit(make, client, admin_headers):
    assert _capture_po(client, admin_headers, make.vendor(), category="pod").status_code == 400  # a shipment's type


# ---- types & tags ----
@pytest.mark.parametrize("typed,saved", [("packing list", "Packing List"), ("  vendor   PACKING list ", "Vendor Packing List"),
                                         ("bill of lading copy", "Bill of Lading Copy"), ("COC cert", "COC Cert"),
                                         ("rush #2 surcharge!", "Rush Surcharge")])
def test_names_are_tidied(typed, saved):
    from app.services.type_lists import normalize_label
    assert normalize_label(typed) == saved


def test_similar_names_are_flagged(api, client, admin_headers):
    r = client.post("/api/types/", headers=admin_headers, json={"list": "attachment", "label": "vendor packing list", "scopes": ["purchase_order"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "similar"
    assert "Packing List" in [x["label"] for x in r.json()["detail"]["similar"]]
    check = api.get("/api/types/check?list=attachment&label=packing%20lists")
    assert check["label"] == "Packing Lists" and "Packing List" in [x["label"] for x in check["similar"]]
    exact = client.post("/api/types/", headers=admin_headers, json={"list": "attachment", "label": "PACKING list", "scopes": ["shipment"]})
    assert exact.status_code == 409 and exact.json()["detail"]["code"] == "exists"


def test_new_document_type_can_be_used_where_it_belongs(make, api, client, admin_headers):
    label = "Vendor Packing List"
    api.post("/api/types/", json={"list": "attachment", "label": label, "scopes": ["purchase_order"], "force": True}, expect=(200, 409))
    made = next(t for t in api.get("/api/types/?list=attachment&all=true")["options"] if t["label"] == label)
    assert made["key"] == "vendor_packing_list"
    assert "vendor_packing_list" in [t["key"] for t in api.get("/api/types/?list=attachment&scope=purchase_order")["options"]]
    assert "vendor_packing_list" not in [t["key"] for t in api.get("/api/types/?list=attachment&scope=shipment")["options"]]
    po = _capture_po(client, admin_headers, make.vendor(), category="vendor_packing_list")
    assert po.status_code == 200, po.text
    o = _capture_order(client, admin_headers, make.customer())
    r = client.post("/api/attachments/", headers=admin_headers, files={"files": PDF},
                    data={"entity_type": "customer_order", "entity_id": str(o["id"]), "category": "vendor_packing_list"})
    assert r.status_code == 400  # not a customer-order type


def test_hidden_type_is_not_offered_but_old_files_keep_it(api):
    api.post("/api/types/", json={"list": "charge", "label": "Fuel Surcharge", "force": True}, expect=(200, 409))  # may exist from an earlier run
    t = next(x for x in api.get("/api/types/?list=charge&all=true")["options"] if x["label"] == "Fuel Surcharge")
    api.put(f"/api/types/{t['id']}", json={"active": False})
    assert "fuel_surcharge" not in [x["key"] for x in api.get("/api/types/?list=charge")["options"]]
    api.put(f"/api/types/{t['id']}", json={"active": True})
    other = next(x for x in api.get("/api/types/?list=charge&all=true")["options"] if x["key"] == "other")
    api.put(f"/api/types/{other['id']}", json={"active": False}, expect=400)  # Other always stays


def test_new_charge_type_works_on_a_po(make, api):
    api.post("/api/types/", json={"list": "charge", "label": "Crating", "force": True}, expect=(200, 409))
    a = make.item()
    po = make.po(lines=[(a, 5, 1)])
    po = api.post(f"/api/purchase-orders/{po['id']}/charges", json={"charge_type": "crating", "amount": 12.5})
    assert any(c["charge_type"] == "crating" for c in po["charges"])
    api.post(f"/api/purchase-orders/{po['id']}/charges", json={"charge_type": "made_up", "amount": 1}, expect=400)


def test_payment_methods_come_from_the_list(make, api):
    o, sh, inv = make.sold([(make.item(price=10), 2, 10)])
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"})
    inv = api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": 5, "method": "credit card"})
    assert inv["payments"][-1]["method"] == "credit_card"
    api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": 1, "method": "bitcoin"}, expect=400)
    inv = api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": 1})
    assert inv["payments"][-1]["method"] is None  # blank is fine


def test_only_managers_add_types(client):
    from app.services.auth import AuthService
    from app.config.database import SessionLocal
    from app.models import User
    with SessionLocal() as db:
        if not db.query(User).filter(User.username == "emp_types").first():
            db.add(User(username="emp_types", email="emp_types@example.com", hashed_password="x", role="employee", is_active=True))
            db.commit()
    h = {"Authorization": f"Bearer {AuthService.create_access_token({'sub': 'emp_types'})}"}
    assert client.get("/api/types/?list=attachment", headers=h).status_code == 200
    assert client.post("/api/types/", headers=h, json={"list": "charge", "label": "Sneaky"}).status_code == 403


@pytest.mark.parametrize("a,b,alike", [("Packing Slips", "Packing List", True), ("Vendor Packing List", "Packing List", True),
                                       ("Our Invoice", "Vendor Invoice", True), ("Frieght", "Freight", True),
                                       ("Vendor Invoice", "Vendor Quote / Confirmation", False), ("Fuel Surcharge", "Shipping", False),
                                       ("Customer PO", "Purchase Order", False), ("Bill of Lading", "Proof of Delivery", False)])
def test_what_counts_as_similar(a, b, alike):
    from app.services.type_lists import is_similar
    assert is_similar(a, b) is alike
