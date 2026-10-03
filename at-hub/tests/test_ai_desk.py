"""AI Desk without the AI: the instruction decides the kind, numbers in the document find the record."""
import io
import random

from reportlab.pdfgen import canvas


def pdf_with(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i, line in enumerate(text.splitlines()):
        c.drawString(50, 800 - 14 * i, line)
    c.save()
    return buf.getvalue()


def test_vendor_invoice_found_by_so_number_and_added(make, api):
    a = make.item()
    so = f"SO{random.randint(100000, 999999)}"
    po = make.po(lines=[(a, 10, 2)], vendor_so_number=so)
    doc = pdf_with(f"INVOICE 77123\nYour order {so}\nTotal 20.00")
    r = api.post("/api/ai-desk/analyze", data={"instruction": "these are vendor invoices", "kind": "mtr"},
                 files={"file": ("inv.pdf", doc, "application/pdf")})
    assert r["kind"] == "mtr" and r["target"]["id"] == po["id"]  # a chosen kind wins over the instruction
    res = api.post("/api/ai-desk/apply", data={"action": "add_bill", "kind": "vendor_invoice", "target_type": "purchase_order",
                                               "target_code": po["code"], "bill": '{"bill_number": "77123", "amount": 20}'},
                   files={"file": ("inv.pdf", doc, "application/pdf")})
    assert res["done"] == "bill"
    full = api.get(f"/api/purchase-orders/{po['id']}")
    assert [b["bill_number"] for b in full["bills"]] == ["77123"]
    att = api.get(f"/api/attachments/?entity_type=purchase_order&entity_id={po['id']}")
    assert [x["category"] for x in att] == ["vendor_invoice"]


def test_customer_po_attached_to_its_order(make, api):
    a = make.item()
    num = str(random.randint(5_000_000, 9_999_999))
    o = make.order(lines=[(a, 1, 1)], po_number=num)
    doc = pdf_with(f"PURCHASE ORDER {num}\nShip to: somewhere")
    r = api.post("/api/ai-desk/analyze", data={"kind": "customer_po"}, files={"file": ("po.pdf", doc, "application/pdf")})
    assert r["target"]["id"] == o["id"] and r["actions"][0]["id"] == "attach" and r["actions"][0]["ready"]
    api.post("/api/ai-desk/apply", data={"action": "attach", "kind": "customer_po", "target_type": "customer_order", "target_code": o["code"]},
             files={"file": ("po.pdf", doc, "application/pdf")})
    att = api.get(f"/api/attachments/?entity_type=customer_order&entity_id={o['id']}")
    assert [x["category"] for x in att] == ["customer_po"]
