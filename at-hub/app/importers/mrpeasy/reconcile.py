"""Check an imported database against the MRPeasy snapshot it came from.

Each check is PASS / FAIL with the first few differences. The report is printed and
saved next to the snapshot as reconcile-<time>.md, together with the loader's notes.
"""
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Customer, CustomerOrder, InventoryTransaction, Invoice, InvoiceShipment, Lot, PurchaseOrder, Shipment,
    ShipmentLine, StockItem, Vendor,
)
from .load import TARGET_DB, f, INV_DUMMY, LOT_IN_STOCK, SH_SHIPPED

TOL = 0.01


def reconcile(snapshot: Path, target: Path = TARGET_DB) -> bool:
    snap = lambda name: json.loads((snapshot / f"{name}.json").read_text(encoding="utf-8"))
    engine = create_engine(f"sqlite:///{target}")
    db = sessionmaker(bind=engine)()
    results = []

    def check(name, diffs, total=None, note=""):
        results.append((name, diffs, total, note))

    # 1. record counts
    counts = [("Items (incl. deleted with stock)", StockItem, len({i["article_id"] for i in snap("items")} | {r["article_id"] for r in snap("inventory")})),
              ("Customers", Customer, len(snap("customers"))), ("Vendors", Vendor, len(snap("vendors"))),
              ("Customer orders", CustomerOrder, len(snap("customer_orders"))), ("Purchase orders", PurchaseOrder, len(snap("purchase_orders"))),
              ("Shipments", Shipment, len(snap("shipments"))), ("Invoices", Invoice, len(snap("invoices")))]
    check("Record counts", [f"{n}: MRPeasy {want}, AT-HUB {db.query(m).count()}" for n, m, want in counts if db.query(m).count() != want],
          len(counts))

    by_mrp = lambda model: {r.mrp_id: r for r in db.query(model).all()}

    # 2. stock on hand per item == MRPeasy stock report
    items = by_mrp(StockItem)
    inv = snap("inventory")
    check("On hand per item = MRPeasy stock", [f"{items[r['article_id']].code}: MRPeasy {f(r['quantity']):g}, AT-HUB {items[r['article_id']].on_hand:g}"
                                               for r in inv if abs(items[r["article_id"]].on_hand - f(r["quantity"])) > 1e-6], len(inv))

    # 3. in-stock lots: remaining qty per lot code
    lots = {l.lot_code: l for l in db.query(Lot).all()}
    raw = [l for l in snap("lots") if l["status"] == LOT_IN_STOCK]
    check("Lot quantities", [f"{l['code']}: MRPeasy {f(l['available']) + f(l['booked']):g}, AT-HUB {lots[l['code']].quantity:g}" if l["code"] in lots else f"{l['code']}: missing"
                             for l in raw if l["code"] not in lots or abs(lots[l["code"]].quantity - f(l["available"]) - f(l["booked"])) > 1e-6], len(raw))

    # 4. stock ledger: transactions per lot sum to the lot's quantity, per item to on hand
    led_lot, led_item = Counter(), Counter()
    for t in db.query(InventoryTransaction).all():
        led_lot[t.lot_id] += t.quantity_delta
        led_item[t.item_id] += t.quantity_delta
    all_lots = db.query(Lot).all()
    check("Stock ledger balances (lot + item)",
          [f"lot {l.lot_code}: ledger {led_lot[l.id]:g}, lot {l.quantity:g}" for l in all_lots if abs(led_lot[l.id] - l.quantity) > 1e-6]
          + [f"item {i.code}: ledger {led_item[i.id]:g}, on hand {i.on_hand:g}" for i in items.values() if abs(led_item[i.id] - i.on_hand) > 1e-6],
          len(all_lots) + len(items))

    # 5. customer order totals and shipped quantities per line
    cos = by_mrp(CustomerOrder)
    raw_cos = snap("customer_orders")
    check("Customer order totals", [f"{o['code']}: MRPeasy {f(o['total_price']):,.2f}, AT-HUB {sum(l.quantity * l.unit_price for l in cos[o['cust_ord_id']].lines):,.2f}"
                                    for o in raw_cos if abs(sum(l.quantity * l.unit_price for l in cos[o["cust_ord_id"]].lines) - f(o["total_price"])) > TOL], len(raw_cos))
    shipped_diffs, n = [], 0
    for o in raw_cos:
        lines = {l.mrp_id: l for l in cos[o["cust_ord_id"]].lines}
        for p in o["products"]:
            n += 1
            if abs(lines[p["line_id"]].shipped_quantity - f(p["shipped"])) > 1e-6:
                shipped_diffs.append(f"{o['code']} #{lines[p['line_id']].line_no}: MRPeasy shipped {f(p['shipped']):g}, AT-HUB {lines[p['line_id']].shipped_quantity:g}")
    check("Shipped qty per order line", shipped_diffs, n)

    # 6. shipments: picked quantity per item, and every line traced to a lot
    shs = by_mrp(Shipment)
    raw_sh = snap("shipments")
    diffs = []
    for s in raw_sh:
        want, got = Counter(), Counter()
        for p in s["products"]:
            want[p["article_id"]] += f(p["quantity_picked"]) if s["status"] == SH_SHIPPED else f(p["quantity_booked"]) + f(p["quantity_picked"])
        for l in shs[s["shipment_id"]].lines:
            got[l.item_id] += l.quantity
        want = Counter({items[k].id: v for k, v in want.items() if v})
        if any(abs(want[k] - got[k]) > 1e-6 for k in set(want) | set(got)):
            diffs.append(f"{s['code']}: quantities differ")
    check("Shipment quantities", diffs, len(raw_sh))
    no_lot = db.query(ShipmentLine).filter(ShipmentLine.lot_id.is_(None)).count()
    check("Shipment lines traced to a lot", [f"{no_lot} shipment lines have no lot"] if no_lot else [], db.query(ShipmentLine).count())

    # 7. purchase orders: lines + charges = MRPeasy total
    pos = by_mrp(PurchaseOrder)
    raw_pos = snap("purchase_orders")
    # MRPeasy's PO total excludes tax (AT-HUB counts it -- it's owed) and rounds every line to the cent;
    # AT-HUB rounds the sum. Differences of a few cents from that rounding alone are reported, not failed.
    po_diffs, rounding = [], []
    for o in raw_pos:
        want, got = f(o["total_price"]) + f(o["tax_sum"]), pos[o["pur_ord_id"]].order_total
        cents = abs(sum(round(f(p["quantity"]) * f(p["item_price"]), 2) - f(p["quantity"]) * f(p["item_price"]) for p in o["products"]))
        if abs(want - got) <= TOL:
            continue
        (rounding if abs(want - got) <= max(cents, 0.0) + 0.105 else po_diffs).append(f"{o['code']}: MRPeasy {want:,.2f}, AT-HUB {got:,.2f}")
    check("Purchase order totals (incl. tax)", po_diffs, len(raw_pos),
          f"· {len(rounding)} differ by cents from per-line rounding: {', '.join(rounding)}" if rounding else "")

    # 8. invoices: totals, status, balance owed, shipment links
    invs = by_mrp(Invoice)
    raw_inv = snap("invoices")
    check("Invoice totals", [f"{i['code']}: MRPeasy {f(i['total_price']):,.2f}, AT-HUB {invs[i['invoice_id']].total:,.2f}"
                             for i in raw_inv if abs(invs[i["invoice_id"]].total - f(i["total_price"])) > TOL], len(raw_inv))
    owed_mrp = sum(f(i["total_price"]) for i in raw_inv if i["status"] == "20")
    owed_hub = sum(v.balance for v in invs.values() if v.status == "sent")
    check("Open receivables (unpaid invoices)", [f"MRPeasy {owed_mrp:,.2f}, AT-HUB {owed_hub:,.2f}"] if abs(owed_mrp - owed_hub) > TOL else [], 1,
          f"{owed_mrp:,.2f} owed on {sum(1 for i in raw_inv if i['status'] == '20')} invoices")
    linked = {iid for (iid,) in db.query(InvoiceShipment.invoice_id).all()}
    check("Invoices linked to their shipments", [f"{i['code']}" for i in raw_inv if i["status"] != INV_DUMMY and invs[i["invoice_id"]].id not in linked],
          sum(1 for i in raw_inv if i["status"] != INV_DUMMY))
    combined = sum(1 for v in invs.values() if len(v.shipments) > 1)

    # 9. every shipped shipment of a fully-invoiced order is on an invoice
    sh_linked = {sid for (sid,) in db.query(InvoiceShipment.shipment_id).all()}
    uninv = [s.code for s in shs.values() if s.status in ("shipped", "delivered") and s.id not in sh_linked]

    db.close()
    engine.dispose()
    return _write(snapshot, results, combined, uninv)


def _write(snapshot, results, combined, uninvoiced) -> bool:
    ok = all(not d for _, d, _, _ in results)
    lines = [f"# MRPeasy import reconciliation — {datetime.now():%Y-%m-%d %H:%M}", "",
             f"Snapshot: `{snapshot.name}`  ·  Result: **{'ALL CHECKS PASS' if ok else 'DIFFERENCES FOUND'}**", "",
             "| Check | Result | Checked |", "|---|---|---|"]
    for name, diffs, total, note in results:
        lines.append(f"| {name} | {'PASS' if not diffs else f'FAIL ({len(diffs)})'} | {total if total is not None else ''} {note} |")
    lines += ["", f"Combined invoices created: {combined}", "",
              f"Shipped but not invoiced ({len(uninvoiced)}): {', '.join(uninvoiced) or 'none'}", ""]
    for name, diffs, _, _ in results:
        if diffs:
            lines += [f"## {name}", *[f"- {d}" for d in diffs[:25]], *([f"- … and {len(diffs) - 25} more"] if len(diffs) > 25 else []), ""]
    notes_file = snapshot / "load-notes.json"
    if notes_file.exists():
        lines += ["## Loader notes (inferred, rebuilt or skipped)", ""]
        for section, msgs in json.loads(notes_file.read_text(encoding="utf-8")).items():
            lines.append(f"**{section}** ({len(msgs)})")
            lines += [f"- {m}" for m in msgs[:15]] + ([f"- … and {len(msgs) - 15} more"] if len(msgs) > 15 else []) + [""]
    text = "\n".join(lines)
    out = snapshot / f"reconcile-{datetime.now():%Y%m%d-%H%M%S}.md"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nSaved: {out}")
    return ok
