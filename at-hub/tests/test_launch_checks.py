"""Pre-launch bug sweep (2026-10-05): each test is a bug found by driving the real API, kept so it stays fixed."""
from datetime import datetime, timedelta


def _booked(make, api, qty=10):
    """Two lines (bolt + nut) booked, confirmed and fully picked on one shipment."""
    a, b = make.item(), make.item()
    make.stock(a, 50)
    make.stock(b, 50)
    o = make.order(lines=[(a, qty, 1), (b, qty, 1)])
    sh = api.post(f"/api/customer-orders/{o['id']}/shipments",
                  json={"lines": [{"line_id": l["id"], "quantity": qty} for l in o["lines"]]})
    api.post(f"/api/shipments/{sh['id']}/confirm-booking")
    return o, sh


def test_unbooking_after_packing_needs_packing_accepted_again(make, api):
    # unbooking dropped that line's boxes but packing stayed "accepted": it shipped with a line in no box
    o, sh = _booked(make, api)
    first, second = sh["lines"]
    api.post(f"/api/shipments/{sh['id']}/pick", json={"lines": [{"shipment_line_id": first["id"], "quantity": 10},
                                                                {"shipment_line_id": second["id"], "quantity": 9}]})
    api.post(f"/api/shipments/{sh['id']}/accept-packing")
    sh = api.post(f"/api/shipments/{sh['id']}/unbook", json={"shipment_line_id": second["id"], "quantity": 1})
    assert sh["packed_at"] is None
    api.post(f"/api/shipments/{sh['id']}/ship", expect=400)
    sh = api.post(f"/api/shipments/{sh['id']}/accept-packing")  # boxes come back for the line that lost them
    boxed = {}
    for b in sh["boxes"]:
        boxed[b["order_line_id"]] = boxed.get(b["order_line_id"], 0) + b["quantity_in_box"]
    assert boxed == {first["order_line_id"]: 10, second["order_line_id"]: 9}
    assert api.post(f"/api/shipments/{sh['id']}/ship")["status"] == "shipped"


def test_ship_refuses_boxes_that_dont_add_up(make, api):
    o, sh = _booked(make, api)
    api.post(f"/api/shipments/{sh['id']}/pick", json={"pick_all": True})
    api.post(f"/api/shipments/{sh['id']}/accept-packing")
    from app.config.database import SessionLocal
    from app.models import ShipmentBox
    db = SessionLocal()
    db.query(ShipmentBox).filter(ShipmentBox.shipment_id == sh["id"]).delete()  # a box went missing behind the screen's back
    db.commit()
    db.close()
    api.post(f"/api/shipments/{sh['id']}/ship", expect=400)


def test_delivered_date_with_a_time_zone(make, api):
    # "...Z" from an API client crashed comparing it with the naive ship date; moments now come back in UTC, marked Z
    a = make.item()
    make.stock(a, 10)
    o = make.order(lines=[(a, 5, 1)])
    sh = make.ship(o)
    when = (datetime.utcnow() + timedelta(hours=1)).replace(microsecond=0)
    sh = api.post(f"/api/shipments/{sh['id']}/delivered", json={"delivered_at": when.strftime("%Y-%m-%dT%H:%M:%SZ")})
    assert sh["status"] == "delivered" and sh["delivered_at"] == when.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_ship_date_is_a_utc_moment(make, api):
    a = make.item()
    make.stock(a, 10)
    sh = make.ship(make.order(lines=[(a, 5, 1)]))
    assert sh["ship_date"].endswith("Z")
    assert abs(datetime.fromisoformat(sh["ship_date"][:-1]) - datetime.utcnow()) < timedelta(minutes=5)


def test_line_quantities_must_be_positive(make, api):
    a = make.item()
    o = make.order(lines=[(a, 5, 1)])
    line = o["lines"][0]
    api.put(f"/api/customer-orders/{o['id']}/lines/{line['id']}", json={"quantity": 0}, expect=400)
    v = make.vendor()
    api.post("/api/purchase-orders/", json={"vendor_id": v["id"], "lines": [{"item_id": a["id"], "quantity": 0}]}, expect=400)
    api.post("/api/purchase-orders/", json={"vendor_id": v["id"], "lines": [{"item_id": a["id"], "quantity": -3}]}, expect=400)
    api.post("/api/purchase-orders/", json={"vendor_id": v["id"], "lines": [{"item_id": a["id"], "quantity": 3, "unit_cost": -1}]}, expect=400)
    po = make.po(vendor=v, lines=[(a, 3, 1)])
    api.post(f"/api/purchase-orders/{po['id']}/lines", json={"item_id": a["id"], "quantity": 0}, expect=400)
    api.put(f"/api/purchase-orders/{po['id']}/lines/{po['lines'][0]['id']}", json={"quantity": -1}, expect=400)


def test_blank_required_fields_are_ignored_not_500(make, api):
    a = make.item()
    o = make.order(lines=[(a, 5, 1)])
    c, v = make.customer(), make.vendor()
    assert api.put(f"/api/customer-orders/{o['id']}", json={"customer_id": None})["customer_id"] == o["customer_id"]
    api.put(f"/api/customer-orders/{o['id']}/lines/{o['lines'][0]['id']}", json={"quantity": None, "unit_price": None})
    po = make.po(vendor=v, lines=[(a, 3, 1)])
    assert api.put(f"/api/purchase-orders/{po['id']}", json={"vendor_id": None})["vendor_id"] == v["id"]
    assert api.put(f"/api/customers/{c['id']}", json={"is_active": None})["is_active"] is True
    assert api.put(f"/api/vendors/{v['id']}", json={"is_active": None})["is_active"] is True


def test_void_invoice_stays_void_and_records_who(make, api):
    a = make.item(price=2)
    make.stock(a, 10)
    sh = make.ship(make.order(lines=[(a, 5, 2)]))
    inv = make.invoice(sh)
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"})
    api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": 0.004}, expect=400)  # rounds to $0.00
    inv = api.put(f"/api/invoices/{inv['id']}/status", json={"status": "void", "reason": "test"})
    assert inv["voided_by"] == "admin"
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"}, expect=400)
    assert make.invoice(sh)["status"] == "draft"  # its shipment is billed on a new invoice instead


def test_on_hand_correction_cant_go_negative(make, api):
    a = make.item()
    make.stock(a, 5)
    api.put(f"/api/stock-items/{a['id']}", json={"on_hand": -2}, expect=400)
    assert api.put(f"/api/stock-items/{a['id']}", json={"on_hand": 0})["on_hand"] == 0


def test_invoice_funding_cant_be_negative(make, api):
    a = make.item(price=2)
    make.stock(a, 10)
    inv = make.invoice(make.ship(make.order(lines=[(a, 5, 2)])))
    api.put(f"/api/invoices/{inv['id']}/funding", json={"funding_amount": -1}, expect=400)
    assert api.put(f"/api/invoices/{inv['id']}/funding", json={"funding_amount": 8, "funding_discount": 0.4})["funding_amount"] == 8


def test_two_step_login(client):
    from app.services import totp
    from app.services.auth import AuthService
    client.post("/api/users/", json={"username": "tfa-user", "password": "Str0ng!Passw0rd#", "role": "employee"},
                headers={"Authorization": f"Bearer {AuthService.create_access_token({'sub': 'admin'})}"})
    h = {"Authorization": f"Bearer {AuthService.create_access_token({'sub': 'tfa-user'})}"}
    setup = client.post("/api/auth/2fa/setup", headers=h).json()
    assert "<svg" in setup["qr_svg"]
    assert client.post("/api/auth/2fa/enable", json={"code": "000000"}, headers=h).status_code == 400
    import time
    good = totp._code(setup["secret"], int(time.time() // 30))
    assert client.post("/api/auth/2fa/enable", json={"code": good}, headers=h).json()["totp_enabled"] is True
    login = lambda **kw: client.post("/api/auth/login", json={"username": "tfa-user", "password": "Str0ng!Passw0rd#", **kw})
    assert login().json()["detail"] == "TOTP_REQUIRED"
    assert login(code="123456").status_code == 401
    assert login(code=totp._code(setup["secret"], int(time.time() // 30))).status_code == 200


def test_login_rate_limit(client):
    from app.routes import auth
    auth._FAILS.clear()
    for _ in range(10):
        assert client.post("/api/auth/login", json={"username": "nobody-here", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "nobody-here", "password": "wrong"}).status_code == 429


def test_packing_list_as_excel_and_csv(make, api, client, admin_headers):
    import csv, io
    from openpyxl import load_workbook
    a, b = make.item(), make.item()
    make.stock(a, 50)
    make.stock(b, 50)
    o = make.order(lines=[(a, 20, 1), (b, 10, 1)], po_number="PO-XL-1")
    sh = make.ship(o)
    r = client.get(f"/api/shipments/{sh['id']}/packing-list.xlsx?pallets=true", headers=admin_headers)
    assert r.status_code == 200 and r.headers["content-disposition"].endswith(".xlsx")
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Packing List", "Boxes", "Pallets"]
    rows = [row for row in wb["Packing List"].iter_rows(values_only=True)]
    head = next(i for i, row in enumerate(rows) if row[0] == "Line")
    shipped = {row[1]: row[rows[head].index("Qty shipped")] for row in rows[head + 1:] if row[0]}
    assert shipped == {a["code"]: 20, b["code"]: 10}                      # real numbers, not "20" text
    r = client.get(f"/api/shipments/{sh['id']}/packing-list.csv", headers=admin_headers)
    lines = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert [(l["Part #"], l["Qty shipped"], l["Customer PO #"]) for l in lines] == [(a["code"], "20", "PO-XL-1"), (b["code"], "10", "PO-XL-1")]
    r = client.get(f"/api/shipments/packing-lists.xlsx?ids={sh['id']},{sh['id']}", headers=admin_headers)
    assert load_workbook(io.BytesIO(r.content)).sheetnames[:2] == ["Lines", "Shipments"]
    assert client.get(f"/api/shipments/{sh['id']}/packing-list.doc", headers=admin_headers).status_code == 404


def test_shipment_pallet_labels(make, api, client, admin_headers):
    import pypdfium2 as pdfium
    from app.services import doc_context
    from app.config.database import SessionLocal
    from app.models import Shipment
    bolt = make.item(code=make.item()["code"] + "B")
    nut = make.item(code=bolt["code"] + "-NUT", group="Nut")
    washer = make.item()
    for it in (bolt, nut, washer):
        make.stock(it, 100)
    o = make.order(lines=[(bolt, 40, 1), (nut, 40, 1), (washer, 20, 1)], po_number="4156932", job_number="M219-30C")
    sh = make.ship(o)
    bl, nl, wl = [l["id"] for l in o["lines"]]
    api.put(f"/api/shipments/{sh['id']}/boxes", json={"boxes": [
        {"order_line_id": bl, "item_id": bolt["id"], "box_number": 1, "quantity_in_box": 40, "pallet_number": "1"},
        {"order_line_id": nl, "item_id": nut["id"], "box_number": 1, "quantity_in_box": 40},                     # rides on its bolt's pallet
        {"order_line_id": wl, "item_id": washer["id"], "box_number": 1, "quantity_in_box": 20, "pallet_number": "2"}]})
    db = SessionLocal()
    ctxs = doc_context.pallet_label_contexts(db, db.get(Shipment, sh["id"]))
    db.close()
    assert [c["label"]["badge"] for c in ctxs] == ["1 of 2", "2 of 2"] and ctxs[0]["label"]["po"] == "4156932"
    assert ctxs[0]["pallet_rows"][0]["items"] == f"{bolt['code']}, {nut['code']}" and ctxs[0]["pallet_rows"][0]["_hi"]
    r = client.get(f"/api/shipments/{sh['id']}/pallet-labels.pdf", headers=admin_headers)
    assert r.status_code == 200 and len(pdfium.PdfDocument(r.content)) == 2               # one per pallet
    r = client.get(f"/api/shipments/{sh['id']}/pallet-labels.pdf?per_pallet=false", headers=admin_headers)
    assert len(pdfium.PdfDocument(r.content)) == 1


def test_pallet_table_carries_on_to_a_second_label():
    import pypdfium2 as pdfium
    from app.services import template_engine, template_starters
    rows = [{"pallet": str(i), "items": "15422, 15422-NUTS, 16642, 16713, 16718, 58268, 58268-NUTS, 77183-HPC, 77183-HPC-NUTS", "boxes": "9"} for i in range(1, 16)]
    pdf = template_engine.render_labels(template_starters.classic_pallet_label(), [{"label": {"po": "1"}, "pallet_rows": rows}])
    assert len(pdfium.PdfDocument(pdf)) >= 2


def test_pallet_label_print_options():
    from app.routes.shipments import _pallet_label_spec
    from app.services import template_engine, template_starters
    spec = _pallet_label_spec(template_starters.classic_pallet_label(), {"boxes_col": False, "company_line": False})
    shown = template_engine.visible_spec(spec)["header"]["blocks"]
    table = next(b for b in shown if b["type"] == "table")
    assert [c["key"] for c in table["columns"] if not c.get("hidden")] == ["pallet", "items"]
    assert not any(b.get("group") == "Company" for b in shown)
