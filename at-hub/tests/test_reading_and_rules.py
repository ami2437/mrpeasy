"""PDF reading, item naming, redaction, product groups, recycle bin, item delete rules."""
import json

import pytest

from app.services import customer_po_templates, item_naming

# A made-up Chart / Hudson PO in the exact text layout pypdf gives for the real ones (no real names).
CHART_PO = """[Page 1]
Supplier:  379150
OUR COMPANY LLC
HUDSON PRODUCTS CORPORATION
PURCHASE ORDER
SHIP TO:
HUDSON PRODUCTS CORPORATION
1 TEST RD
TULSA OK 74115
United States
1 of 1
Sep 18, 2026
4999001
0
7631
Page:
Date:
Order Number:
   Item Number
1.000 31181
BOLT_HH_3/8-16x1_A307A_XYLAN_
w/A563 GR A HEX NUT
250237
Oct 8, 2026 EA 3.8500 192.5050.00
N0
2.000 41572
BOLT_HH_7/8-9x2_A325_TYPE1_HDG
w/A194-2H HEX NUT_
250237
Oct 8, 2026 EA 3.0000 915.00305.00
N0
NUT SHALL BE WAXED DIP
3.000 16713
WASHER_FLAT_5/8"_F436_HDG_HRDN 250237
Oct 8, 2026 EA .1900 19.00100.00
N0
4.000 M246-30A
FIELD ERECTION BOLT KIT
Oct 8, 2026 EA .0100 .011.00
N0
Total Order (USD)
1,126.51
0.00
1,126.51
"""


def test_chart_reader_splits_glued_columns_notes_and_jobs():
    r = customer_po_templates.parse(CHART_PO)
    assert r["template"] == "Chart / Hudson Products" and r["po_number"] == "4999001"
    lines = {l["item_code"]: l for l in r["lines"]}
    assert set(lines) == {"31181", "41572", "16713"}                      # kit header skipped
    assert (lines["31181"]["quantity"], lines["31181"]["unit_price"], lines["31181"]["extended"]) == (50, 3.85, 192.50)
    assert lines["41572"]["line_note"] == "NUT SHALL BE WAXED DIP"
    assert lines["16713"]["description"] == 'WASHER_FLAT_5/8"_F436_HDG_HRDN'  # job # not glued to the title
    assert lines["16713"]["job_number"] == "250237"
    assert r["problems"] == []                                           # every line: unit x qty = extended


@pytest.mark.parametrize("desc,title,conf", [
    ("BOLT_HH_3/4-10x2-1/2_A325_TYPE1_HDG_w/A194-2H HEX NUT", "3/4-10 A194-2H HEX NUT", "high"),
    ("BOLT_HH_3/8-16x1_A307A_XYLAN_ w/A563 GR A HEX NUT", "3/8-16 A563 GR A HEX NUT", "high"),
    ("BOLT_HH_7/8-9x2_A325_TYPE1_HDG w/A194-2H HEX NUT_ NUT SHALL BE WAXED DIP", "7/8-9 A194-2H HEX NUT WAX DIPPED", "high"),
    ("STUD_1-8x14-1/2_SA193_B7_ HDG_w/(2) 2H HVY HEX NUTS", "1-8 A194-2H HVY HEX NUT", "high"),
    ("BOLT_EYE_5/16x1-1/8_A489_HDG_ SHOULDER PATTERN_W/NUT", "5/16-18 A563 GR A HEX NUT", "check"),
    ("BOLT_HH_1/2-13x3-1/4_A325_ TYPE1_MECH GALV", "1/2-13 A194-2H HEX NUT", "high"),  # no "w/": usual nut for A325
    ("BOLT_HH_5/8-11UNCx2-1/4 FULL_ THREAD_A325_HDG_w/A194-2H NUTS", "5/8-11 A194-2H HEX NUT", "high"),
])
def test_nut_titles_come_from_the_bolt_description(desc, title, conf):
    r = item_naming.nut_title(desc)
    assert (r["title"], r["confidence"]) == (title, conf)


def test_assembled_nut_gets_no_separate_item():
    assert item_naming.nut_title("BOLT_HH_3/4-10x10_J429GR5_W/ASSEMBLED HVY HEX NUT")["title"] is None


def test_cap_screws_count_as_bolts():
    assert item_naming.item_category("SCREW_CAP_SKHDFL_1/2-13x1-1/2_ F835") == "Bolt"
    assert item_naming.usually_with_nut("BOLT_HH_3/4-10x9-1/4_A325__ TYPE1_HDG")


# ---------------- redaction: nothing identifying leaves for Claude ----------------
def test_redaction_removes_people_phones_cards_and_account_numbers(client):
    from app.config.database import SessionLocal
    from app.services import ai_cloud
    text = """INVOICE 6665732
Customer ID: 333745
Attn: JANE DOE  Ordered By: JANE .
Phone 703-555-0142
Shipment Accepted By:
Reference Number:
226
00309Q
****4571
743525109
SUB-TOTAL:Total Lines: 1 1,657.50
57402-HPC WASHER 120 0.45"""
    safe, removed = ai_cloud.redact(text, SessionLocal())
    for leak in ("333745", "JANE", "703-555-0142", "4571", "743525109", "00309Q"):
        assert leak not in safe, leak
    for keep in ("6665732", "1,657.50", "57402-HPC"):
        assert keep in safe, keep


# ---------------- product groups ----------------
def test_groups_are_a_fixed_list(api):
    groups = {g["name"] for g in api.get("/api/stock-items/groups/list")}
    assert "Bolt" in groups and "BOLT" not in groups
    r = api.post("/api/stock-items/groups/list", json={"name": "NUTS"}, expect=400)
    assert "Nut" in r["detail"]
    it = api.post("/api/stock-items/", json={"code": "GRP-T1", "title": "t", "category": "BOLT"})
    assert it["category"] == "Bolt"                                       # spelled as the list spells it
    api.post("/api/stock-items/", json={"code": "GRP-T2", "title": "t", "category": "Fastener"}, expect=400)


# ---------------- items: delete only if unused, otherwise archive ----------------
def test_used_items_are_archived_not_deleted(make, api):
    used, spare = make.item(), make.item()
    make.order(lines=[(used, 1, 1)])
    r = api.delete(f"/api/stock-items/{used['id']}", expect=409)
    assert r["detail"].startswith("USED|") and "customer orders" in r["detail"]
    assert api.put(f"/api/stock-items/{used['id']}", json={"is_active": False})["is_active"] is False
    api.delete(f"/api/stock-items/{spare['id']}", expect=204)


def test_ai_items_need_verifying(api):
    it = api.post("/api/stock-items/", json={"code": "AI-T1", "title": "t", "category": "Nut", "created_via": "ai-scan"})
    assert it["verified_by"] is None
    assert api.post(f"/api/stock-items/{it['id']}/verify")["verified_by"].startswith("admin")


# ---------------- recycle bin ----------------
def test_recycle_bin_restores_an_order_with_its_lines(make, api):
    a = make.item()
    o = make.order(lines=[(a, 2, 1), (a, 3, 1)])
    api.post(f"/api/customer-orders/{o['id']}/cancel")
    api.delete(f"/api/customer-orders/{o['id']}")
    entry = next(e for e in api.get("/api/recycle-bin") if e["label"] == o["code"])
    assert entry["contents"] == {"customer_orders": 1, "customer_order_lines": 2}
    r = api.post(f"/api/recycle-bin/{entry['id']}/restore")
    back = [x for x in api.get("/api/customer-orders/") if x["po_number"] == o["po_number"]][0]
    assert len(back["lines"]) == 2 and back["code"] in (o["code"], o["code"] + "-R")
    api.post(f"/api/recycle-bin/{entry['id']}/restore", expect=400)       # only once


def test_recycle_bin_keeps_deleted_files(make, api, client, admin_headers):
    o = make.order(lines=[(make.item(), 1, 1)])
    up = client.post("/api/attachments/", headers=admin_headers, data={"entity_type": "customer_order", "entity_id": str(o["id"]), "category": "other"},
                     files={"files": ("note.txt", b"keep me", "text/plain")}).json()[0]
    api.delete(f"/api/attachments/{up['id']}")
    entry = next(e for e in api.get("/api/recycle-bin") if e["label"] == "note.txt")
    api.post(f"/api/recycle-bin/{entry['id']}/restore")
    assert client.get(f"/api/attachments/{up['id']}/file", headers=admin_headers).content == b"keep me"


def test_a_deleted_orders_number_is_never_reused(make, api):
    a = make.item()
    o = make.order(lines=[(a, 1, 1)])
    api.post(f"/api/customer-orders/{o['id']}/cancel")
    api.delete(f"/api/customer-orders/{o['id']}")
    o2 = make.order(lines=[(a, 1, 1)])
    assert o2["code"] != o["code"]


def test_employees_never_see_prices_in_history(make, api, client):
    from app.config.database import SessionLocal
    from app.models import User
    from app.services.auth import AuthService
    db = SessionLocal()
    if not db.query(User).filter(User.username == "emp1").first():
        db.add(User(username="emp1", hashed_password=AuthService.hash_password("x"), role="employee", is_active=True))
        db.commit()
    a = make.item()
    o = make.order(lines=[(a, 2, 9.99)])
    api.put(f"/api/customer-orders/{o['id']}/lines/{o['lines'][0]['id']}", json={"unit_price": 12.34})
    emp = {"Authorization": f"Bearer {AuthService.create_access_token({'sub': 'emp1'})}"}
    hist = client.get(f"/api/activity/customer_order/{o['id']}", headers=emp).json()
    assert hist and all("12.34" not in (h["detail"] or "") for h in hist)
