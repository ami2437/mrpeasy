"""AI Desk: names first (a vendor's sales order to us is a vendor document even though it says "Sales Order" and names
us as the buyer), one primary action that always works, drafts made in Validation with the file attached, files kept
on the desk until acted on, any file type."""
import io

import pytest

from tests.builders import uid


@pytest.fixture
def fake_reads(monkeypatch):
    """The AI reads, stubbed: box["vendor"] / box["customer"] is what the model 'read'."""
    from app.services import ai_docs, ai_orders
    box = {}

    def vendor_read(db, kind, data, filename, po_id=None, engine="local", text=None):
        return {"kind": kind, **box["vendor"]}

    def customer_read(db, file_bytes, engine=None, text=None):
        return {"customer": {"customer_id": None}, "problems": [], "lines": [], **box["customer"]}
    monkeypatch.setattr(ai_docs, "extract", vendor_read)
    monkeypatch.setattr(ai_orders, "extract_order", customer_read)
    return box


def _drop(client, h, name, text):
    r = client.post("/api/ai-desk/files", headers=h, files=[("files", (name, io.BytesIO(text.encode()), "text/plain"))])
    assert r.status_code == 200, r.text
    return r.json()[0]


def test_vendor_sales_order_is_a_vendor_document_and_creates_a_draft_po(client, admin_headers, api, make, fake_reads):
    v = make.vendor(name=f"Zeiglor Bolt {uid()}")
    it = make.item()
    text = (f"{v['name']}\nSALES ORDER 1964913\nSold To: AMERICAN TRADERS INDUSTRIAL SUPPLIES\n19051 Kenswick Dr #100\n"
            f"Item 79299-HPC-NUT  qty 100\n")
    row = _drop(client, admin_headers, "Ziegler_Sales_Order_1964913.txt", text)
    assert row["status"] == "new" and row["quick"]["side"] == "vendor" and row["quick"]["party"]["name"] == v["name"]
    fake_reads["vendor"] = {"vendor": {"vendor_id": None}, "vendor_name": "AMERICAN TRADERS", "document_number": "1964913",
                            "lines": [{"vendor_item_code": "79299-HPC-NUT", "description": "nut", "quantity": 100, "unit_price": 0.2, "item_id": None},
                                      {"vendor_item_code": "X1", "description": "bolt", "quantity": 5, "unit_price": 1, "item_id": it["id"]}]}
    r = client.post(f"/api/ai-desk/files/{row['id']}/read", headers=admin_headers, data={})
    plan = r.json()["plan"]
    assert plan["kind"] == "vendor_order" and plan["party"]["name"] == v["name"]
    assert plan["primary"]["action"] == "create_po" and v["name"] in plan["primary"]["label"]
    assert any("item picked" in t for t in plan["todo"])
    # the one button: a draft PO in Validation, the sure line on it, the other waiting, the file attached
    done = client.post(f"/api/ai-desk/files/{row['id']}/act", headers=admin_headers, json={"action": "create_po"}).json()
    assert done["status"] == "done" and done["record_type"] == "purchase_order"
    po = api.get(f"/api/purchase-orders/{done['record_id']}")
    assert po["status"] == "validation" and po["vendor_so_number"] == "1964913" and len(po["lines"]) == 1 and len(po["ai_pending_lines"]) == 1
    files = api.get(f"/api/attachments/?entity_type=purchase_order&entity_id={po['id']}")
    assert [f["filename"] for f in files] == ["Ziegler_Sales_Order_1964913.txt"]
    # a second copy of the same SO: offered as "attach to" that PO, not another PO
    row2 = _drop(client, admin_headers, "again.txt", text)
    plan2 = client.post(f"/api/ai-desk/files/{row2['id']}/read", headers=admin_headers, data={}).json()["plan"]
    assert plan2["primary"]["action"] == "attach" and plan2["primary"]["record"]["id"] == po["id"]


def test_customer_po_from_a_spreadsheet_and_the_duplicate_po(client, admin_headers, api, make, fake_reads):
    c, it = make.customer(name=f"Hudsonia Products {uid()}"), make.item()
    csv = f"PURCHASE ORDER,PO-77{uid()}\nVendor,American Traders\nBill To,{c['name']}\nItem,Qty,Price\n{it['code']},10,2.5\n"
    po_no = csv.split(",")[1].split("\n")[0]
    row = client.post("/api/ai-desk/files", headers=admin_headers,
                      files=[("files", ("order.csv", io.BytesIO(csv.encode()), "text/csv"))]).json()[0]
    assert row["family"] == "csv" and row["quick"]["side"] == "customer"
    fake_reads["customer"] = {"customer_name": None, "po_number": po_no,
                              "lines": [{"item_code": it["code"], "quantity": 10, "unit_price": 2.5, "item_id": it["id"], "match": "code"}]}
    plan = client.post(f"/api/ai-desk/files/{row['id']}/read", headers=admin_headers, data={}).json()["plan"]
    assert plan["kind"] == "customer_po" and plan["party"]["id"] == c["id"] and plan["primary"]["action"] == "create_order"
    done = client.post(f"/api/ai-desk/files/{row['id']}/act", headers=admin_headers, json={"action": "create_order"}).json()
    o = api.get(f"/api/customer-orders/{done['record_id']}")
    assert o["status"] == "validation" and o["po_number"] == po_no and o["customer_id"] == c["id"]
    # the same PO again -> attach to that order (and "create another anyway" is there)
    row2 = client.post("/api/ai-desk/files", headers=admin_headers, files=[("files", ("order2.csv", io.BytesIO(csv.encode()), "text/csv"))]).json()[0]
    plan2 = client.post(f"/api/ai-desk/files/{row2['id']}/read", headers=admin_headers, data={}).json()["plan"]
    assert plan2["primary"]["action"] == "attach" and plan2["primary"]["record"]["id"] == o["id"]
    assert any(x.get("allow_duplicate") for x in plan2["options"])
    # the preview of a spreadsheet is a table
    assert client.get(f"/api/ai-desk/files/{row2['id']}/preview", headers=admin_headers).json()["type"] == "table"


def test_vendor_invoice_goes_on_the_po_printed_on_it(client, admin_headers, api, make, fake_reads):
    v, it = make.vendor(name=f"Fastenal Partsco {uid()}"), make.item()
    po = make.po(vendor=v, lines=[(it, 10, 3.0)])
    fake_reads["vendor"] = {"vendor": {"vendor_id": v["id"]}, "invoice_number": f"INV-{uid()}", "total": 30.0, "invoice_date": "2026-10-01",
                            "due_date": "2026-10-31", "po_number": None, "lines": []}
    row = _drop(client, admin_headers, "invoice.txt", f"INVOICE\nInvoice No: X\n{v['name']}\nYour PO: {po['code']}\nAmount due 30.00")
    plan = client.post(f"/api/ai-desk/files/{row['id']}/read", headers=admin_headers, data={}).json()["plan"]
    assert plan["kind"] == "vendor_invoice" and plan["primary"]["action"] == "add_bill" and plan["primary"]["record"]["id"] == po["id"]
    client.post(f"/api/ai-desk/files/{row['id']}/act", headers=admin_headers,
                json={"action": "add_bill", "record_type": "purchase_order", "record_id": po["id"]}).raise_for_status()
    bills = api.get(f"/api/purchase-orders/{po['id']}")["bills"]
    assert len(bills) == 1 and bills[0]["amount"] == 30.0


def test_never_stuck_unknown_file_kept_until_acted_on(client, admin_headers, api, make):
    v = make.vendor()
    row = _drop(client, admin_headers, "mystery.txt", "nothing useful here")
    assert any(r["id"] == row["id"] for r in client.get("/api/ai-desk/files", headers=admin_headers).json())
    # no read at all: still one click (and a pick) from a draft PO with the file on it
    done = client.post(f"/api/ai-desk/files/{row['id']}/act", headers=admin_headers, json={"action": "create_po", "party_id": v["id"]}).json()
    po = api.get(f"/api/purchase-orders/{done['record_id']}")
    assert po["status"] == "validation" and po["vendor_id"] == v["id"]
    # removing a file takes it off the desk
    row2 = _drop(client, admin_headers, "junk.txt", "x")
    assert client.delete(f"/api/ai-desk/files/{row2['id']}", headers=admin_headers).status_code == 204
    assert all(r["id"] != row2["id"] for r in client.get("/api/ai-desk/files", headers=admin_headers).json())


def _pdf(text: str) -> bytes:
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i, line in enumerate(text.splitlines()):
        c.drawString(50, 800 - 14 * i, line)
    c.save()
    return buf.getvalue()


def test_pdf_invoice_found_by_its_so_number(client, admin_headers, api, make, fake_reads):
    import random
    a = make.item()
    so = f"SO{random.randint(100000, 999999)}"
    po = make.po(lines=[(a, 10, 2)], vendor_so_number=so)
    fake_reads["vendor"] = {"vendor": {"vendor_id": None}, "invoice_number": "77123", "total": 20.0, "lines": []}
    row = client.post("/api/ai-desk/files", headers=admin_headers,
                      files=[("files", ("inv.pdf", io.BytesIO(_pdf(f"INVOICE 77123\nYour order {so}\nTotal 20.00")), "application/pdf"))]).json()[0]
    plan = client.post(f"/api/ai-desk/files/{row['id']}/read", headers=admin_headers, data={"kind": "vendor_invoice"}).json()["plan"]
    assert plan["primary"]["action"] == "add_bill" and plan["primary"]["record"]["id"] == po["id"]
    assert client.get(f"/api/ai-desk/files/{row['id']}/thumb", headers=admin_headers).headers["content-type"] == "image/png"


def test_pdf_customer_po_we_already_have_goes_on_its_order(client, admin_headers, api, make, fake_reads):
    import random
    a = make.item()
    num = str(random.randint(5_000_000, 9_999_999))
    o = make.order(lines=[(a, 1, 1)], po_number=num)
    fake_reads["customer"] = {"customer_name": None, "po_number": num, "lines": []}
    row = client.post("/api/ai-desk/files", headers=admin_headers,
                      files=[("files", ("po.pdf", io.BytesIO(_pdf(f"PURCHASE ORDER {num}\nShip to: somewhere")), "application/pdf"))]).json()[0]
    plan = client.post(f"/api/ai-desk/files/{row['id']}/read", headers=admin_headers, data={}).json()["plan"]
    assert plan["kind"] == "customer_po" and plan["primary"]["action"] == "attach" and plan["primary"]["record"]["id"] == o["id"]
    client.post(f"/api/ai-desk/files/{row['id']}/act", headers=admin_headers,
                json={"action": "attach", "record_type": "customer_order", "record_id": o["id"]}).raise_for_status()
    att = api.get(f"/api/attachments/?entity_type=customer_order&entity_id={o['id']}")
    assert [x["category"] for x in att] == ["customer_po"]


def test_vendor_known_by_part_numbers_and_file_name_with_page_number_misread(client, admin_headers, api, make, fake_reads):
    """Ziegler's quote: their name only in the logo (not in the text), our name as 'Sold To', the AI reads the page # as
    the quote #, the ship date comes back as text and no line matches an item -- the draft PO is still made, right."""
    v, it = make.vendor(name=f"Ziegglor Bolt {uid()}"), make.item()
    code = f"62C{uid()}BTA3"
    api.post("/api/purchase-orders/", json={"vendor_id": v["id"], "lines": [{"item_id": it["id"], "quantity": 1, "unit_cost": 1, "vendor_item_code": code}]})
    text = f"Item No. Description Qty\n{code} 5/8-11 X 7 HEX TAP BOLT 200\nQuote No.\nSold To: American Traders LLC\n1\n"
    # their name only in the file name (words joined by "_") -> still them
    named = _drop(client, admin_headers, f"{v['name'].replace(' ', '_')}_Sales_Order_77.txt", "SALES ORDER\nSold To: American Traders LLC\n")
    assert named["quick"]["party"]["id"] == v["id"]
    # just the first word of the name in the file name (the text says nothing) -> still them
    first = _drop(client, admin_headers, f"{v['name'].split()[0]}_Sales_Order_1980403.txt", "HEX FINISH NUT 10000\n")
    assert first["quick"]["party"]["id"] == v["id"]
    # no name anywhere: their part # on it -> them
    row = _drop(client, admin_headers, "Sales_-_Quote_1515258.txt", text)
    assert row["quick"]["side"] == "vendor" and row["quick"]["party"]["id"] == v["id"]
    fake_reads["vendor"] = {"vendor": {"vendor_id": None}, "vendor_name": "American Traders LLC", "document_number": "1", "expected_date": "2026-05-08",
                            "lines": [{"vendor_item_code": "ZZ-NEW", "description": "new thing", "quantity": 5, "unit_price": 2, "item_id": None}]}
    plan = client.post(f"/api/ai-desk/files/{row['id']}/read", headers=admin_headers, data={}).json()["plan"]
    assert plan["party"]["id"] == v["id"] and "part #" in plan["party"]["why"] and "SO 1515258" in plan["facts"]
    done = client.post(f"/api/ai-desk/files/{row['id']}/act", headers=admin_headers, json={"action": "create_po"})
    assert done.status_code == 200, done.text
    po = api.get(f"/api/purchase-orders/{done.json()['record_id']}")
    assert po["vendor_so_number"] == "1515258" and po["expected_date"][:10] == "2026-05-08" and len(po["ai_pending_lines"]) == 1
