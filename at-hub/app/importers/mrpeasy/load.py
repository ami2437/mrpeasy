"""Build a fresh AT-HUB database from an MRPeasy snapshot.

Every run starts from an empty file (at_hub_import.db by default), so dry runs can be
repeated until the reconciliation is clean. Only users and the company profile are
copied from the live at_hub.db, so people can log in to look at the result. The live
database is opened read-only and never written.

Load order follows the dependencies: groups -> items -> customers/vendors -> POs ->
lots -> customer orders -> shipments -> invoices -> packing data -> stock ledger.
"""
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from itertools import combinations
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, CompanyProfile, Customer, CustomerOrder, CustomerOrderLine, InventoryTransaction, Invoice, InvoiceLine,
    InvoicePayment, InvoiceShipment, Lot, NumberSeries, PalletWeight, ProductGroup, PurchaseOrder, PurchaseOrderCharge,
    PurchaseOrderLine, Shipment, ShipmentBox, ShipmentLine, StockItem, User, Vendor, VendorBill, VendorItem,
)

AT_HUB = Path(__file__).resolve().parents[3]
LIVE_DB = AT_HUB / "at_hub.db"
TARGET_DB = AT_HUB / "at_hub_import.db"
OLD_BACKEND_DB = AT_HUB.parent / "backend-fastapi" / "mrpeasy.db"
BY = "mrpeasy-import"

# MRPeasy status codes (from status_txt in the snapshot)
CO_STATUS = {"30": "confirmed", "60": "confirmed", "70": "shipped", "80": "shipped"}
SH_SHIPPED, SH_READY = "20", "15"
LOT_IN_STOCK = "20"  # 10 = expected on an open PO, 5 = planned: not physical stock
INV_PAID, INV_UNPAID, INV_DUMMY = "40", "20", "10"

# Custom fields: mapped ones go to real columns; every value is also kept in custom_fields.
CUSTOM_LABELS = {
    "custom_814": "Job #", "custom_815": "Job #", "custom_531": "Job #",
    "custom_753": "Ship To", "custom_570": "Disbursement Date", "custom_571": "Funding Amount",
    "custom_572": "Funding Discount",
}
DATE_CUSTOM = {"custom_218", "custom_570"}
# Per-record corrections agreed with the business, applied after mapping (MRPeasy itself is not changed).
VENDOR_OVERRIDES = {"V00001": {"email": None}}  # Superior Bolts: leave email blank for now

IGNORED_CUSTOM = {"custom_748", "custom_775", "custom_766", "custom_218"}  # not wanted (218 has its own column)


# ---------- small helpers ----------
def f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def dt(v):
    """MRPeasy sends unix timestamps as strings."""
    try:
        return datetime.fromtimestamp(int(v)) if v not in (None, "", "0", 0) else None
    except (TypeError, ValueError):
        return None


def clean(v):
    v = (v or "").strip() if isinstance(v, str) else v
    return v or None


def address_text(a) -> str:
    if not a:
        return None
    if isinstance(a, str):
        return clean(a.replace("\r\n", "\n"))
    name = " ".join(x for x in (a.get("first_name"), a.get("last_name")) if x)
    city_line = " ".join(x for x in (a.get("city"), a.get("state"), a.get("postal_code")) if x)
    parts = [a.get("company"), name, a.get("street_line_1"), a.get("street_line_2"), city_line,
             a.get("country_code") if a.get("country_code") not in (None, "", "US") else None]
    return "\n".join(p for p in parts if p) or None


def custom_fields(rec: dict) -> str:
    out = {}
    for k, v in rec.items():
        if not k.startswith("custom_") or v in (None, "") or k in IGNORED_CUSTOM:
            continue
        if k in DATE_CUSTOM and dt(v):
            v = dt(v).strftime("%Y-%m-%d")
        elif isinstance(v, str) and re.fullmatch(r"-?\d+\.\d+", v):
            v = f(v)
        out[CUSTOM_LABELS.get(k, k)] = v
    return json.dumps(out) if out else None


class Report:
    """Everything the loader had to infer, skip or patch -- printed and saved with the reconciliation."""
    def __init__(self):
        self.notes = defaultdict(list)

    def add(self, section, msg):
        self.notes[section].append(msg)

    def dump(self) -> dict:
        return {k: v for k, v in self.notes.items()}


# ---------- the load ----------
def load(snapshot: Path, target: Path = TARGET_DB) -> Path:
    snap = lambda name: json.loads((snapshot / f"{name}.json").read_text(encoding="utf-8"))
    rep = Report()
    if target.exists():
        target.unlink()
    engine = create_engine(f"sqlite:///{target}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()

    _copy_users_and_company(db, rep)
    groups, units = snap("product_groups"), {u["unit_id"]: u["title"] for u in snap("units")}
    for g in groups:
        db.add(ProductGroup(name=g["title"]))

    # --- items (plus any deleted item that still holds stock or history) ---
    items = {}
    for it in snap("items"):
        items[it["article_id"]] = StockItem(
            mrp_id=it["article_id"], code=it["code"], title=it["title"] or it["code"], unit=units.get(it["unit_id"]),
            category=it["group_title"], selling_price=f(it["selling_price"]), cost_price=f(it["avg_cost"]),
            reorder_point=f(it["min_quantity"]), is_active=not it["deleted"])
    inventory = snap("inventory")
    lots_raw = snap("lots")
    for r in inventory:
        if r["article_id"] not in items:
            items[r["article_id"]] = StockItem(mrp_id=r["article_id"], code=r["product_code"], title=r["product_title"],
                                               category=r["group_title"], cost_price=f(r["avg_cost"]), is_active=False)
            rep.add("items", f"{r['product_code']} is deleted in MRPeasy but holds {f(r['quantity']):g} on hand: imported as inactive")
    db.add_all(items.values())

    # --- customers / vendors ---
    customers = {}
    for c in snap("customers"):
        cd = c.get("contact_data") or []
        pick = lambda t: next((d["value"] for d in cd if d["type"] == t), None)
        customers[c["customer_id"]] = Customer(
            mrp_id=c["customer_id"], name=c["title"], phone=clean(pick("phone")), email=clean(pick("email")),
            address=address_text(pick("address")), shipping_address=address_text(pick("shipping_address")))
    db.add_all(customers.values())
    vendors = {}
    for v in snap("vendors"):
        cd = v.get("contact_data") or []
        every = lambda t: [clean(d["value"]) for d in cd if d["type"] == t and isinstance(d["value"], str) and clean(d["value"])]
        # AT-HUB has one phone/email field: keep every value; fax and website go under the address
        address = address_text(next((d["value"] for d in cd if d["type"] == "address"), None))
        extra = [f"Fax: {x}" for x in every("fax")] + [f"Web: {x}" for x in every("web")]
        vendors[v["vendor_id"]] = Vendor(mrp_id=v["vendor_id"], code=v["code"], name=v["title"],
                                         phone=", ".join(every("phone")) or None, email=", ".join(every("email")) or None,
                                         address="\n".join(x for x in [address, *extra] if x) or None)
    for v in vendors.values():
        for field, value in VENDOR_OVERRIDES.get(v.code, {}).items():
            setattr(v, field, value)
    db.add_all(vendors.values())
    db.flush()

    # vendor part numbers from each item's purchase terms
    seen = set()
    for it in snap("items"):
        for t in it.get("purchase_terms") or []:
            code = clean(t.get("vendor_product_code"))
            if not code or t["vendor_id"] not in vendors:
                continue
            key = (t["vendor_id"], code.lower())
            if key in seen:
                rep.add("vendor part #", f"{t['vendor_title']} uses {code} for more than one item: kept the first")
                continue
            seen.add(key)
            db.add(VendorItem(vendor_id=vendors[t["vendor_id"]].id, item_id=items[it["article_id"]].id,
                              vendor_item_code=code, last_unit_cost=f(t["price"]) or None))
    vcode = {(t["vendor_id"], it["article_id"]): clean(t.get("vendor_product_code"))
             for it in snap("items") for t in it.get("purchase_terms") or []}

    # --- purchase orders ---
    lot_by_id = {l["lot_id"]: l for l in lots_raw}
    lots_by_po_item = defaultdict(list)
    for l in lots_raw:
        if l["pur_ord_id"]:
            lots_by_po_item[(l["pur_ord_id"], l["article_id"])].append(l)
    pos, po_lines = {}, {}
    lots = {}  # MRPeasy lot code -> Lot
    for o in snap("purchase_orders"):
        po = PurchaseOrder(
            mrp_id=o["pur_ord_id"], code=o["code"], vendor_id=vendors[o["vendor_id"]].id,
            order_date=dt(o["order_date"]) or dt(o["created"]), expected_date=dt(o["expected_date"]),
            vendor_so_number=clean(o.get("order_number")),
            notes=clean(o["free_text"]), custom_fields=custom_fields(o), created_by=BY, created_at=dt(o["created"]))
        db.add(po)
        pos[o["pur_ord_id"]] = po
        used_lots = set()
        for p in sorted(o["products"], key=lambda p: p["ord"] or 0):
            line = PurchaseOrderLine(po=po, mrp_id=p["line_id"], item_id=items[p["article_id"]].id, quantity=f(p["quantity"]),
                                     unit_cost=f(p["item_price"]), vendor_item_code=vcode.get((o["vendor_id"], p["article_id"])),
                                     vendor_description=clean(p["description"]))
            db.add(line)
            db.flush()
            po_lines[p["line_id"]] = line
            # Lots received on this line: the line's own lot, plus any other in-stock lot of this PO+item not
            # claimed by another line (split receipts). A received line whose lot is used up is rebuilt.
            mine = []
            if p["lot_id"] in lot_by_id:
                mine.append(lot_by_id[p["lot_id"]])
            for l in lots_by_po_item[(o["pur_ord_id"], p["article_id"])]:
                if l["lot_id"] != p["lot_id"] and l["lot_id"] not in used_lots and not any(
                        q["lot_id"] == l["lot_id"] for q in o["products"]):
                    mine.append(l)
            received = 0.0
            for l in mine:
                used_lots.add(l["lot_id"])
                if l["status"] != LOT_IN_STOCK:
                    continue  # expected, not yet received
                lots[l["code"]] = _lot(l, items, line, po.code)
                received += f(l["quantity"])
            arrived = o["status"] == "40" or p["arrival_date"]
            if arrived and not received and p["lot_code"]:
                lots[p["lot_code"]] = Lot(item_id=items[p["article_id"]].id, lot_code=p["lot_code"], quantity=0,
                                          initial_quantity=f(p["quantity"]), base_unit_cost=f(p["item_price"]),
                                          unit_cost=f(p["item_price"]), po_line_id=line.id, source="purchase",
                                          source_reference=po.code, received_date=dt(p["arrival_date"]) or po.order_date)
                received = f(p["quantity"])
                rep.add("lots rebuilt", f"{p['lot_code']} ({po.code}): used up, not in MRPeasy's lot list -- rebuilt from the PO line")
            elif arrived and not received:
                received = f(p["quantity"])
                rep.add("po lines", f"{po.code} line {p['ord']}: received but has no lot code -- marked received without a lot")
            line.received_quantity = min(received, line.quantity) if received else 0
            if not received and clean(p["lot_code"]):
                line.planned_lot_code = p["lot_code"]  # MRPeasy assigns the lot # at order time; keep it for the receipt
        if f(o["fees_sum"]):
            po.charges.append(PurchaseOrderCharge(charge_type="shipping", amount=round(f(o["fees_sum"]), 2),
                                       description="Fees (imported from MRPeasy)", created_by=BY))
        if f(o["fees_taxable_sum"]):
            po.charges.append(PurchaseOrderCharge(charge_type="shipping", amount=round(f(o["fees_taxable_sum"]), 2),
                                                  description="Taxable fees (imported from MRPeasy)", created_by=BY))
        if f(o["tax_sum"]):
            po.charges.append(PurchaseOrderCharge(charge_type="other", amount=round(f(o["tax_sum"]), 2), description="Tax (MRPeasy)", created_by=BY))
        if f(o["discount_sum"]):
            po.charges.append(PurchaseOrderCharge(charge_type="other", amount=-round(f(o["discount_sum"]), 2),
                                       description="Discount (MRPeasy)", created_by=BY))
        # Vendor invoices: MRPeasy lists them under "bills" (the old single invoice_number field is usually empty).
        # The export carries no amount per bill: a PO with one bill is billed its full total; with several, the
        # split isn't known, so each comes in at $0 for the user to fill in.
        bills = [b for b in (o.get("bills") or []) if clean(b.get("invoice_number"))]
        if not bills and clean(o["invoice_number"]):
            bills = [{"invoice_number": o["invoice_number"], "invoice_date": o["invoice_date"], "due_date": o["due_date"]}]
        seen_bills = set()
        for b in bills:
            num = clean(b["invoice_number"])
            if num in seen_bills:
                continue
            seen_bills.add(num)
            one = len(bills) == 1
            po.bills.append(VendorBill(bill_number=num, bill_date=dt(b.get("invoice_date")), due_date=dt(b.get("due_date") or o["due_date"]),
                                       amount=round(f(o["total_price"]), 2) if one else 0,
                                       note="Imported from MRPeasy" if one else
                                       f"Imported from MRPeasy -- one of {len(bills)} invoices on this PO; MRPeasy doesn't export the split, enter the amount",
                                       created_by=BY))
        goods = [l for l in po.lines]
        if goods and all(l.received_quantity >= l.quantity - 1e-9 for l in goods):
            po.status = "received"
        elif any(l.received_quantity > 0 for l in goods):
            po.status = "partially_received"
        else:
            po.status = "ordered"

    # In-stock lots with no PO (opening stock, adjustments)
    for l in lots_raw:
        if l["status"] == LOT_IN_STOCK and l["code"] not in lots:
            lots[l["code"]] = _lot(l, items, None, None)
    skipped = Counter(l["status"] for l in lots_raw if l["status"] != LOT_IN_STOCK)
    if skipped:
        rep.add("lots", f"Not imported as stock (expected on open POs / planned): {dict(skipped)} lots by MRPeasy status")
    db.add_all([l for l in lots.values() if l.id is None])
    db.flush()

    # --- customer orders ---
    cos, co_lines = {}, {}
    lines_by_co_item = defaultdict(list)
    for o in snap("customer_orders"):
        # "PO # 4179330" -> "4179330"; only a literal "PO #" / "PO " before a number is dropped
        po_num = clean(re.sub(r"^\s*PO\s*#\s*|^\s*PO\s+(?=\d)", "", o["reference"] or "", flags=re.I))
        co = CustomerOrder(
            mrp_id=o["cust_ord_id"], code=o["code"], customer_id=customers[o["customer_id"]].id,
            order_date=dt(o["created"]), delivery_date=dt(o["delivery_date"]), status=CO_STATUS.get(str(o["status"]), "confirmed"),
            po_number=po_num, customer_po_date=dt(o.get("custom_218")), job_number=clean(o.get("custom_814")), ship_to_address=address_text(o["shipping_address"]),
            notes=clean((o["notes"] or "").replace("\r\n", "\n")), custom_fields=custom_fields(o), created_by=BY,
            created_at=dt(o["created"]))
        db.add(co)
        cos[o["cust_ord_id"]] = co
        for n, p in enumerate(sorted(o["products"], key=lambda p: p["ord"] or 0), start=1):
            line = CustomerOrderLine(order=co, mrp_id=p["line_id"], line_no=n, item_id=items[p["article_id"]].id,
                                     quantity=f(p["quantity"]), unit_price=f(p["item_price"]),
                                     delivery_date=dt(p["delivery_date"]), shipped_quantity=0)
            db.add(line)
            co_lines[p["line_id"]] = line
            lines_by_co_item[(o["cust_ord_id"], p["article_id"])].append((line, f(p["shipped"])))
    db.flush()

    # --- shipments: each picked quantity is spread over the order's lines for that item ---
    shipments = {}
    sh_raw = sorted(snap("shipments"), key=lambda s: (int(s["created"] or 0), s["shipment_id"]))
    capacity = {line.id: shipped for pairs in lines_by_co_item.values() for line, shipped in pairs}
    for s in sh_raw:
        order_ids = [s["customer_order_id"]] if s["customer_order_id"] else [x["customer_order_id"] for x in s["orders"]]
        co = cos[order_ids[0]]
        if len(order_ids) > 1:
            rep.add("shipments", f"{s['code']} covers orders {', '.join(cos[i].code for i in order_ids)}: filed under {co.code}, lines kept on their own orders")
        shipped = s["status"] == SH_SHIPPED
        sh = Shipment(mrp_id=s["shipment_id"], code=s["code"], order_id=co.id, status="shipped" if shipped else "ready",
                      ship_date=dt(s["delivery_date"]) if shipped else None, tracking_number=clean(s["tracking_number"]),
                      notes=clean("\n".join(x for x in (s["packing_notes"], s["waybill_notes"]) if x)),
                      custom_fields=custom_fields(s), created_by=BY, created_at=dt(s["created"]))
        db.add(sh)
        shipments[s["shipment_id"]] = sh
        for p in s["products"]:
            qty = f(p["quantity_picked"]) if shipped else f(p["quantity_booked"]) + f(p["quantity_picked"])
            if qty <= 0:
                continue
            lot = lots.get(p["lot_code"])
            if lot is None and p["lot_code"]:
                lot = Lot(item_id=items[p["article_id"]].id, lot_code=p["lot_code"], quantity=0, initial_quantity=0,
                          source="adjustment", source_reference="MRPeasy (lot not in export)", received_date=sh.created_at)
                lots[p["lot_code"]] = lot
                db.add(lot)
                rep.add("lots rebuilt", f"{p['lot_code']}: shipped on {s['code']} but not in MRPeasy's lot list -- rebuilt as a used-up lot")
            remaining = qty
            candidates = [pair for oid in order_ids for pair in lines_by_co_item.get((oid, p["article_id"]), [])]
            if not candidates:
                rep.add("shipments", f"{s['code']}: item {items[p['article_id']].code} isn't on its order -- line skipped")
                continue
            for line, _ in candidates:  # fill each order line up to what MRPeasy says it shipped
                take = min(remaining, max(0.0, capacity[line.id])) if shipped else remaining
                if take <= 1e-9:
                    continue
                _ship_line(db, sh, line, lot, take, p["quantity_picked"], shipped)
                if shipped:
                    capacity[line.id] -= take
                remaining -= take
                if remaining <= 1e-9:
                    break
            if remaining > 1e-9:  # more shipped than the order lines account for: put it on the last line
                line = candidates[-1][0]
                _ship_line(db, sh, line, lot, remaining, p["quantity_picked"], shipped)
                rep.add("shipments", f"{s['code']}: {remaining:g} x {items[p['article_id']].code} beyond the order line's shipped qty -- added to line #{line.line_no}")
    db.flush()

    # --- invoices: MRPeasy doesn't link invoices to shipments, so pair them by quantities ---
    inv_raw = snap("invoices")
    invoices = {}
    sh_by_co = defaultdict(list)
    for s in sh_raw:
        if s["status"] == SH_SHIPPED:
            for oid in ([s["customer_order_id"]] if s["customer_order_id"] else [x["customer_order_id"] for x in s["orders"]]):
                sh_by_co[oid].append(s)
    claimed = set()
    portal_links = _portal_invoice_shipments()
    # Dummy (draft) invoices last, so a real invoice always gets first claim on its shipment
    for i in sorted(inv_raw, key=lambda i: (str(i["status"]) == INV_DUMMY, int(i["created"] or 0), i["invoice_id"])):
        status = {INV_PAID: "paid", INV_UNPAID: "sent"}.get(str(i["status"]), "draft")
        inv = Invoice(mrp_id=i["invoice_id"], code=i["code"], customer_id=customers[i["customer_id"]].id,
                      order_id=cos[i["cust_ord_id"]].id if i["cust_ord_id"] in cos else None,
                      invoice_date=dt(i["created"]), due_date=dt(i["due_date"]), status=status,
                      free_text=clean(i["free_text"]), disbursement_date=dt(i.get("custom_570")),
                      funding_amount=f(i.get("custom_571")) or None, funding_discount=f(i.get("custom_572")) or None,
                      custom_fields=custom_fields(i), created_by=BY, created_at=dt(i["created"]))
        db.add(inv)
        db.flush()
        invoices[i["invoice_id"]] = inv
        if str(i["status"]) == INV_DUMMY:
            rep.add("invoices", f"{i['code']} is a Dummy invoice in MRPeasy ({f(i['total_price']):,.2f}): imported as draft")
        # pair with shipments of the same order whose items/quantities add up to this invoice
        pool = [s for s in sh_by_co.get(i["cust_ord_id"], []) if (s["shipment_id"], i["cust_ord_id"]) not in claimed]
        shippable = {p["article_id"] for s in sh_by_co.get(i["cust_ord_id"], []) for p in s["products"]}
        want = Counter()
        for p in i["products"]:
            if p["article_id"] in shippable:  # a "Shipping" or service line never ships
                want[p["article_id"]] += f(p["quantity"])
        dates = [int(p["delivery_date"]) for p in i["products"] if p["delivery_date"]]
        match = [] if not want else _match_shipments(want, pool, max(dates) if dates else None)
        co_code = cos[i["cust_ord_id"]].code if i["cust_ord_id"] in cos else "?"
        recorded = portal_links.get(i["invoice_id"])
        if recorded:
            # our old portal created this invoice in MRPeasy (POST /invoices can't carry a shipment) and wrote
            # down which shipment(s) it billed: that record is the link, no matching needed
            match = [s for s in sh_by_co.get(i["cust_ord_id"], []) if s["code"] in recorded]
            rep.add("invoice links", f"{i['code']} ({co_code}): linked to {', '.join(sorted(recorded))} from the portal's record of sending it")
        elif str(i["status"]) == INV_DUMMY:
            # a draft is linked only to a shipment no real invoice took, and only when the quantities match exactly
            rep.add("invoice links", f"{i['code']} ({co_code}, Dummy): " + (f"linked to {', '.join(s['code'] for s in match)} (exact quantities)"
                                                                          if match else "not linked -- no unclaimed shipment matches its quantities"))
            match = match or []
        elif match is None:
            # Quantities don't add up (billed more/less than picked): fall back to the shipment(s) delivered
            # on the invoice lines' delivery dates, then to the only unclaimed shipment of the order.
            match = [s for s in pool if s["delivery_date"] and int(s["delivery_date"]) in dates] or (pool if len(pool) == 1 else [])
            rep.add("invoice links", f"{i['code']} ({co_code}): quantities differ from what was picked -- "
                    + (f"linked by date to {', '.join(s['code'] for s in match)}" if match else "NOT linked"))
        for s in match:
            claimed.add((s["shipment_id"], i["cust_ord_id"]))
            db.add(InvoiceShipment(invoice_id=inv.id, shipment_id=shipments[s["shipment_id"]].id))
        inv.shipment_id = shipments[match[0]["shipment_id"]].id if match else None
        # lines: to the MRPeasy order line when given, else the order's line for that item; shipment from the match
        for p in sorted(i["products"], key=lambda p: p["ord"] or 0):
            line = co_lines.get(p["co_line_id"])
            if line is None:
                cand = lines_by_co_item.get((i["cust_ord_id"], p["article_id"]), [])
                line = next((l for l, _ in cand if abs(l.unit_price - f(p["item_price"])) < 1e-6), cand[0][0] if cand else None)
            ship = next((shipments[s["shipment_id"]] for s in match
                         if any(q["article_id"] == p["article_id"] and f(q["quantity_picked"]) > 0 for q in s["products"])), None)
            it = items[p["article_id"]]
            db.add(InvoiceLine(invoice=inv, item_id=it.id, order_line_id=line.id if line else None,
                               shipment_id=ship.id if ship else None,
                               description=clean(p["description"]) or f"{it.code} - {it.title}",
                               quantity=f(p["quantity"]), unit_price=f(p["item_price"])))
        if status == "paid":
            db.add(InvoicePayment(invoice=inv, amount=round(f(i["total_price"]), 2), paid_date=dt(i["last_payment"]),
                                  note="Imported from MRPeasy (payment detail to be uploaded)", created_by=BY))
    db.flush()

    # shipment / order statuses now that invoices are linked
    invoiced_sh = {sid for (sid,) in db.query(InvoiceShipment.shipment_id).join(Invoice).filter(Invoice.status != "draft").all()}
    co_by_id = {o["cust_ord_id"]: o for o in snap("customer_orders")}
    for mrp_id, sh in shipments.items():
        if sh.status != "shipped":
            continue
        raw_co = co_by_id.get(next((s["customer_order_id"] or s["orders"][0]["customer_order_id"] for s in sh_raw if s["shipment_id"] == mrp_id), None), {})
        if sh.id in invoiced_sh:
            sh.status = "invoiced"
        elif str(raw_co.get("status")) == "80":
            sh.status = "delivered"
        if str(raw_co.get("status")) == "80":
            sh.delivered_at, sh.delivered_by = dt(raw_co.get("actual_delivery_date")) or sh.ship_date, BY
    for co in cos.values():
        all_shipped = all(l.shipped_quantity >= l.quantity - 1e-9 for l in co.lines)
        shs = [s for s in shipments.values() if s.order_id == co.id]
        if all_shipped and shs and all(s.status == "invoiced" for s in shs):
            co.status = "invoiced"

    _packing_from_old_backend(db, shipments, items, rep)
    _stock_ledger(db, items, lots, inventory, rep)
    _number_series(db, rep)
    db.commit()
    (snapshot / "load-notes.json").write_text(json.dumps(rep.dump(), indent=1), encoding="utf-8")
    print(f"Loaded into {target}")
    for section, msgs in rep.dump().items():
        print(f"  {section}: {len(msgs)} note(s)")
    db.close()
    engine.dispose()
    return target


def _lot(l, items, line, po_code):
    return Lot(item_id=items[l["article_id"]].id, lot_code=l["code"], quantity=f(l["available"]) + f(l["booked"]),
               initial_quantity=f(l["quantity"]), base_unit_cost=f(l["item_cost"]), unit_cost=f(l["item_cost"]),
               po_line_id=line.id if line is not None else None, received_date=dt(l["available_from"]) or dt(l["created"]) or datetime.now(),
               source="purchase" if line is not None else "adjustment",
               source_reference=po_code or "MRPeasy opening stock")


def _ship_line(db, sh, line, lot, qty, picked, shipped):
    db.add(ShipmentLine(shipment=sh, order_line=line, item_id=line.item_id, lot=lot, quantity=qty,
                        picked_quantity=qty if shipped else min(qty, f(picked)), unit_price=line.unit_price))
    if shipped:
        line.shipped_quantity += qty


def _portal_invoice_shipments() -> dict:
    """{MRPeasy invoice id: {shipment codes}} for invoices our old portal (backend-fastapi) sent to MRPeasy.
    MRPeasy's POST /invoices has no shipment field, so the portal's own submission lines are the only record."""
    if not OLD_BACKEND_DB.exists():
        return {}
    old = sqlite3.connect(f"file:{OLD_BACKEND_DB}?mode=ro", uri=True)
    try:
        rows = old.execute("""SELECT s.mrp_invoice_id, l.shipment_code FROM pending_invoice_submissions s
                              JOIN pending_invoice_submission_lines l ON l.submission_id = s.id
                              WHERE s.status = 'approved' AND s.mrp_invoice_id IS NOT NULL AND l.shipment_code IS NOT NULL""").fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        old.close()
    links = {}
    for inv_id, code in rows:
        links.setdefault(int(inv_id), set()).add(code)
    return links


def _match_shipments(want: Counter, pool: list, date=None):
    """Smallest set of shipments whose picked quantities equal the invoice's quantities; when
    several sets fit (identical repeat shipments), the one shipped closest to the invoice's date."""
    def picked(s):
        c = Counter()
        for p in s["products"]:
            c[p["article_id"]] += f(p["quantity_picked"])
        return c
    sums = [(s, picked(s)) for s in pool if set(picked(s)) & set(want)]
    for size in range(1, min(len(sums), 6) + 1):
        fits = []
        for combo in combinations(sums, size):
            total = Counter()
            for _, c in combo:
                total.update(c)
            if all(abs(total[k] - want[k]) < 1e-6 for k in want):  # extra unbilled items (companion nuts) are fine
                fits.append([s for s, _ in combo])
        if fits:
            gap = lambda combo: sum(abs(int(s["delivery_date"] or 0) - date) for s in combo) if date else 0
            return min(fits, key=gap)
    return None


def _packing_from_old_backend(db, shipments, items, rep):
    """Box labels, pallet weights and pack sizes were kept in the old backend's own database."""
    if not OLD_BACKEND_DB.exists():
        rep.add("packing", f"{OLD_BACKEND_DB} not found -- no box/pallet history imported")
        return
    old = sqlite3.connect(f"file:{OLD_BACKEND_DB}?mode=ro", uri=True)
    sh_by_code = {s.code: s for s in shipments.values()}
    item_by_code = {i.code: i for i in items.values()}
    n_box = n_pal = n_pack = 0
    for code, item_code, order_line, box_no, qty, lot_codes, pallet in old.execute(
            "select shipment_code, item_code, order_line, box_number, quantity_in_box, lot_codes, pallet_number from shipment_boxes"):
        sh, it = sh_by_code.get(code), item_by_code.get(item_code)
        if not sh or not it:
            rep.add("packing", f"box for {code}/{item_code}: shipment or item not in MRPeasy export -- skipped")
            continue
        co_line = next((l.order_line for l in sh.lines if l.order_line.line_no == int(order_line or 0) and l.item_id == it.id), None) \
            or next((l.order_line for l in sh.lines if l.item_id == it.id), None)
        lots = ", ".join(json.loads(lot_codes)) if lot_codes and lot_codes.startswith("[") else lot_codes
        db.add(ShipmentBox(shipment=sh, order_line_id=co_line.id if co_line else None, item_id=it.id,
                           box_number=box_no, quantity_in_box=qty, lot_code=lots, pallet_number=pallet))
        n_box += 1
    for code, pallet, weight, dims in old.execute("select shipment_code, pallet_number, weight, dimensions from pallet_weights"):
        if code in sh_by_code:
            db.add(PalletWeight(shipment=sh_by_code[code], pallet_number=pallet, weight=weight, dimensions=dims))
            n_pal += 1
    for item_code, size in old.execute("select item_code, pack_size from pack_sizes"):
        if item_code in item_by_code and size:
            item_by_code[item_code].default_pack_size = int(size)
            n_pack += 1
    old.close()
    rep.add("packing", f"Imported {n_box} boxes, {n_pal} pallet weights, {n_pack} pack sizes from the old backend")


def _stock_ledger(db, items, lots, inventory, rep):
    """Receipt per lot, issue per shipped line, and a balancing adjustment where MRPeasy's history
    doesn't add up -- so every lot's ledger sums to its imported quantity."""
    db.flush()
    issued = Counter()
    for sl in db.query(ShipmentLine).join(Shipment).filter(Shipment.status.in_(("shipped", "delivered", "invoiced"))).all():
        if sl.lot_id:
            issued[sl.lot_id] += sl.quantity
            db.add(InventoryTransaction(item_id=sl.item_id, lot_id=sl.lot_id, quantity_delta=-sl.quantity, type="shipment",
                                        reference=sl.shipment.code, note="Imported from MRPeasy", created_by=BY,
                                        created_at=sl.shipment.ship_date or sl.shipment.created_at))
    gaps = 0
    for lot in lots.values():
        initial = lot.initial_quantity or 0
        if initial:
            db.add(InventoryTransaction(item_id=lot.item_id, lot_id=lot.id, quantity_delta=initial,
                                        type="receipt", reference=lot.source_reference, note=f"Lot {lot.lot_code} (MRPeasy)",
                                        created_by=BY, created_at=lot.received_date))
        gap = lot.quantity - (initial - issued[lot.id])
        if abs(gap) > 1e-6:
            gaps += 1
            db.add(InventoryTransaction(item_id=lot.item_id, lot_id=lot.id, quantity_delta=gap, type="adjustment",
                                        reference="MRPeasy import", note="Balances MRPeasy's history to its current lot quantity",
                                        created_by=BY))
            if lot.initial_quantity == 0:
                lot.initial_quantity = issued[lot.id] + lot.quantity
    if gaps:
        rep.add("stock", f"{gaps} lots needed a balancing adjustment (MRPeasy history didn't add up to the current quantity)")
    on_hand = Counter()
    for lot in lots.values():
        on_hand[lot.item_id] += lot.quantity
    booked = Counter()
    for sl in db.query(ShipmentLine).join(Shipment).filter(Shipment.status.in_(("new", "ready"))).all():
        booked[sl.item_id] += sl.quantity
    for it in items.values():
        it.on_hand, it.booked = on_hand[it.id], booked[it.id]


def _number_series(db, rep):
    """Continue MRPeasy's numbering: the newest style in use per document type."""
    for key, model, attr, prefix, width in [("CO", CustomerOrder, "code", "C", 5), ("PO", PurchaseOrder, "code", "PO", 6),
                                            ("SH", Shipment, "code", "SH", 6), ("INV", Invoice, "code", "Inv-", 7),
                                            ("LOT", Lot, "lot_code", "L", 5), ("V", Vendor, "code", "V", 5)]:
        db.add(NumberSeries(key=key, prefix=prefix, width=width))
        codes = [c for (c,) in db.query(getattr(model, attr)).all()]
        if key == "LOT":  # lot #s promised to open PO lines are taken too
            codes += [c for (c,) in db.query(PurchaseOrderLine.planned_lot_code).filter(PurchaseOrderLine.planned_lot_code.isnot(None)).all()]
        nums = [int(c[len(prefix):]) for c in codes if c and c.startswith(prefix) and c[len(prefix):].isdigit() and len(c) - len(prefix) == width]
        rep.add("numbering", f"{key}: next is {prefix}{(max(nums, default=0) + 1):0{width}d}")


def _copy_users_and_company(db, rep):
    if not LIVE_DB.exists():
        rep.add("users", "No live at_hub.db -- no users copied; create one before logging in")
        return
    live = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    live.row_factory = sqlite3.Row
    cols = {c.name for c in User.__table__.columns}
    for r in live.execute("select * from users"):
        db.add(User(**{k: r[k] for k in r.keys() if k in cols and k not in ("last_login", "created_at")}))
    cols = {c.name for c in CompanyProfile.__table__.columns}
    for r in live.execute("select * from company_profile"):
        db.add(CompanyProfile(**{k: r[k] for k in r.keys() if k in cols and k != "updated_at"}))
    live.close()
    db.flush()
