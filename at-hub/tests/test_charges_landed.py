"""PO charges (freight, customs...) carried into the goods' cost as landed costs (user, 2026-10-17).

The user's scenario: PO 100 has item A 5,000 x $0.15 and $100 freight; PO 200 (same container) has item B 6,000 x $0.10;
customs + dispatch of $2,000 is applied to both. A should cost 0.15 + 0.02 + its share, B 0.10 + its share."""
from app.config.database import SessionLocal
from app.models import LandedCost, Lot, PurchaseOrderCharge


def _ordered(api, make, lines, vendor=None):
    po = make.po(vendor=vendor, lines=lines)
    api.post(f"/api/purchase-orders/{po['id']}/mark-ordered")
    return api.get(f"/api/purchase-orders/{po['id']}")


def _receive_all(api, po):
    api.post(f"/api/purchase-orders/{po['id']}/receive", json={"lines": [{"line_id": l["id"], "quantity": l["quantity"]} for l in po["lines"]]})


def _lot_cost(item_id):
    db = SessionLocal()
    try:
        return round(db.query(Lot).filter(Lot.item_id == item_id).one().unit_cost, 4)
    finally:
        db.close()


def test_the_container_scenario(api, make):
    a, b = make.item(cost=0.15), make.item(cost=0.10)
    po100 = _ordered(api, make, [(a, 5000, 0.15)])
    po200 = _ordered(api, make, [(b, 6000, 0.10)])
    # $100 freight on PO 100 goes onto PO 100's own goods by itself: +$0.02 on A
    po100 = api.post(f"/api/purchase-orders/{po100['id']}/charges", json={"charge_type": "freight", "amount": 100})
    fr = po100["charges"][0]
    assert fr["landed_cost_code"] and fr["applied_po_ids"] == [po100["id"]] and fr["lc_split"] == "quantity"
    assert abs(po100["lines"][0]["landed_cost_per_unit"] - 0.02) < 1e-9
    # the broker's $2,000 customs + dispatch, billed on its own charges-only PO, applied to both POs by value
    broker = make.vendor()
    cpo = api.post("/api/purchase-orders/", json={"vendor_id": broker["id"], "charges_only": True, "lines": []})
    cpo = api.post(f"/api/purchase-orders/{cpo['id']}/charges", json={"charge_type": "customs", "amount": 2000, "description": "Customs + dispatch"})
    ch = cpo["charges"][0]
    assert ch["landed_cost_id"] is None                                  # no goods of its own: waits to be applied
    un = api.get("/api/landed-costs/unapplied")
    assert any(c["charge_id"] == ch["id"] and not c["has_lines"] for c in un["charges"])
    preview = api.post("/api/landed-costs/preview", json={"amount": 2000, "po_ids": [po100["id"], po200["id"]], "split": "quantity"})
    assert all(abs(r["per_unit"] - 2000 / 11000) < 1e-9 for r in preview)
    cpo = api.post(f"/api/purchase-orders/{cpo['id']}/charges/{ch['id']}/apply", json={"po_ids": [po100["id"], po200["id"]], "split": "value"})
    ch = cpo["charges"][0]
    assert sorted(ch["applied_po_ids"]) == sorted([po100["id"], po200["id"]]) and ch["lc_split"] == "value"
    lc = api.get(f"/api/landed-costs/{ch['landed_cost_id']}")
    assert lc["source_charge_id"] == ch["id"] and lc["source_po_id"] == cpo["id"]
    shares = {x["po_id"]: round(x["amount"], 2) for x in lc["allocations"]}
    assert shares == {po100["id"]: 1111.11, po200["id"]: 888.89}
    _receive_all(api, api.get(f"/api/purchase-orders/{po100['id']}"))
    _receive_all(api, api.get(f"/api/purchase-orders/{po200['id']}"))
    assert _lot_cost(a["id"]) == round(0.15 + 0.02 + 1111.11 / 5000, 4)    # 0.3922
    assert _lot_cost(b["id"]) == round(0.10 + 888.89 / 6000, 4)            # 0.2481
    line = api.get(f"/api/purchase-orders/{po100['id']}")["lines"][0]
    assert {p["cost_type"] for p in line["landed_parts"]} == {"freight", "customs"}
    assert ch["id"] not in [c["charge_id"] for c in api.get("/api/landed-costs/unapplied")["charges"]]
    # the amount follows the charge: the landed cost's amount can't be changed on its own
    body = {"description": lc["description"], "cost_type": lc["cost_type"], "amount": 1500, "po_ids": [po100["id"]]}
    api.put(f"/api/landed-costs/{lc['id']}", json=body, expect=400)


def test_own_charges_follow_line_changes_and_lines_can_still_go(api, make):
    a, b = make.item(), make.item()
    po = _ordered(api, make, [(a, 100, 1.0), (b, 100, 1.0)])
    po = api.post(f"/api/purchase-orders/{po['id']}/charges", json={"charge_type": "freight", "amount": 50})
    assert [round(l["landed_cost_per_unit"], 4) for l in po["lines"]] == [0.25, 0.25]
    po = api.delete(f"/api/purchase-orders/{po['id']}/lines/{po['lines'][1]['id']}")   # carries only this PO's own freight
    assert [round(l["landed_cost_per_unit"], 4) for l in po["lines"]] == [0.5]
    # leave it as a PO cost: off the goods, not listed as unapplied
    c = po["charges"][0]
    po = api.post(f"/api/purchase-orders/{po['id']}/charges/{c['id']}/skip")
    assert po["charges"][0]["lc_status"] == "skip" and po["lines"][0]["landed_cost_per_unit"] == 0
    assert c["id"] not in [x["charge_id"] for x in api.get("/api/landed-costs/unapplied")["charges"]]
    # removing the charge takes its landed cost with it
    po = api.post(f"/api/purchase-orders/{po['id']}/charges", json={"charge_type": "customs", "amount": 10})
    lc_id = po["charges"][-1]["landed_cost_id"]
    assert lc_id and po["charges"][-1]["lc_split"] == "value"
    api.delete(f"/api/purchase-orders/{po['id']}/charges/{po['charges'][-1]['id']}")
    db = SessionLocal()
    assert db.get(LandedCost, lc_id) is None
    db.close()


def test_last_line_rules_and_charges_only_pos(api, make):
    v = make.vendor()
    item = make.item()
    po = api.post("/api/purchase-orders/", json={"vendor_id": v["id"], "lines": [{"item_id": item["id"], "quantity": 1, "unit_cost": 610}]})
    po = api.delete(f"/api/purchase-orders/{po['id']}/lines/{po['lines'][0]['id']}")   # a draft may lose its last line
    assert po["lines"] == []
    api.post(f"/api/purchase-orders/{po['id']}/mark-ordered", expect=400)               # but can't be ordered empty
    api.post(f"/api/purchase-orders/{po['id']}/charges", json={"charge_type": "customs", "amount": 480})
    assert api.post(f"/api/purchase-orders/{po['id']}/mark-ordered")["status"] == "ordered"   # a charges-only PO is a real PO
    api.post("/api/purchase-orders/", json={"vendor_id": v["id"], "lines": []}, expect=400)
    ordered = _ordered(api, make, [(item, 1, 5)])
    api.delete(f"/api/purchase-orders/{ordered['id']}/lines/{ordered['lines'][0]['id']}", expect=400)  # ordered: keeps one line


def test_unapplied_landed_cost_and_charge_raise_tasks(api, make):
    # a cancelled PO can't keep a landed cost (cancel refuses), so an orphan only comes from old / odd data -- still caught
    a = make.item()
    po = _ordered(api, make, [(a, 10, 1)])
    api.post(f"/api/purchase-orders/{po['id']}/cancel", json={})
    db = SessionLocal()
    db.add(LandedCost(code="LC-ORPHAN1", description="Dray", cost_type="freight", amount=30))
    db.commit()
    db.close()
    tasks = {t["key"]: t for t in api.get("/api/tasks/")}
    assert "lc-unapplied:LC-ORPHAN1" in tasks
    v = make.vendor()
    cpo = api.post("/api/purchase-orders/", json={"vendor_id": v["id"], "charges_only": True, "lines": []})
    cpo = api.post(f"/api/purchase-orders/{cpo['id']}/charges", json={"charge_type": "freight", "amount": 75})
    tasks = {t["key"]: t for t in api.get("/api/tasks/")}
    assert f"charge-unapplied:{cpo['code']}:{cpo['charges'][0]['id']}" in tasks


def test_vendor_account_charges_land_on_the_po_once(api, make):
    """An account invoice's freight becomes a charge on its PO (and onto its goods); a charge the PO already has counts."""
    v = make.vendor()
    item = make.item()
    po = make.po(vendor=v, lines=[(item, 100, 2)], vendor_so_number="FR/1/26-27")
    acc = api.post("/api/vendor-accounts/", json={"vendor_id": v["id"], "cash_share": 0})["account"]["id"]
    api.post(f"/api/vendor-accounts/{acc}/bills", json={"so_number": "FR/1/26-27", "bill_number": "FR/9", "goods": 200, "freight": 40, "cash_due": 0})
    got = api.get(f"/api/purchase-orders/{po['id']}")
    mine = [c for c in got["charges"] if c["created_by"] == "vendor-account"]
    assert [(c["charge_type"], c["amount"]) for c in mine] == [("freight", 40)] and mine[0]["landed_cost_id"]
    assert round(got["lines"][0]["landed_cost_per_unit"], 4) == 0.4
    # a second PO that already carries its freight (typed in / imported): nothing added
    po2 = make.po(vendor=v, lines=[(item, 10, 2)], vendor_so_number="FR/2/26-27")
    api.post(f"/api/purchase-orders/{po2['id']}/charges", json={"charge_type": "shipping", "amount": 15})
    api.post(f"/api/vendor-accounts/{acc}/bills", json={"so_number": "FR/2/26-27", "bill_number": "FR/10", "goods": 20, "freight": 15, "cash_due": 0})
    got2 = api.get(f"/api/purchase-orders/{po2['id']}")
    assert round(sum(c["amount"] for c in got2["charges"]), 2) == 15 and not [c for c in got2["charges"] if c["created_by"] == "vendor-account"]
    # opening the account again changes nothing (no churn)
    api.get(f"/api/vendor-accounts/{acc}")
    db = SessionLocal()
    assert db.query(PurchaseOrderCharge).filter(PurchaseOrderCharge.po_id == po["id"], PurchaseOrderCharge.created_by == "vendor-account").count() == 1
    db.close()
