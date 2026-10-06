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


def test_show_hide_leaves_parts_out():
    """The designer's checklist: a hidden section (and its parts), a ticked-off field's lines and a hidden column don't print."""
    from app.services import template_engine, template_starters
    spec = dict(template_starters.starters("invoice"))["coastal"]
    groups = {b.get("group") for b in spec["header"]["blocks"] + spec["summary"]["blocks"]}
    assert {"Logo", "Bill to", "Key facts: Terms", "Totals: Tax", "Notes"} <= groups
    spec["hidden"] = ["Logo", "Totals"]
    od = next(b for b in spec["header"]["blocks"] if b.get("group") == "Order details" and "{{order.job_number}}" in (b.get("text") or ""))
    od["hide_fields"] = ["order.job_number"]
    spec["table"]["columns"][2]["hidden"] = True
    v = template_engine.visible_spec(spec)
    left = [b for k in ("header", "summary") for b in v[k]["blocks"]]
    assert not any(b["type"] == "image" for b in left)
    assert not any((b.get("group") or "").startswith("Totals") for b in left)
    assert "job_number" not in next(b for b in left if b["id"] == od["id"])["text"]
    assert [c["key"] for c in v["table"]["columns"]] == ["line_no", "item_code_desc", "price", "amount"]
    ctx = {"order": {"code": "C1", "job_number": "JOB-77"}, "totals": {"subtotal": "$9.00", "total": "$9.00"}, "doc": {"title": "INVOICE"}}
    text = _text(template_engine.render(spec, ctx, [{"line_no": "1", "item_code": "X", "qty": "5", "price": "$1", "amount": "$5"}]))
    assert "JOB-77" not in text and "Subtotal" not in text and "C1" in text


def test_portal_pallet_table_and_shipping_total(api, make):
    """Portal design: pallet rows (items, weight, size) for the packing list; invoice shipping split from the subtotal."""
    from app.config.database import SessionLocal
    from app.models import Invoice, Shipment
    from app.services import doc_context, template_engine, template_starters
    a, b = make.item(price=2), make.item(price=3)
    make.stock(a, 10)
    make.stock(b, 10)
    sh = make.ship(make.order(lines=[(a, 5, 2), (b, 4, 3)]))
    boxes = [{"order_line_id": bx["order_line_id"], "item_id": bx["item_id"], "box_number": i + 1, "quantity_in_box": bx["quantity_in_box"],
              "pallet_number": "P1"} for i, bx in enumerate(sh["boxes"])]
    api.put(f"/api/shipments/{sh['id']}/boxes", json={"boxes": boxes})
    api.put(f"/api/shipments/{sh['id']}/pallet-weights", json={"pallets": [{"pallet_number": "P1", "weight": 250, "dimensions": "48 x 40 x 50"}]})
    inv = make.invoice(sh, shipping=40)
    db = SessionLocal()
    try:
        ctx, rows = doc_context.build(db, "packing_list", db.get(Shipment, sh["id"]))
        assert ctx["_pallet_rows"] == [{"pallet": "P1", "items": f"{a['code']}, {b['code']}", "weight": "250", "dimensions": "48 x 40 x 50",
                                        "po": ctx["order"]["po_number"]}]
        assert template_engine.render(template_starters.portal_packing_list(), ctx, rows)[:4] == b"%PDF"
        ctx, rows = doc_context.build(db, "invoice", db.get(Invoice, inv["id"]))
        assert (ctx["totals"]["items_subtotal"], ctx["totals"]["shipping"], ctx["totals"]["total"]) == ("$22.00", "$40.00", "$62.00")
        assert [r["_shipping"] for r in rows] == [False, False, True]
        assert template_engine.render(template_starters.portal_invoice(), ctx, rows)[:4] == b"%PDF"
    finally:
        db.close()
