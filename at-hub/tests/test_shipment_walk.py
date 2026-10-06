"""Random walks through the shipment life cycle (book, combine bolts + nuts, unbook, short-pick, pack, ship, undo, cancel,
delete, invoice) with the books checked after EVERY step. Seeded, so a failure replays exactly; never a 5xx.

Invariants (on the items / orders the walk made):
  - item.booked == what its open shipments hold; on_hand == the sum of its lots; nothing negative
  - each order line: shipped == what its shipped shipments carried; shipped + booked never above... ordered is not
    enforced here (overshipping is refused at booking), but shipped/booked are never negative
  - combos: only on open or shipped shipments, at most one per line, never more than what's booked (bolt and nut x ratio)
  - a shipment with packing accepted that's still open has boxes matching what it has to box; a shipped one was fully picked
"""
import random

import pytest

from tests.builders import uid

SHIPPED = ("shipped", "delivered", "invoiced")


def _db_check(item_ids, order_ids):
    from app.config.database import SessionLocal
    from app.models import CustomerOrder, Lot, Shipment, ShipmentLine, StockItem
    from app.services.nut_combos import booked_by_line, packing_quantities
    problems = []
    with SessionLocal() as db:
        for item in db.query(StockItem).filter(StockItem.id.in_(item_ids)).all():
            held = sum(sl.quantity for sl in db.query(ShipmentLine).join(Shipment, Shipment.id == ShipmentLine.shipment_id)
                       .filter(ShipmentLine.item_id == item.id, Shipment.status.in_(("new", "ready"))).all())
            if abs((item.booked or 0) - held) > 1e-6:
                problems.append(f"{item.code}: booked {item.booked} but open shipments hold {held}")
            lots = sum(l.quantity for l in db.query(Lot).filter(Lot.item_id == item.id).all())
            if abs(item.on_hand - lots) > 1e-6:
                problems.append(f"{item.code}: on hand {item.on_hand} but lots hold {lots}")
            if item.on_hand < -1e-9 or (item.booked or 0) < -1e-9:
                problems.append(f"{item.code}: negative on hand / booked")
        for order in db.query(CustomerOrder).filter(CustomerOrder.id.in_(order_ids)).all():
            for ol in order.lines:
                went = sum(sl.quantity for sl in ol.shipment_lines if sl.shipment.status in SHIPPED)
                if abs(ol.shipped_quantity - went) > 1e-6:
                    problems.append(f"{order.code} #{ol.line_no}: shipped {ol.shipped_quantity} but shipments carried {went}")
                if ol.shipped_quantity + ol.booked_quantity > ol.quantity + 1e-6:
                    problems.append(f"{order.code} #{ol.line_no}: shipped + booked {ol.shipped_quantity + ol.booked_quantity} > ordered {ol.quantity}")
            for sh in db.query(Shipment).filter(Shipment.order_id == order.id).all():
                if sh.combos and sh.status == "cancelled":
                    problems.append(f"{sh.code}: cancelled but still has combos")
                booked = booked_by_line(sh)
                seen = set()
                for c in sh.combos:
                    if {c.lead_line_id, c.member_line_id} & seen:
                        problems.append(f"{sh.code}: a line is in two combos")
                    seen |= {c.lead_line_id, c.member_line_id}
                    if c.quantity > booked.get(c.lead_line_id, 0) + 1e-6 or c.member_quantity > booked.get(c.member_line_id, 0) + 1e-6:
                        problems.append(f"{sh.code}: combo {c.quantity}x{c.ratio} bigger than booked {booked}")
                if sh.status in SHIPPED and any((l.picked_quantity or 0) < l.quantity - 1e-6 for l in sh.lines):
                    problems.append(f"{sh.code}: shipped but not fully picked")
                if sh.status in ("new", "ready") and sh.packed_at:
                    want, _ = packing_quantities(sh)
                    boxed = {}
                    for b in sh.boxes:
                        boxed[b.order_line_id] = boxed.get(b.order_line_id, 0) + b.quantity_in_box
                    if any(abs(boxed.get(k, 0) - v) > 1e-6 for k, v in want.items()) or any(k not in want for k in boxed):
                        problems.append(f"{sh.code}: packing accepted but boxes {boxed} != to box {want}")
    return problems


class Walk:
    def __init__(self, api, make, client, headers, seed):
        self.api, self.make, self.client, self.headers, self.rng = api, make, client, headers, random.Random(seed)
        self.items, self.orders, self.log, self.stats, self.deleted = [], [], [], {}, []

    def call(self, method, url, **kw):
        r = self.client.request(method, url, headers=self.headers, **kw)
        assert r.status_code < 500, f"{method} {url} -> {r.status_code}: {r.text[:300]} | log: {self.log[-12:]}"
        ok = r.status_code < 300
        key = self.log[-1] if self.log else method
        self.stats[key] = self.stats.get(key, [0, 0])
        self.stats[key][0 if ok else 1] += 1
        return r.status_code

    def setup(self):
        for k in range(2):
            code = uid("RW")
            bolt = self.make.item(code=code, title="stud w/ nut", price=2)
            nut = self.make.item(code=f"{code}-NUT", title="heavy hex nut", group="Nut", price=0)
            self.make.stock(bolt, 300)
            self.make.stock(nut, 500)
            self.items += [bolt, nut]
            two = k == 1  # second pair: 2 nuts per bolt
            self.orders.append(self.make.order(lines=[(bolt, 100, 2), (nut, 200 if two else 100, 0)]))

    def shipments(self, statuses=None):
        out = []
        for o in self.orders:
            for s in self.api.get("/api/shipments/"):
                if s["order_id"] == o["id"] and (statuses is None or s["status"] in statuses):
                    out.append(s)
        return out

    def step(self):
        rng = self.rng
        o = self.api.get(f"/api/customer-orders/{rng.choice(self.orders)['id']}")
        open_sh = [s for s in self.shipments(("new", "ready")) if s["order_id"] == o["id"]]
        done_sh = [s for s in self.shipments(SHIPPED) if s["order_id"] == o["id"]]
        sh = rng.choice(open_sh) if open_sh else None
        ops = ["book"] + (["combine", "combine", "split", "unbook", "pick", "pick_short", "accept", "ship", "advance", "advance", "advance",
                           "cancel", "unpick", "unpack", "delete"] if sh else []) \
              + (["unship", "invoice", "deliver"] if done_sh else []) + (["restore"] if self.deleted else [])
        op = rng.choice(ops)
        self.log.append(op)
        if op == "advance":  # the next step of the normal flow: confirm -> pick -> accept packing -> ship
            if sh["status"] == "new":
                self.call("POST", f"/api/shipments/{sh['id']}/confirm-booking")
            elif any(l["picked_quantity"] < l["quantity"] for l in sh["lines"]):
                self.call("POST", f"/api/shipments/{sh['id']}/pick", json={"pick_all": True})
            elif not sh["packed_at"]:
                self.call("POST", f"/api/shipments/{sh['id']}/accept-packing")
            else:
                self.call("POST", f"/api/shipments/{sh['id']}/ship")
        elif op == "deliver":
            d = rng.choice(done_sh)
            self.call("POST", f"/api/shipments/{d['id']}/delivered", json={})
        elif op == "restore":
            code = self.deleted.pop(rng.randrange(len(self.deleted)))
            entry = next((e for e in self.api.get("/api/recycle-bin") if code in e["label"] and not e.get("restored_at")), None)
            if entry:
                self.call("POST", f"/api/recycle-bin/{entry['id']}/restore")
        elif op == "book":
            lines = [{"line_id": l["id"], "quantity": rng.randint(1, max(1, int(l["quantity"] - l["shipped_quantity"] - l["booked_quantity"])))}
                     for l in o["lines"] if l["quantity"] - l["shipped_quantity"] - l["booked_quantity"] >= 1 and rng.random() < .8]
            if lines:
                self.call("POST", f"/api/customer-orders/{o['id']}/shipments", json={"lines": lines})
        elif op == "combine":
            b, n = o["lines"][0], o["lines"][1]
            if rng.random() < .15:
                b, n = n, b  # backwards / odd pairs must be refused or handled, never crash
            booked = {}
            for l in sh["lines"]:
                booked[l["order_line_id"]] = booked.get(l["order_line_id"], 0) + l["quantity"]
            ratio = rng.choice([1, 1, 2, 3])
            fits = int(min(booked.get(b["id"], 0), booked.get(n["id"], 0) // ratio))
            qty = rng.randint(1, fits) if fits >= 1 and rng.random() < .8 else rng.randint(1, 150)  # mostly sensible, sometimes too many
            self.call("POST", f"/api/shipments/{sh['id']}/combos", json={"lead_line_id": b["id"], "member_line_id": n["id"],
                                                                          "quantity": qty, "ratio": ratio})
        elif op == "split" and sh["combos"]:
            self.call("DELETE", f"/api/shipments/{sh['id']}/combos/{sh['combos'][0]['id']}")
        elif op == "unbook":
            l = rng.choice(sh["lines"])
            left = l["quantity"] - l["picked_quantity"]
            self.call("POST", f"/api/shipments/{sh['id']}/unbook", json={"shipment_line_id": l["id"], "quantity": max(1, rng.randint(1, max(1, int(left))))})
        elif op == "pick":
            if sh["status"] == "new":
                self.call("POST", f"/api/shipments/{sh['id']}/confirm-booking")
            self.call("POST", f"/api/shipments/{sh['id']}/pick", json={"pick_all": True})
        elif op == "pick_short":
            if sh["status"] == "new":
                self.call("POST", f"/api/shipments/{sh['id']}/confirm-booking")
            lines = [{"shipment_line_id": l["id"], "quantity": rng.randint(0, int(l["quantity"] - l["picked_quantity"]))}
                     for l in sh["lines"] if l["quantity"] - l["picked_quantity"] >= 1]
            self.call("POST", f"/api/shipments/{sh['id']}/pick", json={"lines": [x for x in lines if x["quantity"] > 0], "unbook_rest": True})
        elif op == "accept":
            self.call("POST", f"/api/shipments/{sh['id']}/accept-packing")
        elif op == "ship":
            self.call("POST", f"/api/shipments/{sh['id']}/ship")
        elif op == "cancel":
            self.call("POST", f"/api/shipments/{sh['id']}/cancel")
        elif op == "unpick":
            self.call("POST", f"/api/shipments/{sh['id']}/unpick")
        elif op == "unpack":
            self.call("POST", f"/api/shipments/{sh['id']}/unpack")
        elif op == "delete":
            if self.call("DELETE", f"/api/shipments/{sh['id']}") < 300:
                self.deleted.append(sh["code"])
        elif op == "unship":
            d = rng.choice(done_sh)
            self.call("POST", f"/api/shipments/{d['id']}/unship", json={"reason": "walk", "customer_notified": True, "combined_ok": True})
        elif op == "invoice":
            d = rng.choice([s for s in done_sh if s["status"] != "invoiced"] or [None])
            if d:
                self.call("POST", f"/api/invoices/from-shipment/{d['id']}", json={"shipping_charge": 0})
        problems = _db_check([i["id"] for i in self.items], [o["id"] for o in self.orders])
        assert not problems, f"after {self.log[-12:]}:\n" + "\n".join(problems)


@pytest.mark.parametrize("seed", range(10))
def test_random_walk_keeps_the_books_straight(api, make, client, admin_headers, seed):
    w = Walk(api, make, client, admin_headers, seed)
    w.setup()
    for _ in range(80):
        w.step()
    print(seed, {k: f"{a} ok / {b} refused" for k, (a, b) in sorted(w.stats.items())})
    assert sum(a for a, _ in w.stats.values()) > 40  # the walk really moved things, not just bounced off refusals
