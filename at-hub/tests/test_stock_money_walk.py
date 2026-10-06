"""Random walks through purchasing, stock and billing -- POs (order, part-receive, edit, charges, cancel, delete), stock
corrections, lots on hold, generic-nut transfers, shipping, invoices (pay, send, void, merge, split, delete) -- with the
ledger checked after EVERY step. Seeded, so a failure replays exactly; never a 5xx.

Invariants (on what the walk made):
  - item: on_hand == the sum of its lots, booked == what open shipments hold, no lot below 0
  - PO line: 0 <= received; PO status follows its lines (received only when every line is in)
  - invoice: total == its lines, paid == its payments, balance == total - paid; a shipment is on at most one live invoice,
    and it's "invoiced" exactly when it is
"""
import random

import pytest

from tests.builders import uid

SHIPPED = ("shipped", "delivered", "invoiced")


def _check(item_ids, po_ids, order_ids):
    from app.config.database import SessionLocal
    from app.models import Invoice, Lot, PurchaseOrder, Shipment, ShipmentLine, StockItem
    from app.services.money import line_amount
    bad = []
    with SessionLocal() as db:
        for it in db.query(StockItem).filter(StockItem.id.in_(item_ids)).all():
            lots = db.query(Lot).filter(Lot.item_id == it.id).all()
            if abs(it.on_hand - sum(l.quantity for l in lots)) > 1e-6:
                bad.append(f"{it.code}: on hand {it.on_hand} != lots {sum(l.quantity for l in lots)} "
                           f"({', '.join(f'{l.lot_code}={l.quantity:g}/{l.status}' for l in lots)})")
            if any(l.quantity < -1e-9 for l in lots):
                bad.append(f"{it.code}: a lot below 0")
            held = sum(sl.quantity for sl in db.query(ShipmentLine).join(Shipment, Shipment.id == ShipmentLine.shipment_id)
                       .filter(ShipmentLine.item_id == it.id, Shipment.status.in_(("new", "ready"))).all())
            if abs((it.booked or 0) - held) > 1e-6:
                bad.append(f"{it.code}: booked {it.booked} != open shipments {held}")
        for po in db.query(PurchaseOrder).filter(PurchaseOrder.id.in_(po_ids)).all():
            if any((l.received_quantity or 0) < -1e-9 for l in po.lines):
                bad.append(f"{po.code}: negative received")
            all_in = po.lines and all((l.received_quantity or 0) >= l.quantity - 1e-9 for l in po.lines)
            if po.status == "received" and not all_in:
                bad.append(f"{po.code}: 'received' but lines are still open")
            if po.status in ("ordered", "partially_received") and all_in:
                bad.append(f"{po.code}: every line is in but status is {po.status}")
        live_of = {}
        for inv in db.query(Invoice).filter(Invoice.order_id.in_(order_ids)).all():
            total = sum(line_amount(l.quantity, l.unit_price) for l in inv.lines)
            if abs(inv.total - total) > 0.011:
                bad.append(f"{inv.code}: total {inv.total} != lines {total}")
            paid = sum(p.amount for p in inv.payments)
            if abs(inv.amount_paid - paid) > 0.011 or (inv.status != "void" and abs(inv.balance - (inv.total - paid)) > 0.011 and inv.funding_amount is None):
                bad.append(f"{inv.code}: paid {inv.amount_paid} / balance {inv.balance} don't follow total {inv.total} and payments {paid}")
            if inv.status != "void":
                for s in inv.shipments:
                    if s.id in live_of:
                        bad.append(f"{s.code} is on two live invoices: {live_of[s.id]} and {inv.code}")
                    live_of[s.id] = inv.code
        for s in db.query(Shipment).filter(Shipment.order_id.in_(order_ids)).all():
            if (s.status == "invoiced") != (s.id in live_of):
                bad.append(f"{s.code}: status {s.status} but live invoice {live_of.get(s.id)}")
    return bad


class Walk:
    def __init__(self, api, make, client, headers, seed):
        self.api, self.make, self.client, self.headers, self.rng = api, make, client, headers, random.Random(seed)
        self.items, self.pos, self.orders, self.log, self.stats = [], [], [], [], {}

    def call(self, method, url, **kw):
        r = self.client.request(method, url, headers=self.headers, **kw)
        assert r.status_code < 500, f"{method} {url} -> {r.status_code}: {r.text[:300]} | log: {self.log[-12:]}"
        key = self.log[-1]
        self.stats.setdefault(key, [0, 0])[0 if r.status_code < 300 else 1] += 1
        return r.json() if r.status_code < 300 and r.content and r.headers.get("content-type", "").startswith("application/json") else None

    def setup(self):
        code = uid("SW")
        self.generic = self.make.item(code=f"{code}G-NUT", title="5/8-11 2H heavy hex nut generic", group="Nut", price=0)
        self.api.put(f"/api/stock-items/{self.generic['id']}", json={"is_generic": True})
        bolt = self.make.item(code=code, title="5/8-11 stud w/ nut", price=3)
        nut = self.make.item(code=f"{code}-NUT", title="5/8-11 2H heavy hex nut", group="Nut", price=0)
        self.api.put(f"/api/stock-items/{nut['id']}", json={"parent_item_id": self.generic["id"]})
        washer = self.make.item(code=uid("SWW"), title="5/8 F436 washer", group="Washer", price=0.2)
        self.items = [bolt, nut, self.generic, washer]
        for it in (bolt, self.generic, washer):
            self.make.stock(it, 400, cost=0.5)
        for _ in range(2):
            self.orders.append(self.make.order(lines=[(bolt, 150, 3), (nut, 150, 0), (washer, 300, 0.2)]))

    def step(self):
        rng, api = self.rng, self.api
        pos = [api.get(f"/api/purchase-orders/{p}") for p in self.pos]
        ships = [s for s in api.get("/api/shipments/") if s["order_id"] in {o["id"] for o in self.orders}]
        invs = [i for i in api.get("/api/invoices/") if i["order_id"] in {o["id"] for o in self.orders}]
        op = rng.choice(["po_new", "po_new", "po_order", "po_receive", "po_receive", "po_edit", "po_charge", "po_cancel", "po_delete",
                         "adjust", "adjust", "lot_hold", "transfer", "return", "ship", "ship", "invoice", "invoice", "pay", "send",
                         "void", "merge", "split", "inv_delete", "unship"])
        self.log.append(op)
        it = rng.choice(self.items)
        if op == "po_new":
            p = self.call("POST", "/api/purchase-orders/", json={"vendor_id": self.make.vendor()["id"], "lines": [
                {"item_id": x["id"], "quantity": rng.randint(1, 200), "unit_cost": round(rng.uniform(0.1, 2), 2)} for x in rng.sample(self.items, rng.randint(1, 3))]})
            if p:
                self.pos.append(p["id"])
        elif op == "po_order" and pos:
            self.call("POST", f"/api/purchase-orders/{rng.choice(pos)['id']}/mark-ordered")
        elif op == "po_receive" and pos:
            p = rng.choice(pos)
            lines = [{"line_id": l["id"], "quantity": rng.randint(1, max(1, int(l["quantity"] - l["received_quantity"]) + (5 if rng.random() < .2 else 0)))}
                     for l in p["lines"] if rng.random() < .7]
            if lines:
                self.call("POST", f"/api/purchase-orders/{p['id']}/receive", json={"lines": lines})
        elif op == "po_edit" and pos:
            p = rng.choice(pos)
            if p["lines"]:
                l = rng.choice(p["lines"])
                self.call("PUT", f"/api/purchase-orders/{p['id']}/lines/{l['id']}", json={"quantity": rng.randint(1, 250), "unit_cost": round(rng.uniform(0.1, 3), 2)})
        elif op == "po_charge" and pos:
            p = rng.choice(pos)
            if p.get("charges") and rng.random() < .4:
                self.call("DELETE", f"/api/purchase-orders/{p['id']}/charges/{p['charges'][0]['id']}")
            else:
                self.call("POST", f"/api/purchase-orders/{p['id']}/charges", json={"charge_type": rng.choice(["freight", "tariff", "other"]), "amount": round(rng.uniform(1, 80), 2)})
        elif op == "po_cancel" and pos:
            self.call("POST", f"/api/purchase-orders/{rng.choice(pos)['id']}/cancel")
        elif op == "po_delete" and pos:
            p = rng.choice(pos)
            self.call("DELETE", f"/api/purchase-orders/{p['id']}")
            if self.client.get(f"/api/purchase-orders/{p['id']}", headers=self.headers).status_code == 404:
                self.pos.remove(p["id"])
        elif op == "adjust":
            cur = api.get(f"/api/stock-items/{it['id']}")["on_hand"]
            new = max(0, cur + rng.randint(-60, 60))
            self.call("PUT", f"/api/stock-items/{it['id']}", json={"on_hand": new, "adjustment_unit_cost": 0.75, "adjustment_note": "walk"})
        elif op == "lot_hold":
            lots = [l for l in api.get("/api/lots/") if l["item_id"] in {x["id"] for x in self.items}]
            if lots:
                self.call("PUT", f"/api/lots/{rng.choice(lots)['id']}/status", json={"status": rng.choice(["on_hold", "available", "available"])})
        elif op == "transfer":
            self.call("POST", f"/api/stock-items/{self.items[1]['id']}/transfer-from-parent", json={"quantity": rng.randint(1, 120)})
        elif op == "return":
            self.call("POST", f"/api/stock-items/{self.items[1]['id']}/return-to-parent", json={"quantity": rng.randint(1, 80)})
        elif op == "ship":
            o = api.get(f"/api/customer-orders/{rng.choice(self.orders)['id']}")
            lines = [{"line_id": l["id"], "quantity": rng.randint(1, int(l["quantity"] - l["shipped_quantity"] - l["booked_quantity"]))}
                     for l in o["lines"] if l["quantity"] - l["shipped_quantity"] - l["booked_quantity"] >= 1 and rng.random() < .8]
            if lines:
                sh = self.call("POST", f"/api/customer-orders/{o['id']}/shipments", json={"lines": lines})
                if sh:
                    for step in ("confirm-booking", "pick", "accept-packing", "ship"):
                        self.call("POST", f"/api/shipments/{sh['id']}/{step}", json={"pick_all": True} if step == "pick" else None)
        elif op == "unship":
            done = [s for s in ships if s["status"] in SHIPPED]
            if done:
                self.call("POST", f"/api/shipments/{rng.choice(done)['id']}/unship", json={"reason": "walk", "customer_notified": True, "combined_ok": True})
        elif op == "invoice":
            free = [s for s in ships if s["status"] in ("shipped", "delivered")]
            if free:
                pick = rng.sample(free, min(len(free), rng.randint(1, 2)))
                if len({s["order_id"] for s in pick}) == 1 and len(pick) > 1:
                    self.call("POST", "/api/invoices/from-shipments", json={"shipment_ids": [s["id"] for s in pick]})
                else:
                    self.call("POST", f"/api/invoices/from-shipment/{pick[0]['id']}", json={"shipping_charge": rng.choice([0, 0, 25])})
        elif op == "pay" and invs:
            payable = [i for i in invs if i["status"] == "sent" and i["balance"] > 0.01] or invs
            inv = rng.choice(payable)
            if inv["payments"] and rng.random() < .3:
                self.call("DELETE", f"/api/invoices/{inv['id']}/payments/{inv['payments'][0]['id']}")
            else:  # mostly within the balance (part or all of it), now and then too much
                amt = inv["balance"] if rng.random() < .3 else rng.uniform(0.01, max(0.02, inv["balance"])) if rng.random() < .85 else inv["balance"] + 15
                self.call("POST", f"/api/invoices/{inv['id']}/payments", json={"amount": round(amt, 2)})
        elif op == "send" and invs:
            drafts = [i for i in invs if i["status"] == "draft"] or invs
            self.call("PUT", f"/api/invoices/{rng.choice(drafts)['id']}/status", json={"status": "sent"})
        elif op == "void" and invs:
            self.call("PUT", f"/api/invoices/{rng.choice(invs)['id']}/status", json={"status": "void", "reason": "walk"})
        elif op == "merge":
            drafts = [i for i in invs if i["status"] == "draft"]
            if len(drafts) >= 2:
                a, b = rng.sample(drafts, 2)
                self.call("POST", f"/api/invoices/{a['id']}/merge", json={"invoice_ids": [b["id"]]})
        elif op == "split":
            combined = [i for i in invs if i.get("is_combined")]
            if combined:
                self.call("POST", f"/api/invoices/{rng.choice(combined)['id']}/split")
        elif op == "inv_delete" and invs:
            self.call("DELETE", f"/api/invoices/{rng.choice(invs)['id']}")
        bad = _check([x["id"] for x in self.items], self.pos, [o["id"] for o in self.orders])
        assert not bad, f"after {self.log[-12:]}:\n" + "\n".join(bad)


@pytest.mark.parametrize("seed", range(8))
def test_stock_and_money_walk(api, make, client, admin_headers, seed):
    w = Walk(api, make, client, admin_headers, seed)
    w.setup()
    for _ in range(90):
        w.step()
    print(seed, {k: f"{a}/{b}" for k, (a, b) in sorted(w.stats.items())})
    assert sum(a for a, _ in w.stats.values()) > 35  # it really moved things, not just bounced off refusals
