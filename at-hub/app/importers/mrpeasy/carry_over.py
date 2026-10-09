"""Carry work done in AT-HUB into a fresh MRPeasy import, so a wipe + re-import loses none of it.

Read-only from the live at_hub.db; written into the import being built. Records are matched by what
stays the same across imports -- order / PO / shipment / invoice codes, item codes, customer and vendor
names, an order line's line #, a PO line's position among lines of the same item -- never by database id.

Carried: attached files (+ which PO lines an MTR covers), learned item matches, line notes (+ "print"),
vendor part # links made in AT-HUB, File Matcher / scan picks, generic-stock flags and links, pack sizes (+ history, presets),
quotes, tasks, roles and Template Designer layouts. (Users and the company profile are copied by load.py.) Files themselves stay in uploads/ untouched;
only their database rows move. Anything whose record no longer exists is reported, not guessed.
"""
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime

from app.models import (Attachment, Customer, CustomerOrder, CustomerOrderLine, Invoice, InvoiceLine, ItemAlias, MtrLink, PackSizePreset,
                        PurchaseOrder, PurchaseOrderLine, PurchaseOrderPayment, Quote, QuoteLine, Shipment, StockItem, Task, Vendor,
                        VendorBill, VendorItem)

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
    co_line_mrp = {l.mrp_id: l for o in db.query(CustomerOrder).all() for l in o.lines if l.mrp_id}
    po_line_mrp = {l.mrp_id: l for po in db.query(PurchaseOrder).all() for l in po.lines if l.mrp_id}
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

    # ---- 4b. generic stock: which items are bulk generic stock, and which specific items draw from which ----
    # (transferred lots themselves don't carry: the import rebuilds stock from MRPeasy)
    new_items = {i.code: i for i in db.query(StockItem).all()}
    flags = links = 0
    for r in _rows(live, "select code, is_generic, parent_item_id from stock_items where is_generic = 1 or parent_item_id is not null"):
        it = new_items.get(r["code"])
        if not it:
            continue
        if r["is_generic"] and not it.is_generic:
            it.is_generic, flags = True, flags + 1
        parent = new_items.get(live_item.get(r["parent_item_id"]))
        if parent and not it.parent_item_id and parent.id != it.id:
            it.parent_item_id, parent.is_generic, links = parent.id, True, links + 1
    if flags or links:
        rep.add("carry-over", f"generic stock: {flags} generic item(s), {links} draw link(s) {S}")

    # ---- 4c. pack sizes set or changed in AT-HUB win over the portal's / MRPeasy's ----
    n = 0
    for r in _rows(live, "select code, default_pack_size from stock_items where default_pack_size is not null"):
        it = new_items.get(r["code"])
        if it and r["default_pack_size"] and it.default_pack_size != r["default_pack_size"]:
            it.default_pack_size, n = r["default_pack_size"], n + 1
    if n:
        rep.add("carry-over", f"pack sizes set in AT-HUB: {n} {S}")

    # ---- 5. line notes ----
    n = 0
    # MRPeasy's own line id first (the # can be renumbered in AT-HUB); order # + line # for lines made in AT-HUB
    for r in _rows(live, "select o.code, l.line_no, l.mrp_id, l.notes, l.print_notes from customer_order_lines l join customer_orders o on o.id=l.order_id where l.notes is not null"):
        line = co_line_mrp.get(r["mrp_id"]) if r["mrp_id"] else co_line.get((r["code"], r["line_no"]))
        if line and not line.notes:
            line.notes, line.print_notes, n = r["notes"], r["print_notes"] is not False and r["print_notes"] != 0, n + 1
    for r in _rows(live, "select id, mrp_id, notes, print_notes from purchase_order_lines where notes is not null"):
        line = po_line_mrp.get(r["mrp_id"]) if r["mrp_id"] else po_line.get(live_po_line.get(r["id"]))
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

    # ---- 5b. PO payments typed into AT-HUB (the export's own are re-applied by the import) ----
    n = 0
    po_by_code = {p.code: p for p in db.query(PurchaseOrder).all()}
    for r in _rows(live, "select p.code, b.bill_number, x.* from purchase_order_payments x join purchase_orders p on p.id=x.po_id "
                         "left join vendor_bills b on b.id=x.vendor_bill_id where coalesce(x.reference,'') != 'mrpeasy-po-export' "
                         "and coalesce(x.created_by,'') != 'mrpeasy-import'"):
        po = po_by_code.get(r["code"])
        if not po:
            continue
        bill = db.query(VendorBill).filter(VendorBill.po_id == po.id, VendorBill.bill_number == r["bill_number"]).first() if r["bill_number"] else None
        db.add(PurchaseOrderPayment(po_id=po.id, amount=r["amount"], currency=r["currency"], paid_date=_dt(r["paid_date"]), method=r["method"],
                                    reference=r["reference"], note=r["note"], vendor_bill_id=bill.id if bill else None,
                                    created_by=r["created_by"], created_at=_dt(r["created_at"])))
        n += 1
    if n:
        rep.add("carry-over", f"PO payments entered in AT-HUB: {n} {S}")

    # ---- 6. quotes (AT-HUB only; a quote converted to an order that no longer exists loses that link) ----
    n = 0
    for q in _rows(live, "select * from quotes"):
        cust = cust_by_name.get(live_cust.get(q["customer_id"], ""))
        if not cust:
            continue
        order_code = live_code["customer_order"].get(q["order_id"])
        new = Quote(code=q["code"], customer_id=cust, status=q["status"], quote_date=_dt(q["quote_date"]), valid_until=_dt(q["valid_until"]),
                    customer_ref=q["customer_ref"], notes=q["notes"], order_id=code_to_new["customer_order"].get(order_code),
                    created_by=q["created_by"], created_at=_dt(q["created_at"]))
        for l in _rows(live, "select * from quote_lines where quote_id=? order by position", q["id"]):
            new.lines.append(QuoteLine(position=l["position"], item_id=item_by_code.get(live_item.get(l["item_id"])), description=l["description"],
                                       quantity=l["quantity"], unit_price=l["unit_price"], notes=l["notes"], source_text=l["source_text"]))
        db.add(new)
        n += 1
    if n:
        rep.add("carry-over", f"quotes: {n} {S}")

    # ---- 7. tasks (manual ones, and what was done / dismissed on suggested ones) ----
    n = 0
    for t in _rows(live, "select * from tasks"):
        if t["key"] and t["status"] == "open":
            continue  # suggested + still open: raised again from the new data
        db.add(Task(key=t["key"], category=t["category"], title=t["title"], detail=t["detail"], link=t["link"], status=t["status"],
                    note=t["note"], created_by=t["created_by"], created_at=_dt(t["created_at"]), done_by=t["done_by"], done_at=_dt(t["done_at"])))
        n += 1
    if n:
        rep.add("carry-over", f"tasks: {n} {S}")

    # ---- 8. roles (custom ones, and any changes to the built-in ones) ----
    from app.models import DocTemplate, PackSizeHistory, Role
    n = 0
    for r in _rows(live, "select * from roles"):
        db.merge(Role(key=r["key"], name=r["name"], description=r["description"], permissions=r["permissions"], builtin=bool(r["builtin"]),
                      updated_by=r["updated_by"], updated_at=_dt(r["updated_at"])))
        n += 1
    if n:
        rep.add("carry-over", f"roles: {n} {S}")

    # ---- 9. Template Designer layouts (a customer's own default follows the customer by name) ----
    n = 0
    for t in _rows(live, "select * from doc_templates"):
        cust = cust_by_name.get(live_cust.get(t["customer_id"], "")) if t["customer_id"] else None
        if t["customer_id"] and not cust:
            rep.add("carry-over", f"template {t['name']}: its customer is gone -- kept, not tied to a customer")
        db.add(DocTemplate(**{k: t[k] for k in t.keys() if k not in ("id", "customer_id", "created_at", "updated_at")},
                           customer_id=cust, created_at=_dt(t["created_at"]), updated_at=_dt(t["updated_at"])))
        n += 1
    if n:
        rep.add("carry-over", f"designer templates: {n} {S}")

    # ---- 10. pack size history (by item code) ----
    n = 0
    for h in _rows(live, "select * from pack_size_history"):
        item = item_by_code.get(live_item.get(h["item_id"]))
        if item:
            db.add(PackSizeHistory(item_id=item, pack_size=h["pack_size"], previous_pack_size=h["previous_pack_size"], source=h["source"],
                                   reference=h["reference"], changed_by=h["changed_by"], changed_at=_dt(h["changed_at"])))
            n += 1
    if n:
        rep.add("carry-over", f"pack size history: {n} {S}")

    # ---- 11. pack size presets (item codes inside; a customer's preset follows the customer by name) ----
    n = 0
    for r in _rows(live, "select * from pack_size_presets"):
        db.add(PackSizePreset(name=r["name"], customer_id=cust_by_name.get(live_cust.get(r["customer_id"])), sizes=r["sizes"],
                              created_by=r["created_by"], created_at=_dt(r["created_at"]), updated_at=_dt(r["updated_at"])))
        n += 1
    if n:
        rep.add("carry-over", f"pack size presets: {n} {S}")

    live.close()
    db.flush()
