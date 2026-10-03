"""Carry work done in AT-HUB into a fresh MRPeasy import, so a wipe + re-import loses none of it.

Read-only from the live at_hub.db; written into the import being built. Records are matched by what
stays the same across imports -- order / PO / shipment / invoice codes, item codes, customer and vendor
names, an order line's line #, a PO line's position among lines of the same item -- never by database id.

Carried: attached files (+ which PO lines an MTR covers), learned item matches, line notes (+ "print"),
vendor part # links made in AT-HUB, File Matcher / scan picks. Files themselves stay in uploads/ untouched;
only their database rows move. Anything whose record no longer exists is reported, not guessed.
"""
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime

from app.models import (Attachment, Customer, CustomerOrder, CustomerOrderLine, Invoice, InvoiceLine, ItemAlias, MtrLink,
                        PurchaseOrder, PurchaseOrderLine, Shipment, StockItem, Vendor, VendorItem)

ENTITY_MODELS = {"customer_order": CustomerOrder, "purchase_order": PurchaseOrder, "shipment": Shipment}


def _rows(live, sql, *args):
    try:
        return live.execute(sql, args).fetchall()
    except sqlite3.OperationalError:  # an older live database without that table / column
        return []


def _dt(v):
    """Dates come back from raw sqlite as text."""
    if not v or not isinstance(v, str):
        return v
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


DATE_COLS = {"created_at", "last_used_at", "last_ordered_at", "updated_at"}


def _po_line_keys(lines):
    """PO line -> (item code, n-th line of that item on the PO), stable across imports."""
    seen, out = Counter(), {}
    for l in lines:
        seen[l[1]] += 1
        out[l[0]] = (l[1], seen[l[1]])
    return out


def carry_over(db, live_path, rep) -> None:
    live = sqlite3.connect(f"file:{live_path}?mode=ro", uri=True)
    live.row_factory = sqlite3.Row
    S = "carried over from AT-HUB"

    # ---- lookups in the new import, by stable keys ----
    code_to_new = {t: {r.code: r.id for r in db.query(m).all()} for t, m in ENTITY_MODELS.items()}
    item_by_code = {i.code: i.id for i in db.query(StockItem).all()}
    code_by_item = {v: k for k, v in item_by_code.items()}
    cust_by_name = {c.name.strip().lower(): c.id for c in db.query(Customer).all()}
    vend_by_name = {v.name.strip().lower(): v.id for v in db.query(Vendor).all()}
    co_line = {(o.code, l.line_no): l for o in db.query(CustomerOrder).all() for l in o.lines}
    po_line = {}
    for po in db.query(PurchaseOrder).all():
        lines = sorted(po.lines, key=lambda l: (l.position if l.position is not None else 10**6, l.id))
        keys = _po_line_keys([(l.id, code_by_item.get(l.item_id)) for l in lines])
        for l in lines:
            po_line[(po.code,) + keys[l.id]] = l

    # ---- live: id -> stable key ----
    live_code = {t: {r["id"]: r["code"] for r in _rows(live, f"select id, code from {m.__tablename__}")} for t, m in ENTITY_MODELS.items()}
    live_item = {r["id"]: r["code"] for r in _rows(live, "select id, code from stock_items")}
    live_cust = {r["id"]: r["name"].strip().lower() for r in _rows(live, "select id, name from customers")}
    live_vend = {r["id"]: r["name"].strip().lower() for r in _rows(live, "select id, name from vendors")}
    live_po_line = {}
    for po_id, po_code in live_code["purchase_order"].items():
        lines = _rows(live, "select id, item_id from purchase_order_lines where po_id=? order by coalesce(position, 1000000), id", po_id)
        for lid, key in _po_line_keys([(r["id"], live_item.get(r["item_id"])) for r in lines]).items():
            live_po_line[lid] = (po_code,) + key

    # ---- 1. attachments (files stay where they are in uploads/) ----
    have = {(a.entity_type, a.entity_id, a.filename.lower()) for a in db.query(Attachment).all()}
    att_map, lost = {}, Counter()
    for a in _rows(live, "select * from attachments"):
        code = live_code.get(a["entity_type"], {}).get(a["entity_id"])
        new_id = code_to_new.get(a["entity_type"], {}).get(code)
        if not new_id:
            lost[a["entity_type"]] += 1
            continue
        if (a["entity_type"], new_id, a["filename"].lower()) in have:
            continue
        cols = {c.name for c in Attachment.__table__.columns} - {"id", "entity_id"}
        new = Attachment(entity_id=new_id, **{k: (_dt(a[k]) if k in DATE_COLS else a[k]) for k in a.keys() if k in cols})
        db.add(new)
        db.flush()
        att_map[a["id"]] = new.id
        have.add((a["entity_type"], new_id, a["filename"].lower()))
    rep.add("carry-over", f"files: {len(att_map)} {S}" + (f"; {sum(lost.values())} skipped, their record is gone ({dict(lost)})" if lost else ""))

    # ---- 2. MTR coverage (which PO lines an MTR file covers) ----
    n = 0
    for m in _rows(live, "select * from mtr_links"):
        line = po_line.get(live_po_line.get(m["po_line_id"]))
        if m["attachment_id"] in att_map and line:
            db.add(MtrLink(attachment_id=att_map[m["attachment_id"]], po_line_id=line.id, item_id=line.item_id,
                           heat_number=m["heat_number"], created_by=m["created_by"]))
            n += 1
    if n:
        rep.add("carry-over", f"MTR line links: {n} {S}")

    # ---- 3. learned item matches ----
    n = skipped = 0
    for a in _rows(live, "select * from item_aliases"):
        party = (cust_by_name if a["party_type"] == "customer" else vend_by_name).get(
            (live_cust if a["party_type"] == "customer" else live_vend).get(a["party_id"], ""))
        item = item_by_code.get(live_item.get(a["item_id"]))
        if not party or not item:
            skipped += 1
            continue
        db.add(ItemAlias(party_type=a["party_type"], party_id=party, kind=a["kind"], key=a["key"], text=a["text"],
                         item_id=item, hits=a["hits"], last_used_at=_dt(a["last_used_at"]), created_at=_dt(a["created_at"])))
        n += 1
    rep.add("carry-over", f"learned item matches: {n} {S}" + (f"; {skipped} skipped (customer/vendor/item gone)" if skipped else ""))

    # ---- 4. vendor part # links added in AT-HUB (MRPeasy's own come from the import) ----
    have_vi = {(v.vendor_id, v.vendor_item_code.strip().upper()) for v in db.query(VendorItem).all()}
    have_vi_item = {(v.vendor_id, v.item_id) for v in db.query(VendorItem).all()}
    n = 0
    for v in _rows(live, "select * from vendor_items"):
        vid, iid = vend_by_name.get(live_vend.get(v["vendor_id"], "")), item_by_code.get(live_item.get(v["item_id"]))
        if vid and iid and (vid, v["vendor_item_code"].strip().upper()) not in have_vi and (vid, iid) not in have_vi_item:
            db.add(VendorItem(vendor_id=vid, item_id=iid, vendor_item_code=v["vendor_item_code"], vendor_description=v["vendor_description"],
                              last_unit_cost=v["last_unit_cost"], last_ordered_at=_dt(v["last_ordered_at"])))
            have_vi.add((vid, v["vendor_item_code"].strip().upper()))
            n += 1
    if n:
        rep.add("carry-over", f"vendor part # links made in AT-HUB: {n} {S}")

    # ---- 5. line notes ----
    n = 0
    for r in _rows(live, "select o.code, l.line_no, l.notes, l.print_notes from customer_order_lines l join customer_orders o on o.id=l.order_id where l.notes is not null"):
        line = co_line.get((r["code"], r["line_no"]))
        if line and not line.notes:
            line.notes, line.print_notes, n = r["notes"], r["print_notes"] is not False and r["print_notes"] != 0, n + 1
    for r in _rows(live, "select id, notes, print_notes from purchase_order_lines where notes is not null"):
        line = po_line.get(live_po_line.get(r["id"]))
        if line and not line.notes:
            line.notes, line.print_notes, n = r["notes"], r["print_notes"] != 0, n + 1
    inv_lines = defaultdict(list)
    for inv in db.query(Invoice).all():
        for l in sorted(inv.lines, key=lambda l: l.id):
            inv_lines[(inv.code, l.description)].append(l)
    for r in _rows(live, "select i.code, l.description, l.notes, l.print_notes from invoice_lines l join invoices i on i.id=l.invoice_id where l.notes is not null order by l.id"):
        cands = [l for l in inv_lines.get((r["code"], r["description"]), []) if not l.notes]
        if cands:
            cands[0].notes, cands[0].print_notes, n = r["notes"], r["print_notes"] != 0, n + 1
    if n:
        rep.add("carry-over", f"line notes: {n} {S}")

    live.close()
    db.flush()
