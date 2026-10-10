"""AI reads that aren't sure of every line: created in Validation with the matched lines, the rest waiting for an item;
Ask Claude on the order reader; the customer's RFQ PDF read into quote lines and kept on the quote."""
import pytest

from app.services import ai_orders


def _draft(cust, lines, po=None):
    return {"model": "test", "template": None, "customer_name": cust["name"], "customer": {"customer_id": cust["id"], "confidence": 1},
            "po_number": po, "order_date": None, "delivery_date": None, "job_number": "J-1", "ship_to_address": None, "notes": None,
            "lines": lines, "problems": []}


@pytest.fixture
def fake_reader(monkeypatch):
    box = {}
    monkeypatch.setattr(ai_orders, "extract_order", lambda db, data, engine=None, text=None: {**box["draft"], "engine_seen": engine})
    return box


def test_unsure_read_created_for_validation_and_finished(make, api, client, admin_headers, fake_reader):
    cust, a, b = make.customer(), make.item(price=2), make.item(price=3)
    from tests.builders import uid
    po = uid("PO")
    fake_reader["draft"] = _draft(cust, [
        {"item_code": a["code"], "description": "known", "quantity": 4, "unit_price": 2, "item_id": a["id"], "match": "code", "candidates": []},
        {"item_code": "THEIR-9", "description": "mystery bolt", "quantity": 6, "unit_price": 5, "item_id": None, "match": None,
         "candidates": [{"item_id": b["id"], "code": b["code"], "title": b["title"], "score": 0.7}]}], po=po)
    files = {"file": ("po.pdf", b"%PDF-1.4 test", "application/pdf")}
    # a plain create refuses (needs a look) ...
    r = client.post("/api/file-matcher/create-upload", headers=admin_headers, data={"kind": "customer", "rel": "po.pdf"}, files=files)
    assert r.status_code == 400 and "Create For Validation" in r.json()["detail"]
    # ... for validation it's made, with the sure line on it and the other waiting
    r = client.post("/api/file-matcher/create-upload", headers=admin_headers, data={"kind": "customer", "rel": "po.pdf", "validation": "true"},
                    files={"file": ("po.pdf", b"%PDF-1.4 test", "application/pdf")})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["validation"] and got["waiting"] == 1
    o = api.get(f"/api/customer-orders/{got['record_id']}")
    assert o["status"] == "validation" and o["ai_source"] == "po.pdf" and len(o["lines"]) == 1
    assert o["ai_pending_lines"][0]["item_code"] == "THEIR-9"
    assert any(f["category"] == "customer_po" for f in api.get(f"/api/attachments/?entity_type=customer_order&entity_id={o['id']}"))
    api.post(f"/api/customer-orders/{o['id']}/validate", json={"confirm": False}, expect=400)        # a line still waits
    o = api.post(f"/api/customer-orders/{o['id']}/ai-pending/0/match", json={"item_id": b["id"]})
    assert o["ai_pending_lines"] == [] and len(o["lines"]) == 2
    line = next(l for l in o["lines"] if l["item_id"] == b["id"])
    assert line["quantity"] == 6 and line["unit_price"] == 5                                          # the PO's qty and price kept
    o = api.post(f"/api/customer-orders/{o['id']}/validate", json={"confirm": False})
    assert o["status"] == "draft"


def test_unknown_customer_or_duplicate_po_still_refused(make, api, client, admin_headers, fake_reader):
    cust, a = make.customer(), make.item()
    d = _draft(cust, [{"item_code": "X", "description": "x", "quantity": 1, "item_id": None, "candidates": []}])
    d["customer"] = {"customer_id": None}
    fake_reader["draft"] = d
    r = client.post("/api/file-matcher/create-upload", headers=admin_headers, data={"kind": "customer", "rel": "x.pdf", "validation": "true"},
                    files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 400 and "Can't create it" in r.json()["detail"]


def test_drop_a_waiting_line_and_no_matched_lines(make, api, client, admin_headers, fake_reader):
    cust = make.customer()
    fake_reader["draft"] = _draft(cust, [{"item_code": "Q-1", "description": "unknown", "quantity": 2, "unit_price": 1, "item_id": None, "candidates": []}])
    r = client.post("/api/file-matcher/create-upload", headers=admin_headers, data={"kind": "customer", "rel": "q.pdf", "validation": "true"},
                    files={"file": ("q.pdf", b"%PDF-1.4", "application/pdf")})
    o = api.get(f"/api/customer-orders/{r.json()['record_id']}")
    assert o["status"] == "validation" and o["lines"] == [] and o["job_number"] == "J-1"
    o = api.post(f"/api/customer-orders/{o['id']}/ai-pending/0/discard")
    assert o["ai_pending_lines"] == []
    api.post(f"/api/customer-orders/{o['id']}/ai-pending/0/discard", expect=400)


def test_ask_claude_reaches_the_reader(make, api, client, admin_headers, fake_reader):
    fake_reader["draft"] = _draft(make.customer(), [])
    r = client.post("/api/ai-orders/extract", headers=admin_headers, data={"engine": "claude"}, files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 200 and r.json()["engine_seen"] == "claude"


def test_rfq_pdf_into_quote_lines_and_kept_on_the_quote(make, api, client, admin_headers, fake_reader):
    cust, a = make.customer(), make.item(price=4)
    fake_reader["draft"] = _draft(cust, [
        {"item_code": a["code"], "description": "bolt", "quantity": 50, "unit_price": None, "item_id": a["id"], "match": "code", "candidates": []},
        {"item_code": None, "description": "odd washer", "quantity": 10, "item_id": None, "candidates": []}], po="RFQ-77")
    r = client.post("/api/quotes/ai-read", headers=admin_headers, files={"file": ("rfq.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["customer"]["customer_id"] == cust["id"] and got["reference"] == "RFQ-77"
    assert got["lines"][0]["item_id"] == a["id"] and got["lines"][0]["quantity"] == 50 and "price" in got["lines"][0]
    assert got["lines"][1]["item_id"] is None
    q = api.post("/api/quotes/", json={"customer_id": cust["id"], "lines": [{"item_id": a["id"], "quantity": 50, "unit_price": 4}]})
    r = client.post("/api/attachments/", headers=admin_headers, data={"entity_type": "quote", "entity_id": str(q["id"]), "category": "customer_rfq"},
                    files={"files": ("rfq.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 200, r.text
    assert api.get(f"/api/attachments/?entity_type=quote&entity_id={q['id']}")[0]["category"] == "customer_rfq"


def test_vendor_document_for_validation(make, api):
    from app.config.database import SessionLocal
    from app.services import ai_pending
    v, a, b = make.vendor(), make.item(), make.item()
    d = {"vendor": {"vendor_id": v["id"]}, "document_number": "SO-1", "expected_date": None, "notes": None, "lines": [
        {"vendor_item_code": "V-A", "description": "known", "quantity": 3, "unit_price": 1.5, "item_id": a["id"]},
        {"vendor_item_code": "V-B", "description": "new to us", "quantity": 7, "unit_price": 2.25, "item_id": None, "candidates": []}]}
    db = SessionLocal()
    try:
        po = ai_pending.create_for_validation(db, "vendor", d, "so.pdf", "admin")
        db.commit()
        pid = po.id
    finally:
        db.close()
    p = api.get(f"/api/purchase-orders/{pid}")
    assert p["status"] == "validation" and len(p["lines"]) == 1 and p["ai_pending_lines"][0]["item_code"] == "V-B"
    p = api.post(f"/api/purchase-orders/{pid}/ai-pending/0/match", json={"item_id": b["id"]})
    line = next(l for l in p["lines"] if l["item_id"] == b["id"])
    assert line["quantity"] == 7 and line["unit_cost"] == 2.25 and line["vendor_item_code"] == "V-B" and not p["ai_pending_lines"]
    assert api.post(f"/api/purchase-orders/{pid}/validate", json={"ordered": False})["status"] == "draft"
