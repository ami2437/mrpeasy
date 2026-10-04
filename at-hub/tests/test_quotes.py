"""Quotes: paste RFQ text -> lines with items and prices from history; PDF; convert to an order."""
from app.services.quotes import split_qty


def test_split_qty():
    assert split_qty("500 pcs 5/8-11 x 2 A325 HDG hex bolt") == (500, "5/8-11 x 2 A325 HDG hex bolt")
    assert split_qty("1,000 - 5/8 F436 washers HDG") == (1000, "5/8 F436 washers HDG")
    assert split_qty("Item 15343 qty 200")[0] == 200
    assert split_qty("5/8-11 x 2 hex bolt\t250")[0] == 250
    assert split_qty("5/8-11 x 2 hex bolt") == (None, "5/8-11 x 2 hex bolt")  # a size is never a quantity


def test_paste_price_convert(make, api, client, admin_headers):
    a = make.item(title="BOLT HH 5/8-11 X 2 A325 HDG", price=1.10)
    c = make.customer()
    make.order(customer=c, lines=[(a, 10, 1.25)])  # this customer last paid 1.25
    rows = api.post("/api/quotes/parse", json={"customer_id": c["id"], "text": f"Hi,\nplease quote\n300 pcs {a['code']} hex bolts\nThanks"})
    assert len(rows) == 1 and rows[0]["item_id"] == a["id"] and rows[0]["quantity"] == 300
    assert rows[0]["price"]["price"] == 1.25 and "this customer" in rows[0]["price"]["basis"]
    q = api.post("/api/quotes/", json={"customer_id": c["id"], "customer_ref": "RFQ 77", "lines": [
        {"item_id": a["id"], "quantity": 300, "unit_price": 1.25, "source_text": rows[0]["source"]},
        {"description": "Custom eye bolt (new item)", "quantity": 5, "unit_price": 9}]})
    assert q["code"].startswith("Q") and q["total"] == 420.0
    assert client.get(f"/api/quotes/{q['id']}/pdf", headers=admin_headers).content[:4] == b"%PDF"
    api.post(f"/api/quotes/{q['id']}/convert", json={}, expect=400)  # line 2 has no item yet
    q["lines"][1]["item_id"] = make.item()["id"]
    api.put(f"/api/quotes/{q['id']}", json={"customer_id": c["id"], "lines": q["lines"]})
    r = api.post(f"/api/quotes/{q['id']}/convert", json={"po_number": "PO-FROM-Q"})
    order = api.get(f"/api/customer-orders/{r['order_id']}")
    assert [l["quantity"] for l in order["lines"]] == [300, 5] and order["po_number"] == "PO-FROM-Q"
    assert api.get(f"/api/quotes/{q['id']}")["status"] == "converted"


def test_price_disagreement_and_quote_history(make, api):
    a = make.item(title="NUT HEX 5/8-11 A563 HDG", price=0.30)
    c, other = make.customer(), make.customer()
    make.order(customer=c, lines=[(a, 100, 0.40)])  # this customer last paid 0.40
    q1 = api.post("/api/quotes/", json={"customer_id": c["id"], "lines": [{"item_id": a["id"], "quantity": 100, "unit_price": 0.45}]})
    api.post("/api/quotes/", json={"customer_id": other["id"], "lines": [{"item_id": a["id"], "quantity": 50, "unit_price": 0.50}]})
    p = api.get(f"/api/quotes/price/{a['id']}?customer_id={c['id']}")
    assert p["price"] == 0.40 and "last sold to this customer" in p["basis"]
    assert [o["price"] for o in p["others"]] == [0.45] and q1["code"] in p["others"][0]["basis"]  # the newer quote isn't hidden
    # editing q1 itself: its own price isn't an "other" price
    assert api.get(f"/api/quotes/price/{a['id']}?customer_id={c['id']}&exclude_quote_id={q1['id']}")["others"] == []
    assert api.post("/api/quotes/prices", json={"customer_id": c["id"], "item_ids": [a["id"]]})[str(a["id"])]["price"] == 0.40
    hist = api.get(f"/api/quotes/item-history/{a['id']}?customer_id={c['id']}")
    assert sorted((h["price"], h["mine"]) for h in hist) == [(0.45, True), (0.50, False)]


def test_email_quote(make, api, monkeypatch):
    from app.config.settings import settings
    from app.services import email as email_service
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self, **k): pass
        def login(self, *a): pass
        def send_message(self, msg, to_addrs): sent.append((msg, to_addrs))

    monkeypatch.setattr(settings, "smtp_host", "smtp.test")
    monkeypatch.setattr(settings, "smtp_username", "sales@test.com")
    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)
    c = make.customer()
    q = api.post("/api/quotes/", json={"customer_id": c["id"], "lines": [{"item_id": make.item()["id"], "quantity": 10, "unit_price": 2}]})
    api.post(f"/api/quotes/{q['id']}/email", json={"to": "bad-address", "subject": "x", "body": "x"}, expect=400)
    r = api.post(f"/api/quotes/{q['id']}/email", json={"to": "buyer@cust.com", "subject": f"Quote {q['code']}", "body": "Hi"})
    assert r["status"] == "sent" and r["emails"][0]["to"] == "buyer@cust.com"
    assert sent[0][1] == ["buyer@cust.com"]
    assert any(p.get_filename() == f"Quote-{q['code']}.pdf" for p in sent[0][0].iter_attachments())
