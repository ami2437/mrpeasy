"""Line notes: typed on an order line, carried to the invoice, printed unless switched off."""
import io

from pypdf import PdfReader


def pdf_text(client, headers, url):
    r = client.get(url, headers=headers)
    assert r.status_code == 200
    return "".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)


def test_order_line_note_carries_to_invoice_and_prints(make, api, client, admin_headers):
    a, b = make.item(price=2), make.item(price=3)
    make.stock(a, 10); make.stock(b, 10)
    o = make.customer()
    order = api.post("/api/customer-orders/", json={"customer_id": o["id"], "lines": [
        {"item_id": a["id"], "quantity": 10, "unit_price": 2, "notes": "Zinc plated per drawing"},
        {"item_id": b["id"], "quantity": 10, "unit_price": 3, "notes": "Internal only", "print_notes": False}]})
    assert [l["notes"] for l in order["lines"]] == ["Zinc plated per drawing", "Internal only"]
    order = api.post(f"/api/customer-orders/{order['id']}/confirm")
    sh = make.ship(order)
    inv = make.invoice(sh)
    notes = {l["notes"]: l["print_notes"] for l in inv["lines"] if l["item_id"]}
    assert notes == {"Zinc plated per drawing": True, "Internal only": False}

    for url in (f"/api/invoices/{inv['id']}/pdf", f"/api/shipments/{sh['id']}/packing-list.pdf"):
        text = pdf_text(client, admin_headers, url)
        assert "Zinc plated per drawing" in text and "Internal only" not in text
        assert "Zinc plated" not in pdf_text(client, admin_headers, url + ("&" if "?" in url else "?") + "notes=false")


def test_line_note_edit_and_clear(make, api):
    a = make.item(price=1)
    order = make.order(lines=[(a, 5, 1)], confirm=False)
    lid = order["lines"][0]["id"]
    order = api.put(f"/api/customer-orders/{order['id']}/lines/{lid}", json={"notes": "  call before delivery  ", "print_notes": False})
    assert order["lines"][0]["notes"] == "call before delivery" and order["lines"][0]["print_notes"] is False
    order = api.put(f"/api/customer-orders/{order['id']}/lines/{lid}", json={"notes": ""})
    assert order["lines"][0]["notes"] is None and order["lines"][0]["print_notes"] is False
    po = make.po(lines=[(a, 4, 1)])
    po = api.put(f"/api/purchase-orders/{po['id']}/lines/{po['lines'][0]['id']}", json={"notes": "Cert required"})
    assert po["lines"][0]["notes"] == "Cert required"
