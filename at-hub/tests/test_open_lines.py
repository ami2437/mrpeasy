"""Customer open-lines check: a report in Hudson's layout (made up here -- no real customer data in the repo) against
our orders: matches, shipped-but-still-open on their side, missing orders (created from the report), missing lines,
differences (take theirs), rules (their kit headers, our $0 nut lines), money permission."""
import io
from datetime import datetime

from openpyxl import Workbook

from tests.builders import uid

HEAD = ["Branch Plant", "Supplier Number", "Ship To Number", "Ship To Name", "Supplier", "Or Ty", "Order Number", "Line Number", "Ln Ty",
        "Transaction Originator", "Item Number", "Description ", "Description Line 2", "Order Quantity", "Quantity Open", "UM ", "Unit Cost",
        "Extended Cost", "Open Extended Cost", "Order Date", "Request Date", "Promised Delivery Date", "Last Stat", "Next Stat", "Reference 2", "Feedback"]


def _xlsx(lines) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(HEAD)
    for po, ln, ty, item, qty, open_, price, req, job in lines:
        ws.append(["  7621", 379150, 7621, "CUSTOMER", "AMERICAN TRADERS LLC", "OP", int(po), ln, ty, "X", item, "desc", "", qty, open_, "EA",
                   price, qty * price, open_ * price, datetime(2026, 5, 15), datetime.fromisoformat(req), datetime.fromisoformat(req), "230", "280", job, ""])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _upload(client, h, cust_id, data):
    r = client.post("/api/reconcile/reports", headers=h, data={"customer_id": str(cust_id)},
                    files={"file": ("open.xlsx", io.BytesIO(data), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200, r.text
    return r.json()


def test_open_lines_check(make, api, client, admin_headers):
    cust = make.customer()
    a, b, c, nut = make.item(), make.item(), make.item(), make.item(group="Nut", price=0)
    make.stock(a, 100)
    make.stock(b, 100)
    po1, po2, po_new = str(uid("")).lstrip("0")[:3] + "41001", str(uid("")).lstrip("0")[:3] + "41002", str(uid("")).lstrip("0")[:3] + "41003"
    o1 = make.order(customer=cust, po_number=po1, job_number="J-1", delivery_date="2026-09-25",
                    lines=[(a, 10, 2.0), (b, 5, 3.0), (nut, 10, 0)])
    make.ship(o1, {o1["lines"][0]["id"]: 10})  # line a shipped in full
    o2 = make.order(customer=cust, po_number=po2, lines=[(a, 4, 2.0)])
    report = _xlsx([
        (po1, 1, "B", a["code"], 10, 10, 2.0, "2026-09-25", "J-1"),   # we shipped it all, they still show it open
        (po1, 2, "B", b["code"], 5, 5, 3.5, "2026-09-25", "J-1"),     # price differs
        (po1, 3, "B", c["code"], 7, 7, 1.0, "2026-09-25", "J-1"),     # not on our order
        (po1, 4, "S", "KIT-1", 1, 1, 0.01, "2026-09-25", "J-1"),      # their kit header: left out
        (po2, 1, "B", a["code"], 4, 4, 2.0, "2026-09-25", "STOCK"),   # job differs only
        (po_new, 1, "B", a["code"], 6, 6, 2.0, "2026-10-16", "J-9"),  # whole PO missing
        (po_new, 2, "B", "THEIR-NEW-ITEM", 3, 3, 5.0, "2026-10-16", "J-9"),
    ])
    r = _upload(client, admin_headers, cust["id"], report)
    s = r["result"]["summary"]
    assert s["lines"] == 7 and s["ignored"] == 1 and s["missing_order"] == 2 and s["missing_line"] == 1 and s["missing_orders"] == 1
    rows = {(x["theirs"]["po"], x["theirs"]["line"]): x for x in r["result"]["rows"]}
    assert rows[(po1, "1")]["flags"] == ["shipped_open"] and rows[(po1, "1")]["ours"]["shipments"]
    assert rows[(po1, "2")]["flags"] == ["price"]
    assert rows[(po2, "1")]["flags"] == ["job"]
    assert r["result"]["ours_only"] == []  # our $0 nut line isn't on their PO -- left out by rule
    # the column layout is remembered for this customer
    assert client.get(f"/api/reconcile/reports?customer_id={cust['id']}", headers=admin_headers).json()[0]["summary"]["lines"] == 7
    # take their price; add their missing line
    rid = r["id"]
    r = client.post(f"/api/reconcile/reports/{rid}/apply", headers=admin_headers, json={"idx": rows[(po1, "2")]["idx"], "field": "price"}).json()
    assert api.get(f"/api/customer-orders/{o1['id']}")["lines"][1]["unit_price"] == 3.5
    r = client.post(f"/api/reconcile/reports/{rid}/apply", headers=admin_headers, json={"idx": rows[(po1, "3")]["idx"], "field": "add_line"}).json()
    assert r["result"]["summary"].get("missing_line", 0) == 0
    # the missing PO -> a draft order in Validation (the item we don't have waits for a pick), report attached
    r = client.post(f"/api/reconcile/reports/{rid}/create-orders", headers=admin_headers, json={"pos": [po_new]}).json()
    made = r["made"][0]
    o = api.get(f"/api/customer-orders/{made['order_id']}")
    assert o["status"] == "validation" and o["po_number"] == po_new and o["job_number"] == "J-9"
    assert len(o["lines"]) == 1 and len(o["ai_pending_lines"]) == 1
    assert r["result"]["summary"].get("missing_order", 0) == 0
    # our orders remember the check
    assert api.get(f"/api/customer-orders/{o2['id']}")["report_check"]
    # the export
    x = client.get(f"/api/reconcile/reports/{rid}/export", headers=admin_headers)
    assert x.status_code == 200 and x.content[:2] == b"PK"


def test_reconcile_is_a_money_permission():
    from app.services import permissions as P
    assert "reconcile" in P.MONEY and dict((k, d) for k, _m, _l, _money, d in P.CATALOG)["reconcile"] == "admin"


def test_new_item_fills_waiting_lines_on_every_validation_order(make, api, client, admin_headers):
    """Two orders made from a report both wait for an item we don't have: creating it once fills in both."""
    cust, a = make.customer(), make.item()
    new_code = f"NEWX{uid('')}"
    p1, p2 = f"77{uid('')}", f"78{uid('')}"
    report = _xlsx([(p1, 1, "B", a["code"], 5, 5, 1.0, "2026-10-16", "J"), (p1, 2, "B", new_code, 3, 3, 2.0, "2026-10-16", "J"),
                    (p2, 1, "B", new_code, 7, 7, 2.0, "2026-10-16", "J")])
    rid = _upload(client, admin_headers, cust["id"], report)["id"]
    made = client.post(f"/api/reconcile/reports/{rid}/create-orders", headers=admin_headers, json={"pos": [p1, p2]}).json()["made"]
    ids = [m["order_id"] for m in made]
    assert all(len(api.get(f"/api/customer-orders/{i}")["ai_pending_lines"]) == 1 for i in ids)
    api.post("/api/stock-items/", json={"code": new_code, "title": "new thing", "category": "Bolt", "selling_price": 2})
    for i in ids:  # filled on both, without touching either order
        o = api.get(f"/api/customer-orders/{i}")
        assert o["ai_pending_lines"] == [] and any(l["quantity"] in (3, 7) for l in o["lines"])
