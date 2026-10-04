"""Template Designer: starters for every document, admin-only, the default prints (a customer's own first),
preview, labels, and the built-in layout when nothing is set."""
import io

from pypdf import PdfReader


def _text(content):
    return "".join(p.extract_text() for p in PdfReader(io.BytesIO(content)).pages)


def _manager(client):
    from app.config.database import SessionLocal
    from app.models import User
    from app.services.auth import AuthService
    db = SessionLocal()
    if not db.query(User).filter(User.username == "mgr-tpl").first():
        db.add(User(username="mgr-tpl", hashed_password=AuthService.hash_password("x"), role="manager", is_active=True))
        db.commit()
    db.close()
    return {"Authorization": f"Bearer {AuthService.create_access_token({'sub': 'mgr-tpl'})}"}


def test_every_starter_renders(api, client, admin_headers, make):
    a = make.item(price=2)
    make.stock(a, 10)
    o = make.order(lines=[(a, 5, 2)])
    make.invoice(make.ship(o))
    make.po(lines=[(a, 5, 1)])
    api.post("/api/quotes/", json={"customer_id": o["customer_id"], "lines": [{"item_id": a["id"], "quantity": 5, "unit_price": 2}]})
    for t in api.get("/api/templates/types"):
        for s in api.get(f"/api/templates/starters/{t['key']}"):
            r = client.post("/api/templates/preview", headers=admin_headers, json={"doc_type": t["key"], "spec": s["spec"]})
            assert r.status_code == 200 and r.content[:4] == b"%PDF", (t["key"], s["key"], r.text[:200])


def test_admin_only(client):
    mgr = _manager(client)
    assert client.get("/api/templates/", headers=mgr).status_code == 403
    assert client.post("/api/templates/", headers=mgr, json={"doc_type": "invoice", "name": "x", "starter": "blank"}).status_code == 403
    assert client.get("/api/templates/defaults", headers=mgr).status_code == 200  # printing still asks


def test_default_prints_and_customer_default_wins(make, api, client, admin_headers):
    a = make.item(price=3)
    make.stock(a, 20)
    c1, c2 = make.customer(), make.customer()
    o1, o2 = make.order(customer=c1, lines=[(a, 2, 3)]), make.order(customer=c2, lines=[(a, 2, 3)])
    i1, i2 = make.invoice(make.ship(o1)), make.invoice(make.ship(o2))
    url = lambda i: f"/api/invoices/{i['id']}/pdf"
    assert "Thank you for your business" in _text(client.get(url(i1), headers=admin_headers).content)  # built-in layout

    t = api.post("/api/templates/", json={"doc_type": "invoice", "name": "House style", "starter": "executive"})
    t["spec"]["header"]["blocks"].append({"type": "text", "x": 0, "y": 2.8, "w": 4, "h": 0.2, "text": "HOUSE-STYLE {{doc.number}}"})
    api.put(f"/api/templates/{t['id']}", json={"spec": t["spec"]})
    api.post(f"/api/templates/{t['id']}/default", json={"on": True})
    assert f"HOUSE-STYLE {i1['code']}" in _text(client.get(url(i1), headers=admin_headers).content)

    own = api.post("/api/templates/", json={"doc_type": "invoice", "name": "For c2", "starter": "modern", "customer_id": c2["id"]})
    own["spec"]["header"]["blocks"].append({"type": "text", "x": 0, "y": 2.8, "w": 4, "h": 0.2, "text": "CUSTOMER-TWO"})
    api.put(f"/api/templates/{own['id']}", json={"spec": own["spec"]})
    api.post(f"/api/templates/{own['id']}/default", json={"on": True})
    assert "CUSTOMER-TWO" in _text(client.get(url(i2), headers=admin_headers).content)       # c2's own
    assert "CUSTOMER-TWO" not in _text(client.get(url(i1), headers=admin_headers).content)   # everyone else: house style
    api.post(f"/api/templates/{t['id']}/default", json={"on": False})
    api.post(f"/api/templates/{own['id']}/default", json={"on": False})
    assert "HOUSE-STYLE" not in _text(client.get(url(i1), headers=admin_headers).content)    # back to built-in


def test_box_labels_print_with_the_default(api, client, admin_headers):
    t = api.post("/api/templates/", json={"doc_type": "box_label", "name": "Big PO", "starter": "big_po"})
    r = client.post("/api/templates/render-labels", headers=admin_headers, json={"doc_type": "box_label", "labels": [{"po": "PO-1"}]})
    assert r.status_code == 404  # nothing is the default yet: the label screens use their built-in label
    api.post(f"/api/templates/{t['id']}/default", json={"on": True})
    assert api.get("/api/templates/defaults").get("box_label")
    r = client.post("/api/templates/render-labels", headers=admin_headers, json={"doc_type": "box_label", "labels": [
        {"customer": "Acme", "po": "PO #4179869", "item_code": "56014-HPC", "qty": 225, "box": 1, "boxes": 2},
        {"customer": "Acme", "po": "4179869", "item_code": "56014-HPC", "qty": 75, "box": 2, "boxes": 2}]})
    text = _text(r.content)
    assert len(PdfReader(io.BytesIO(r.content)).pages) == 2 and "4179869" in text and "Box 2 of 2" in text
    api.post(f"/api/templates/{t['id']}/default", json={"on": False})


def test_empty_field_lines_are_dropped():
    from app.services.template_engine import fill
    ctx = {"customer": {"name": "Acme", "contact": ""}}
    assert fill("{{customer.name}}\nAttn: {{customer.contact}}\nThanks", ctx) == "Acme\nThanks"
    assert fill("<b>{{customer.name}}</b>", {"customer": {"name": "A & B"}}) == "<b>A &amp; B</b>"
