"""Learned matches: a pick saved on an order/PO is remembered against what the document said."""
from app.config.database import SessionLocal
from app.models import StockItem
from app.services import item_alias
from app.services.item_match import ItemMatcher, pick


def matcher_for(party_type, party_id):
    db = SessionLocal()
    try:
        return ItemMatcher(db.query(StockItem).all(), learned=item_alias.for_party(db, party_type, party_id))
    finally:
        db.close()


def test_customer_pick_is_learned_and_repointed(make, api):
    a, b = make.item(title="BOLT HH 5/8-11 X 2 A325"), make.item(title="BOLT HH 5/8-11 X 2 A325 HDG")
    c = make.customer()
    desc = "Hex bolt 5/8 x 2 galv, their part XJ-77"
    api.post("/api/customer-orders/", json={"customer_id": c["id"], "lines": [
        {"item_id": b["id"], "quantity": 5, "unit_price": 1, "source_code": "XJ-77", "source_description": desc}]})
    top = matcher_for("customer", c["id"]).rank(["XJ-77"], desc)[0]
    assert top["item_id"] == b["id"] and top["why"].startswith("learned") and top["score"] == 0.99  # one pick: just under certain, above an exact title
    assert pick(matcher_for("customer", c["id"]).rank([None], desc)) == b["id"]
    # another customer's wording is not shared
    assert not any(x["why"].startswith("learned") for x in matcher_for("customer", make.customer()["id"]).rank(["XJ-77"], desc))
    # picked differently next time: the latest pick wins
    api.post("/api/customer-orders/", json={"customer_id": c["id"], "lines": [
        {"item_id": a["id"], "quantity": 5, "unit_price": 1, "source_code": "XJ-77", "source_description": desc}]})
    assert matcher_for("customer", c["id"]).rank(["XJ-77"], None)[0]["item_id"] == a["id"]


def test_vendor_description_only_is_learned(make, api):
    """Vendors that print no part # (descriptions only) are learned from the PO line's vendor description."""
    a = make.item()
    v = make.vendor()
    desc = "SS 316 HEX BOLT 3/4 X 10 FULL THREAD"
    for _ in range(2):
        api.post("/api/purchase-orders/", json={"vendor_id": v["id"], "lines": [
            {"item_id": a["id"], "quantity": 3, "unit_cost": 1, "vendor_description": desc}]})
    top = matcher_for("vendor", v["id"]).rank([None], desc)[0]
    assert top["item_id"] == a["id"] and top["score"] == 1.0 and "(2x)" in top["why"]
