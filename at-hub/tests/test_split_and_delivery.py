"""Split Lines (part of an invoice onto a new one, same shipment), per-line delivery dates, and the order check
(shipments and invoices may never add up to more than the customer order)."""
from datetime import datetime, timedelta

from app.services.money import cents


def _two_line_sale(make, shipping=0):
    a, b = make.item(price=2), make.item(price=5)
    return make.sold([(a, 10, 2), (b, 4, 5)], shipping=shipping)


# ---------------- Split Lines ----------------
def test_split_whole_line_onto_new_invoice_same_shipment(make, api):
    o, sh, inv = _two_line_sale(make, shipping=12.5)
    before = inv["total"]
    b_line = next(l for l in inv["lines"] if l["unit_price"] == 5)
    new = api.post(f"/api/invoices/{inv['id']}/split-lines", json={"lines": [{"line_id": b_line["id"]}]})
    old = api.get(f"/api/invoices/{inv['id']}")
    assert new["status"] == "draft" and new["order_id"] == o["id"] and new["shipment_ids"] == [sh["id"]]
    assert old["shipment_ids"] == [sh["id"]]                       # both still bill the shipment
    assert new["split_from_code"] == inv["code"] and old["split_into"] == [new["code"]]
    assert cents(old["total"] + new["total"]) == before            # nothing lost or doubled
    assert api.get(f"/api/invoices/{inv['id']}/billing-check")["differences"] == []
    assert api.get(f"/api/invoices/{new['id']}/billing-check")["differences"] == []
    assert api.get(f"/api/shipments/{sh['id']}")["status"] == "invoiced"


def test_split_part_of_a_line_and_billed_still_matches_delivered(make, api):
    o, sh, inv = _two_line_sale(make)
    a_line = next(l for l in inv["lines"] if l["unit_price"] == 2)
    new = api.post(f"/api/invoices/{inv['id']}/split-lines", json={"lines": [{"line_id": a_line["id"], "quantity": 3}]})
    old = api.get(f"/api/invoices/{inv['id']}")
    assert next(l for l in old["lines"] if l["unit_price"] == 2)["quantity"] == 7
    assert new["lines"][0]["quantity"] == 3 and new["lines"][0]["order_line_id"] == a_line["order_line_id"]
    check = api.get(f"/api/invoices/{inv['id']}/billing-check")
    assert check["differences"] == [] and check["elsewhere"][str(a_line["order_line_id"])] == {new["code"]: 3}
    # editing one half up to the full delivered qty is now over-billing across the two
    lines = [{**l, "quantity": 10 if l["id"] == next(x for x in old["lines"] if x["unit_price"] == 2)["id"] else l["quantity"]} for l in old["lines"]]
    r = api.put(f"/api/invoices/{inv['id']}", json={"lines": lines}, expect=400)
    assert "on " + new["code"] in r["detail"]
    # the order ledger counts both invoices once
    ledger = api.get(f"/api/customer-orders/{o['id']}/billing")["lines"]
    assert all(l["billed"] == l["shipped"] == l["ordered"] and not l["problems"] for l in ledger)


def test_split_refuses_sent_all_lines_and_too_much(make, api):
    o, sh, inv = _two_line_sale(make)
    ids = [l["id"] for l in inv["lines"]]
    api.post(f"/api/invoices/{inv['id']}/split-lines", json={"lines": [{"line_id": i} for i in ids]}, expect=400)
    api.post(f"/api/invoices/{inv['id']}/split-lines", json={"lines": [{"line_id": ids[0], "quantity": 99}]}, expect=400)
    api.post(f"/api/invoices/{inv['id']}/split-lines", json={"lines": []}, expect=400)
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"})
    api.post(f"/api/invoices/{inv['id']}/split-lines", json={"lines": [{"line_id": ids[0]}]}, expect=400)


def test_voiding_one_half_keeps_shipment_invoiced_and_combine_back(make, api):
    o, sh, inv = _two_line_sale(make)
    new = api.post(f"/api/invoices/{inv['id']}/split-lines", json={"lines": [{"line_id": inv["lines"][1]["id"]}]})
    # combine back: one invoice, one shipment link, original total
    merged = api.post(f"/api/invoices/{inv['id']}/merge", json={"invoice_ids": [new["id"]]})
    assert merged["shipment_ids"] == [sh["id"]] and merged["total"] == inv["total"] and not merged["split_into"]
    # split again, void the new half: the shipment is still billed by the original
    new = api.post(f"/api/invoices/{inv['id']}/split-lines", json={"lines": [{"line_id": merged["lines"][1]["id"]}]})
    api.put(f"/api/invoices/{new['id']}/status", json={"status": "void"})
    assert api.get(f"/api/shipments/{sh['id']}")["status"] == "invoiced"
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "void"})
    assert api.get(f"/api/shipments/{sh['id']}")["status"] in ("shipped", "delivered")


def test_undo_ship_refused_while_split_over_two_invoices(make, api):
    o, sh, inv = _two_line_sale(make)
    api.post(f"/api/invoices/{inv['id']}/split-lines", json={"lines": [{"line_id": inv["lines"][1]["id"]}]})
    r = api.get(f"/api/shipments/{sh['id']}/undo-plan", expect=400)
    assert "split" in r["detail"]


def test_split_off_a_combined_invoice_moves_only_that_shipment(make, api):
    a = make.item(price=1)
    make.stock(a, 30)
    o = make.order(lines=[(a, 30, 1)])
    lid = o["lines"][0]["id"]
    s1 = make.ship(o, {lid: 10})
    s2 = make.ship(api.get(f"/api/customer-orders/{o['id']}"), {lid: 20})
    inv = make.invoice([s1, s2])
    s2_line = next(l for l in inv["lines"] if l["shipment_id"] == s2["id"])
    new = api.post(f"/api/invoices/{inv['id']}/split-lines", json={"lines": [{"line_id": s2_line["id"]}]})
    assert new["shipment_ids"] == [s2["id"]]
    assert api.get(f"/api/invoices/{inv['id']}")["shipment_ids"] == [s1["id"]]
    assert api.get(f"/api/invoices/{inv['id']}/billing-check")["differences"] == []


# ---------------- delivery dates ----------------
def _day(days_ago):
    from app.services import clock  # the company's calendar day, as the server checks it (not UTC: after 7 pm Central that's tomorrow)
    return (clock.today() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def test_delivery_date_and_a_line_on_another_day(make, api):
    o, sh, inv = _two_line_sale(make)
    lid_a, lid_b = (l["order_line_id"] for l in sorted(inv["lines"], key=lambda l: l["unit_price"]))
    today = _day(0)
    got = api.put(f"/api/shipments/{sh['id']}/delivery-dates", json={"delivered_at": f"{today}T12:00:00",
                  "lines": [{"order_line_id": lid_a, "delivered_at": f"{today}T12:00:00"}, {"order_line_id": lid_b, "delivered_at": None}]})
    assert got["delivered_at"] and all(l["delivered_at"] is None for l in got["lines"])  # same day = came with it
    assert all(l["effective_delivered_at"] == got["delivered_at"] for l in got["lines"])
    # a line in the future, or before it shipped, is refused
    api.put(f"/api/shipments/{sh['id']}/delivery-dates", json={"delivered_at": f"{today}T12:00:00",
            "lines": [{"order_line_id": lid_b, "delivered_at": f"{_day(-3)}T12:00:00"}]}, expect=400)
    api.put(f"/api/shipments/{sh['id']}/delivery-dates", json={"delivered_at": f"{today}T12:00:00",
            "lines": [{"order_line_id": lid_b, "delivered_at": f"{_day(30)}T12:00:00"}]}, expect=400)
    api.put(f"/api/shipments/{sh['id']}/delivery-dates", json={"delivered_at": f"{_day(-2)}T12:00:00", "lines": []}, expect=400)
    api.put(f"/api/shipments/{sh['id']}/delivery-dates", json={"delivered_at": f"{_day(30)}T12:00:00", "lines": []}, expect=400)
    # clearing the delivery clears the line dates too
    api.post(f"/api/shipments/{sh['id']}/undeliver")
    assert all(l["delivered_at"] is None for l in api.get(f"/api/shipments/{sh['id']}")["lines"])


def test_line_date_differs_and_shows_on_combined_invoice_pdf(make, api, client, admin_headers):
    import io as _io
    import re
    from pypdf import PdfReader
    from app.config.database import SessionLocal
    from app.models import Shipment
    a, b = make.item(price=2), make.item(price=5)
    make.stock(a, 20)
    make.stock(b, 4)
    o = make.order(lines=[(a, 20, 2), (b, 4, 5)])
    la, lb = o["lines"][0]["id"], o["lines"][1]["id"]
    s1 = make.ship(o, {la: 10, lb: 4})
    s2 = make.ship(api.get(f"/api/customer-orders/{o['id']}"), {la: 10})
    db = SessionLocal()  # backdate the ship so a line can arrive a day after the shipment
    for sid in (s1["id"], s2["id"]):
        db.get(Shipment, sid).ship_date = datetime.utcnow() - timedelta(days=5)
    db.commit()
    db.close()
    got = api.put(f"/api/shipments/{s1['id']}/delivery-dates", json={"delivered_at": f"{_day(3)}T12:00:00",
                  "lines": [{"order_line_id": lb, "delivered_at": f"{_day(1)}T12:00:00"}]})
    own = [l for l in got["lines"] if l["delivered_at"]]
    assert [l["order_line_id"] for l in own] == [lb]
    assert own[0]["effective_delivered_at"] != got["delivered_at"]
    # changing the shipment's date leaves the line's own date alone
    got = api.put(f"/api/shipments/{s1['id']}/delivery-dates", json={"delivered_at": f"{_day(2)}T12:00:00",
                  "lines": [{"order_line_id": lb, "delivered_at": f"{_day(1)}T12:00:00"}]})
    assert [l["order_line_id"] for l in got["lines"] if l["delivered_at"]] == [lb]
    inv = make.invoice([s1, s2])
    text = "".join(pg.extract_text() for pg in PdfReader(_io.BytesIO(client.get(f"/api/invoices/{inv['id']}/pdf", headers=admin_headers).content)).pages)
    shown = {d for d in re.findall(r"[A-Z][a-z]{2} \d{2}, \d{4}", text)}
    assert len(shown) >= 2  # the shipment's date and the late line's own date both print


# ---------------- order check ----------------
def test_order_check_flags_billing_past_what_was_ordered(make, api):
    a = make.item(price=3)
    o, sh, inv = make.sold([(a, 10, 3)])
    lines = [{**l, "quantity": 12} for l in inv["lines"]]
    api.put(f"/api/invoices/{inv['id']}", json={"lines": lines, "accept_qty_differences": True, "qty_note": "test"})
    row = api.get(f"/api/customer-orders/{o['id']}/billing")["lines"][0]
    assert {"over_ordered_billed", "billed_vs_shipped"} <= set(row["problems"])
    tasks = api.get("/api/tasks/")
    keys = [t.get("key") for t in (tasks if isinstance(tasks, list) else tasks.get("tasks", []))]
    assert f"order-check:{o['code']}" in keys


def test_order_check_clean_order_has_no_problems(make, api):
    a = make.item(price=3)
    o, sh, inv = make.sold([(a, 10, 3)])
    row = api.get(f"/api/customer-orders/{o['id']}/billing")["lines"][0]
    assert row["problems"] == [] and row["shipped_on_shipments"] == 10
