"""What a document template can show: for each document type, the values its {{fields}} resolve to and the
rows of its line-items table -- built from a real record (or from a label's own data).

    build(db, doc_type, record, options) -> (context: {"company": {...}, "order": {...}, ...}, rows: [{...}])
    FIELDS[doc_type] -> [(key, label)]   the field picker in the designer
    COLUMNS[doc_type] -> [(key, label)]  the table columns it can have
Every value is already a display string ("$1,234.50", "Sep 25, 2026")."""
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.models import Customer, CustomerOrder, Invoice, Lot, PurchaseOrder, Shipment, ShipmentLine, StockItem, Vendor
from app.services.crud import get_company_profile
from app.services.money import line_amount

DOC_TYPES = {
    "invoice": "Invoice", "packing_list": "Packing list", "purchase_order": "Purchase order", "quote": "Quote",
    "box_label": "Box label", "address_label": "Address label", "pallet_label": "Shipment pallet label",
}
LABEL_TYPES = {"box_label", "address_label", "pallet_label"}


# ---------- formatting ----------
def money(v):
    return f"${v:,.2f}" if v is not None else ""


def price(v):
    if v is None:
        return ""
    whole, frac = f"{v:,.4f}".split(".")
    return f"${whole}.{frac.rstrip('0').ljust(2, '0')}"


def qty(v):
    if v is None or v == "":
        return ""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{v:,.0f}" if abs(v - round(v)) < 1e-9 else f"{v:,.2f}"


def date(d):
    return d.strftime("%b %d, %Y") if d else ""

def moment(d) -> str:
    """The day of a stored moment (UTC), in the company's time zone -- documents are read where the company is."""
    from app.services.clock import local
    return date(local(d)) if d else ""


def lines(*parts):
    return "\n".join(p.strip() for p in parts if p and str(p).strip())


def addr(text):
    return "\n".join(l.strip() for l in (text or "").splitlines() if l.strip())


# ---------- the field picker ----------
_COMPANY = [("company.name", "Our company name"), ("company.address", "Our address"), ("company.address_line", "Our address on one line"), ("company.phone", "Our phone"),
            ("company.email", "Our email"), ("company.website", "Our website"), ("company.tax_id", "Our tax ID"),
            ("company.contact_line", "Our phone · email · website"), ("company.invoice_notes", "Invoice notes / payment terms")]
_DOC = [("doc.title", "Document title (INVOICE)"), ("doc.name", "Document name (Invoice)"), ("doc.number", "Document number"),
        ("doc.date", "Document date"), ("page", "Page number"), ("pages", "Number of pages")]
_CUST = [("customer.name", "Customer name"), ("customer.contact", "Customer contact"), ("customer.phone", "Customer phone"),
         ("customer.email", "Customer email"), ("customer.bill_to", "Bill-to address"), ("customer.ship_to", "Ship-to address")]
_ORDER = [("order.code", "Order #"), ("order.po_number", "Customer PO #"), ("order.job_number", "Job #"),
          ("order.po_date", "Customer PO date"), ("order.delivery_date", "Order delivery date")]
FIELDS = {
    "invoice": _COMPANY + _DOC + _CUST + _ORDER + [
        ("invoice.date", "Invoice date"), ("invoice.due_date", "Due date"), ("invoice.terms", "Terms"),
        ("invoice.shipments", "Shipment #(s)"), ("invoice.shipped", "Ship date"), ("invoice.delivered", "Delivered date"),
        ("invoice.notes", "Invoice notes (free text)"), ("totals.subtotal", "Subtotal"), ("totals.items_subtotal", "Subtotal before shipping"),
        ("totals.shipping", "Shipping"), ("totals.tax", "Tax"),
        ("totals.total", "Total / amount due"), ("totals.lines", "Number of lines")],
    "packing_list": _COMPANY + _DOC + _CUST + _ORDER + [
        ("shipment.code", "Shipment #"), ("shipment.ship_date", "Ship date"), ("shipment.carrier", "Carrier"),
        ("shipment.tracking", "Tracking #"), ("shipment.notes", "Shipment notes"), ("shipment.lines", "Number of lines"),
        ("shipment.units", "Units shipped"), ("shipment.boxes", "Number of boxes"), ("shipment.pallets", "Number of pallets"),
        ("shipment.weight", "Total weight"), ("shipment.pod_url", "Driver POD link (for a QR code)")],
    "purchase_order": _COMPANY + _DOC + [
        ("vendor.name", "Vendor name"), ("vendor.contact", "Vendor contact"), ("vendor.address", "Vendor address"),
        ("vendor.phone", "Vendor phone"), ("vendor.email", "Vendor email"), ("po.date", "PO date"), ("po.expected", "Required by"),
        ("po.vendor_so", "Vendor SO #"), ("po.buyer", "Buyer"), ("po.notes", "PO notes"), ("po.ship_to", "Ship-to (our address)"),
        ("totals.subtotal", "Subtotal"), ("totals.charges", "Freight / charges"), ("totals.total", "PO total"), ("totals.lines", "Number of lines")],
    "quote": _COMPANY + _DOC + _CUST + [
        ("quote.date", "Quote date"), ("quote.valid_until", "Valid until"), ("quote.ref", "Their reference / RFQ #"),
        ("quote.notes", "Quote notes"), ("totals.total", "Quote total"), ("totals.lines", "Number of lines")],
    "box_label": [("company.name", "Our company name"), ("company.contact_line", "Our phone · email · website"),
                  ("label.customer", "Customer"), ("label.shipment", "Shipment #"), ("label.order", "Order #"), ("label.po", "Customer PO #"),
                  ("label.job", "Job #"), ("label.item_code", "Item #"), ("label.item_title", "Item description"), ("label.qty", "Quantity in box"),
                  ("label.box", "Box # (e.g. 3)"), ("label.boxes", "Box count (e.g. 12)"), ("label.lot", "Lot #"), ("label.pallet", "Pallet #"),
                  ("label.ship_to", "Ship-to address"), ("label.footer", "Footer text")],
    "address_label": [("company.name", "Our company name"), ("label.from", "From address"), ("label.to", "Ship-to address"),
                      ("label.attn", "Attention"), ("label.ref", "Reference"), ("label.note", "Note")],
    "pallet_label": [("company.name", "Our company name"), ("company.contact_line", "Our phone · email · website"),
                     ("label.po", "Customer PO #"), ("label.job", "Job #"), ("label.customer", "Customer"), ("label.ship_to", "Ship-to address"),
                     ("label.shipment", "Shipment #"), ("label.order", "Order #"), ("label.ship_date", "Ship date"), ("label.carrier", "Carrier"),
                     ("label.pallet", "This label's pallet # (one label per pallet)"), ("label.pallet_of", "Pallet 3 of 5"),
                     ("label.badge_caption", "Badge caption (Pallet / Shipment)"), ("label.badge", "Badge (3 of 5 / 5 pallets)"),
                     ("label.totals", "Totals line (pallets · boxes · weight, as ticked when printing)"),
                     ("label.footer_info", "Company name · ship date (as ticked when printing)"),
                     ("label.pallets", "Number of pallets"), ("label.boxes", "Number of boxes"), ("label.weight", "Total weight"),
                     ("label.sheet", "Sheet 1 of 2 (when the list runs over)")],
}
COLUMNS = {
    "invoice": [("line_no", "#"), ("item_code", "Item #"), ("description", "Description"), ("item_code_desc", "Item # + description"),
                ("shipment", "Shipment"), ("delivery", "Delivery date"), ("qty", "Qty"), ("price", "Unit price"), ("amount", "Amount")],
    "packing_list": [("line_no", "Line"), ("item_code", "Item #"), ("description", "Description"), ("item_code_desc", "Item # + description"),
                     ("lot", "Lot #"), ("previous", "Previously shipped"), ("ordered", "Ordered"), ("shipped", "Shipped"),
                     ("backorder", "Backorder"), ("boxes", "Boxes"), ("pallet", "Pallet #"), ("check", "Check box")],
    "pallets": [("pallet", "Pallet #"), ("items", "Items on it"), ("weight", "Weight (lbs)"), ("dimensions", "Dimensions"), ("po", "Customer PO #")],
    "purchase_order": [("line_no", "#"), ("item_code", "Part # (vendor's)"), ("our_code", "Our item #"), ("description", "Description"),
                       ("item_code_desc", "Part # + description"), ("qty", "Qty"), ("price", "Unit cost"), ("amount", "Amount")],
    "quote": [("line_no", "#"), ("item_code", "Item #"), ("description", "Description"), ("item_code_desc", "Item # + description"),
              ("qty", "Qty"), ("price", "Unit price"), ("amount", "Amount")],
}


def _company(db):
    c = get_company_profile(db)
    return c, {"name": c.name or "", "address": addr(c.address), "address_line": ", ".join(addr(c.address).splitlines()),
               "phone": c.phone or "", "email": c.email or "",
               "website": (c.website or "").replace("https://", "").replace("http://", ""), "tax_id": c.tax_id or "",
               "contact_line": " · ".join(x for x in [c.phone, c.email, (c.website or "").replace("https://", "")] if x),
               "invoice_notes": c.invoice_notes or ""}


def _customer(cust, ship_to=None):
    if not cust:
        return {}
    return {"name": cust.name or "", "contact": cust.contact_name or "", "phone": cust.phone or "", "email": cust.email or "",
            "bill_to": addr(cust.address), "ship_to": addr(ship_to or cust.shipping_address or cust.address)}


def _order(o):
    if not o:
        return {}
    return {"code": o.code, "po_number": o.po_number or "", "job_number": o.job_number or "",
            "po_date": date(o.customer_po_date), "delivery_date": date(o.delivery_date)}


def _desc(text, line, show_notes):
    note = (getattr(line, "notes", None) or "").strip()
    if show_notes and note and getattr(line, "print_notes", True) is not False:
        return f"{text}\n{note}" if text else note
    return text or ""


# ---------- per document ----------
def build(db: Session, doc_type: str, record, options: Optional[dict] = None):
    options = options or {}
    company_row, company = _company(db)
    fn = {"invoice": _invoice, "packing_list": _packing_list, "purchase_order": _purchase_order, "quote": _quote}[doc_type]
    ctx, rows = fn(db, record, options)
    ctx["company"] = company
    ctx["_logo"] = company_row.logo_data
    ctx["doc"]["name"] = ctx["doc"]["title"].title()  # "Packing List" (footers)
    return ctx, rows


def _natural(text):
    import re
    return [(0, int(t), "") if t.isdigit() else (1, 0, t.lower()) for t in re.split(r"(\d+)", str(text or "")) if t]


def _is_shipping(line, item=None):
    """A freight line: AT-HUB's own (no item, "Shipping") or MRPeasy's "Shipping" item."""
    code = (item.code if item else "").strip().lower()
    return code == "shipping" or (line.item_id is None and (line.description or "").strip().lower().startswith("shipping"))


def _invoice(db, inv: Invoice, opt):
    cust = db.get(Customer, inv.customer_id)
    order = db.get(CustomerOrder, inv.order_id) if inv.order_id else None
    ships = list(inv.shipments) or ([db.get(Shipment, inv.shipment_id)] if inv.shipment_id else [])
    ships = [s for s in ships if s]
    items = {i.id: i for i in db.query(StockItem).filter(StockItem.id.in_({l.item_id for l in inv.lines if l.item_id})).all()}
    show_notes = opt.get("show_notes", True)
    printed = [l for l in inv.lines if inv.print_zero_lines or abs(line_amount(l.quantity, l.unit_price)) >= 0.005]
    by_id = {s.id: s for s in ships}
    rows = []
    for i, l in enumerate(printed, 1):
        it = items.get(l.item_id)
        desc = l.description or (it.title if it else "")
        sh = by_id.get(l.shipment_id)
        rows.append({"line_no": str(i), "item_code": it.code if it else "", "description": _desc(desc, l, show_notes),
                     "shipment": sh.code if sh else "", "qty": qty(l.quantity), "price": price(l.unit_price),
                     "delivery": moment(sh.delivered_at) if sh and sh.delivered_at else date(order.delivery_date if order else None),
                     "amount": money(line_amount(l.quantity, l.unit_price)), "_shipping": _is_shipping(l, it)})
    total = sum(line_amount(l.quantity, l.unit_price) for l in printed)
    shipping = sum(line_amount(l.quantity, l.unit_price) for l in printed if _is_shipping(l, items.get(l.item_id)))
    due = inv.due_date or (inv.invoice_date + timedelta(days=30) if inv.invoice_date else None)
    order_ship_to = order.ship_to_address if order else None
    ctx = {"doc": {"title": "INVOICE", "number": inv.code, "date": date(inv.invoice_date), "status": inv.status},
           "customer": _customer(cust, order_ship_to), "order": _order(order),
           "invoice": {"date": date(inv.invoice_date), "due_date": date(due), "terms": "Net 30",
                       "shipments": ", ".join(s.code for s in ships), "shipped": moment(ships[0].ship_date) if ships else "",
                       "delivered": moment(ships[0].delivered_at) if ships else "",
                       "notes": inv.free_text if inv.free_text and inv.free_text != "Generated via AT-HUB" else ""},
           "totals": {"subtotal": money(total), "items_subtotal": money(total - shipping), "shipping": money(shipping),
                      "tax": money(0), "total": money(total), "lines": str(len(rows))},
           "_watermark": "VOID" if inv.status == "void" else None}
    return ctx, rows


def _packing_list(db, sh: Shipment, opt):
    order = db.get(CustomerOrder, sh.order_id)
    cust = db.get(Customer, order.customer_id) if order else None
    items = {i.id: i for i in db.query(StockItem).filter(StockItem.id.in_({l.item_id for l in sh.lines})).all()}
    lots = {l.id: l for l in db.query(Lot).filter(Lot.id.in_({l.lot_id for l in sh.lines if l.lot_id})).all()}
    show_notes = opt.get("show_notes", True)
    by_line, ols, lots_by_line = {}, {}, {}
    for l in sh.lines:
        by_line[l.order_line_id] = by_line.get(l.order_line_id, 0) + l.quantity
        ols[l.order_line_id] = l.order_line
        if l.lot_id in lots:
            lots_by_line.setdefault(l.order_line_id, []).append(lots[l.lot_id].lot_code)
    previous = {}
    earlier = (db.query(ShipmentLine, Shipment).join(Shipment, Shipment.id == ShipmentLine.shipment_id)
               .filter(ShipmentLine.order_line_id.in_(list(by_line)), Shipment.id != sh.id,
                       Shipment.status.in_(("shipped", "delivered", "invoiced"))).order_by(Shipment.ship_date, Shipment.id).all())
    for sl, s in earlier:
        e = previous.setdefault(sl.order_line_id, {}).setdefault(s.id, {"code": s.code, "qty": 0, "when": s.delivered_at or s.ship_date})
        e["qty"] += sl.quantity
    from app.services.nut_pairing import line_order, pallets_by_line
    codes = {i: it.code for i, it in items.items()}
    eff_pallets = pallets_by_line(sh, codes)
    rows = []
    for lid in [ol.id for ol in line_order(list(ols.values()), lambda ol: codes.get(ol.item_id, ""))]:
        ol = ols[lid]
        it = items.get(ol.item_id)
        counts = {}
        for b in sh.boxes:
            if b.order_line_id == lid:
                counts[b.quantity_in_box] = counts.get(b.quantity_in_box, 0) + 1
        back = max(0, ol.quantity - ol.shipped_quantity - ol.booked_quantity)
        rows.append({"line_no": str(ol.line_no or ""), "item_code": it.code if it else "", "description": _desc(it.title if it else "", ol, show_notes),
                     "lot": ", ".join(dict.fromkeys(lots_by_line.get(lid, []))), "ordered": qty(ol.quantity), "shipped": qty(by_line[lid]),
                     "backorder": qty(back) if back else "—",
                     "previous": "\n".join(f"{e['code']}: {qty(e['qty'])}" + (f" · {moment(e['when'])}" if e["when"] else "") for e in previous.get(lid, {}).values()),
                     "boxes": "\n".join(f"{n} × {qty(q)}" for q, n in sorted(counts.items(), reverse=True)) or "—",
                     "pallet": ", ".join(eff_pallets.get(lid, [])),
                     "check": ""})
    pallets = sorted({p for ps in eff_pallets.values() for p in ps}, key=_natural)  # 1, 2, 10 -- not 1, 10, 2
    saved = {p.pallet_number: p for p in sh.pallets}
    on_pallet = {}
    for lid, ps in eff_pallets.items():
        for pn in ps:
            on_pallet.setdefault(pn, []).append(codes.get(ols[lid].item_id, "") if lid in ols else "")
    pallet_rows = [{"pallet": pn, "items": ", ".join(dict.fromkeys(c for c in on_pallet.get(pn, []) if c)),
                    "weight": qty(saved[pn].weight) if pn in saved and saved[pn].weight is not None else "",
                    "dimensions": (saved[pn].dimensions or "") if pn in saved else "", "po": (order.po_number or "") if order else ""}
                   for pn in pallets]
    weight = sum(p.weight or 0 for p in sh.pallets)
    ship_to = (order.ship_to_address if order else None)
    from app.services.concurrency import REQUEST_BASE
    base = (settings.public_url or REQUEST_BASE.get() or "").rstrip("/")  # PUBLIC_URL once launched; else the address in use
    ctx = {"doc": {"title": "PACKING LIST", "number": sh.code, "date": moment(sh.ship_date or sh.created_at)},
           "customer": _customer(cust, ship_to), "order": _order(order),
           "shipment": {"code": sh.code, "ship_date": moment(sh.ship_date), "carrier": sh.carrier or "", "tracking": sh.tracking_number or "",
                        "notes": sh.notes or "", "lines": str(len(rows)), "units": qty(sum(by_line.values())), "boxes": str(len(sh.boxes) or ""),
                        "pallets": str(len(pallets)) if pallets else "", "weight": f"{weight:,.0f} lbs" if weight else "",
                        "pod_url": f"{base}/pod.html?id={sh.id}" if base else f"/pod.html?id={sh.id}"},
           "_pallet_rows": pallet_rows}
    return ctx, rows


def _purchase_order(db, po: PurchaseOrder, opt):
    vend = db.get(Vendor, po.vendor_id)
    items = {i.id: i for i in db.query(StockItem).filter(StockItem.id.in_({l.item_id for l in po.lines})).all()}
    for_vendor = opt.get("for_vendor", True)
    show_notes = opt.get("show_notes", True)
    rows = []
    for i, l in enumerate(po.lines, 1):
        it = items.get(l.item_id)
        code = (l.vendor_item_code or (it.code if it else "")) if for_vendor else (it.code if it else "")  # no vendor part #: ours
        desc = (l.vendor_description or (it.title if it else "")) if for_vendor else (it.title if it else "")
        rows.append({"line_no": str(i), "item_code": code, "our_code": it.code if it else "",
                     "description": _desc(desc, l, show_notes), "qty": qty(l.quantity), "price": price(l.unit_cost),
                     "amount": money(line_amount(l.quantity, l.unit_cost))})
    sub = sum(line_amount(l.quantity, l.unit_cost) for l in po.lines)
    charges = sum(c.amount or 0 for c in (po.charges or []))
    company = get_company_profile(db)
    ctx = {"doc": {"title": "PURCHASE ORDER", "number": po.code, "date": date(po.order_date or po.created_at)},
           "vendor": {"name": vend.name if vend else "", "contact": vend.contact_name if vend else "", "address": addr(vend.address) if vend else "",
                      "phone": vend.phone if vend else "", "email": (vend.po_email or vend.email) if vend else ""},
           "po": {"date": date(po.order_date or po.created_at), "expected": date(po.expected_date), "vendor_so": po.vendor_so_number or "",
                  "buyer": po.created_by if po.created_by not in (None, "mrpeasy-import") else "", "notes": po.notes or "",
                  "ship_to": lines(company.name, addr(company.address), company.phone)},
           "totals": {"subtotal": money(sub), "charges": money(charges), "total": money(sub + charges), "lines": str(len(rows))},
           "_watermark": "CANCELLED" if po.status == "cancelled" else None}
    return ctx, rows


def _quote(db, q, opt):
    cust = db.get(Customer, q.customer_id)
    items = {i.id: i for i in db.query(StockItem).filter(StockItem.id.in_({l.item_id for l in q.lines if l.item_id})).all()}
    rows = []
    for i, l in enumerate(q.lines, 1):
        it = items.get(l.item_id)
        rows.append({"line_no": str(i), "item_code": it.code if it else "", "description": _desc(l.description or (it.title if it else ""), l, True),
                     "qty": qty(l.quantity), "price": price(l.unit_price), "amount": money(line_amount(l.quantity, l.unit_price))})
    total = sum(line_amount(l.quantity, l.unit_price) for l in q.lines)
    ctx = {"doc": {"title": "QUOTATION", "number": q.code, "date": date(q.quote_date)}, "customer": _customer(cust),
           "quote": {"date": date(q.quote_date), "valid_until": date(q.valid_until), "ref": q.customer_ref or "", "notes": q.notes or ""},
           "totals": {"total": money(total), "lines": str(len(rows))}}
    return ctx, rows


def pallet_label_contexts(db, sh: Shipment, per_pallet: bool = True, totals=("pallets", "boxes", "weight"),
                          footer=("company", "ship_date")) -> list:
    """A shipment's pallet labels: every pallet and the customer item #s on it, with PO # and job #. per_pallet: one
    label per pallet with its own row highlighted ("PALLET 3 OF 5"); else one summary label. The item list follows
    the packing list's rules (a nut rides on its bolt's pallet)."""
    from app.services.nut_pairing import line_order, pallets_by_line
    from app.services.clock import local
    company_row, company = _company(db)
    order = db.get(CustomerOrder, sh.order_id)
    cust = db.get(Customer, order.customer_id) if order else None
    items = {i.id: i for i in db.query(StockItem).filter(StockItem.id.in_({l.item_id for l in sh.lines} or {0})).all()}
    codes = {i: it.code for i, it in items.items()}
    eff = pallets_by_line(sh, codes)
    ols = {l.order_line_id: l.order_line for l in sh.lines if l.order_line}
    ordered_ids = [ol.id for ol in line_order(list(ols.values()), lambda ol: codes.get(ol.item_id, ""))]
    on_pallet, boxes_on = {}, {}
    for lid in ordered_ids:
        for pn in eff.get(lid, []):
            on_pallet.setdefault(pn, []).append(codes.get(ols[lid].item_id, ""))
    for b in sh.boxes:
        if b.pallet_number:
            boxes_on[b.pallet_number] = boxes_on.get(b.pallet_number, 0) + 1
    saved = {p.pallet_number: p for p in sh.pallets}
    names = sorted(on_pallet, key=_natural)
    rows = [{"pallet": pn, "items": ", ".join(dict.fromkeys(c for c in on_pallet[pn] if c)), "boxes": str(boxes_on.get(pn, "") or ""),
             "weight": qty(saved[pn].weight) if pn in saved and saved[pn].weight else "",
             "dimensions": (saved[pn].dimensions or "") if pn in saved else ""} for pn in names]
    weight = sum(p.weight or 0 for p in sh.pallets)
    base = {"po": (order.po_number or "") if order else "", "job": (order.job_number or "") if order else "",
            "customer": cust.name if cust else "", "ship_to": addr((order.ship_to_address if order else None) or (cust.shipping_address if cust else None)),
            "shipment": sh.code, "order": order.code if order else "", "ship_date": date(local(sh.ship_date)) if sh.ship_date else date(local(sh.created_at)),
            "carrier": sh.carrier or "", "pallets": str(len(rows)), "boxes": str(len(sh.boxes)), "weight": f"{weight:,.0f} lbs" if weight else "",
            "pallet": "", "pallet_of": "", "sheet": "", "continued": "",
            "badge_caption": "Shipment", "badge": f"{len(rows)} pallet{'' if len(rows) == 1 else 's'}"}
    # the footer's totals line, with only the parts asked for at print time
    parts = {"pallets": f"{len(rows)} pallet{'' if len(rows) == 1 else 's'}", "boxes": f"{len(sh.boxes)} box{'' if len(sh.boxes) == 1 else 'es'}",
             "weight": base["weight"]}
    base["totals"] = " · ".join(parts[k] for k in ("pallets", "boxes", "weight") if k in totals and parts[k])
    info = {"company": company.get("name", ""), "ship_date": f"shipped {base['ship_date']}" if base["ship_date"] else ""}
    base["footer_info"] = " · ".join(info[k] for k in ("company", "ship_date") if k in footer and info[k])
    common = {"company": company, "_logo": company_row.logo_data, "doc": {"title": "PALLET LABEL", "number": sh.code}}
    if not per_pallet or not rows:
        return [{**common, "label": base, "pallet_rows": rows}]
    return [{**common, "label": {**base, "pallet": r["pallet"], "pallet_of": f"{i} of {len(rows)}", "badge_caption": "Pallet",
                                 "badge": f"{r['pallet']} of {len(rows)}"},
             "pallet_rows": [{**x, "_hi": x["pallet"] == r["pallet"]} for x in rows]} for i, r in enumerate(rows, 1)]


SAMPLE_PALLETS = [("1", "15422, 15422-NUTS, 16642, 16713, 16718, 58268, 58268-NUTS", "9"), ("2", "15420, 15420-NUTS, 16716, 33797, 33806", "8"),
                  ("3", "15437, 15437-NUTS, 15439, 15439-NUTS, 77183-HPC, 77183-HPC-NUTS", "11"), ("4", "15421, 15421-NUTS, 41575, 41575-NUT", "6"),
                  ("5", "41574, 41574-NUTS", "3")]


def label_context(db, doc_type: str, label: dict) -> dict:
    """A label's own data (as the label screens build it) -> context."""
    company_row, company = _company(db)
    l = {k: ("" if v is None else str(v)) for k, v in (label or {}).items()}
    if doc_type == "pallet_label":
        rows = [{"pallet": p, "items": i, "boxes": n, "weight": "", "dimensions": "", "_hi": p == l.get("pallet")} for p, i, n in SAMPLE_PALLETS]
        return {"company": company, "label": l, "_logo": company_row.logo_data, "doc": {"title": "PALLET LABEL", "number": l.get("shipment", "")},
                "pallet_rows": (label or {}).get("pallet_rows") or rows}
    if doc_type == "box_label":
        if l.get("qty"):
            l["qty"] = qty(l["qty"])
        l["po"] = l.get("po", "").replace("PO #", "").replace("PO#", "").strip()
        l.setdefault("footer", company["contact_line"])
    return {"company": company, "label": l, "_logo": company_row.logo_data, "doc": {"title": DOC_TYPES[doc_type].upper(), "number": l.get("shipment", "")}}


SAMPLE_LABEL = {"box_label": {"customer": "Hudson Products", "shipment": "SH215757", "order": "C89122", "po": "4179869", "job": "M219-30B",
                              "item_code": "56014-HPC", "item_title": "BOLT_HH_7/8-9x3-1/4_A325_TYPE1_HDG", "qty": 225, "box": "3", "boxes": "10",
                              "lot": "L00512", "pallet": "PLT-1", "ship_to": "HUDSON PRODUCTS CORPORATION\n9660 GRUNWALD ROAD\nBEASLEY TEXAS 77417"},
                "address_label": {"from": "American Traders LLC\n1217 Business 71\nColumbus, TX", "to": "HUDSON PRODUCTS CORPORATION\n9660 GRUNWALD ROAD\nBEASLEY TEXAS 77417",
                                  "attn": "Receiving", "ref": "PO 4179869", "note": "Fragile"},
                "pallet_label": {"po": "4156932", "job": "M219-30C", "customer": "Hudson Products", "shipment": "SH215741-M219-30C", "order": "C89117",
                                 "ship_date": "Oct 06, 2026", "carrier": "Customer pickup", "pallet": "3", "pallet_of": "3 of 5", "pallets": "5", "boxes": "37",
                                 "weight": "4,820 lbs", "sheet": "", "continued": "", "badge_caption": "Pallet", "badge": "3 of 5",
                                 "totals": "5 pallets · 37 boxes · 4,820 lbs", "footer_info": "American Traders LLC · shipped Oct 06, 2026",
                                 "ship_to": "HUDSON PRODUCTS CORPORATION\n9660 GRUNWALD ROAD\nBEASLEY TEXAS 77417"}}
