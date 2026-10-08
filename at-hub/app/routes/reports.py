"""Reports: the 'action items' list -- things that are stuck or need someone to follow up.
Shipping items are shown to everyone; purchasing and money items to managers and up."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import get_current_active_user
from app.services.permissions import has
from app.services.money import cents, line_amount
from app.models import (Attachment, Customer, CustomerOrder, Invoice, InvoiceShipment, MtrLink, PurchaseOrder, Shipment,
                        StockItem, User, Vendor, VendorBill, VendorPayment)

router = APIRouter(prefix="/api/reports", tags=["reports"])

SHIPPED = ("shipped", "invoiced", "delivered")


def _days(since):
    return (datetime.utcnow() - since).days if since else None


@router.get("/action-items")
def action_items(db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """Needs Attention: without rows dismissed or dated before the go-live cut-off (MRP Migrate > Go-Live Cleanup)."""
    from app.services import golive
    return golive.apply(db, all_sections(db, user))


def all_sections(db: Session, user: User) -> list:
    """Every action item, each row with its _rid (record id within the section), _date (what it's dated by, for the
    go-live cut-off) and _label (for the dismissed list)."""
    now = datetime.utcnow()
    orders = {o.id: o for o in db.query(CustomerOrder).all()}
    customers = {c.id: c.name for c in db.query(Customer).all()}
    vendors = {v.id: v.name for v in db.query(Vendor).all()}

    def ship_row(s, extra=None):
        o = orders.get(s.order_id)
        row = {"id": s.id, "code": s.code, "order_id": s.order_id, "order_code": o.code if o else None,
               "customer": customers.get(o.customer_id) if o else None, "po_number": o.po_number if o else None,
               "carrier": s.carrier, "tracking_number": s.tracking_number,
               "ship_date": s.ship_date.isoformat() if s.ship_date else None, "days": _days(s.ship_date),
               "_date": s.ship_date or s.created_at, "_label": s.code}
        row.update(extra or {})
        return row

    shipments = db.query(Shipment).filter(Shipment.status != "cancelled").all()
    shipped = [s for s in shipments if s.status in SHIPPED or s.ship_date]
    pod_ids = {a.entity_id for a in db.query(Attachment.entity_id).filter(Attachment.entity_type == "shipment", Attachment.category == "pod")}

    sections = [
        {"key": "not_delivered", "title": "Shipped But Not Marked Delivered", "page": "shipments.html",
         "help": "Shipped more than 2 days ago with no delivery recorded. Upload the POD or mark it delivered.",
         "rows": sorted([ship_row(s) for s in shipped if not s.delivered_at and (_days(s.ship_date) or 0) >= 2], key=lambda r: -(r["days"] or 0))},
        {"key": "missing_pod", "title": "Missing Proof Of Delivery", "page": "pod.html",
         "help": "Shipped with no POD file attached.",
         "rows": sorted([ship_row(s, {"delivered": bool(s.delivered_at)}) for s in shipped if s.id not in pod_ids], key=lambda r: -(r["days"] or 0))},
        {"key": "not_shipped", "title": "Shipments Created But Not Shipped (3+ Days)", "page": "shipments.html",
         "help": "Booked or ready shipments sitting for 3 days or more.",
         "rows": [ship_row(s, {"status": s.status, "days": _days(s.created_at)}) for s in shipments
                  if s.status in ("new", "ready") and (_days(s.created_at) or 0) >= 3]},
    ]
    late_orders = []
    for o in orders.values():
        if o.status in ("confirmed", "draft") and o.delivery_date and o.delivery_date < now:
            late_orders.append({"id": o.id, "order_code": o.code, "customer": customers.get(o.customer_id), "po_number": o.po_number,
                                "status": o.status, "due": o.delivery_date.isoformat(), "days": _days(o.delivery_date),
                                "_date": o.created_at, "_label": o.code})
    sections.append({"key": "late_orders", "title": "Customer Orders Past Delivery Date", "page": "customer-orders.html",
                     "help": "Not fully shipped and the requested delivery date has passed.", "rows": sorted(late_orders, key=lambda r: -r["days"])})

    if has(user, "purchasing"):
        pos = db.query(PurchaseOrder).filter(PurchaseOrder.status.in_(("draft", "ordered", "partially_received"))).all()
        # services / do-not-sell POs (freight, coating, rent...) are never "received": not chased for receiving
        from app.models import ProductGroup
        non_stock = {g.name for g in db.query(ProductGroup).filter(ProductGroup.non_stock.is_(True)).all()}
        cats = dict(db.query(StockItem.id, StockItem.category).all())
        pos = [po for po in pos if not (po.lines and all(cats.get(l.item_id) in non_stock for l in po.lines))]

        def po_row(po, extra=None):
            ordered = sum(l.quantity for l in po.lines)
            received = sum(l.received_quantity for l in po.lines)
            row = {"id": po.id, "code": po.code, "vendor": vendors.get(po.vendor_id), "status": po.status,
                   "expected": po.expected_date.isoformat() if po.expected_date else None,
                   "ordered_qty": ordered, "received_qty": received, "_date": po.order_date or po.created_at, "_label": po.code}
            row.update(extra or {})
            return row
        # A vendor invoice usually means the goods shipped; if they're still not received, chase it.
        billed_not_received = [po_row(po, {"billed": sum(b.amount for b in po.bills), "bills": ", ".join(b.bill_number for b in po.bills),
                                           "days": _days(min(b.bill_date or b.created_at for b in po.bills))})
                               for po in pos if po.bills]
        overdue_pos = [po_row(po, {"days": _days(po.expected_date)}) for po in pos if po.status != "draft" and po.expected_date and po.expected_date < now]
        captured = db.query(PurchaseOrder).filter(PurchaseOrder.status == "validation").all()
        sections += [
            {"key": "captured_pos", "title": "Captured POs To Validate", "page": "purchase-orders.html",
             "help": "Quick-captured vendor documents: check them, fill in the lines and validate.",
             "rows": [{"id": po.id, "code": po.code, "vendor": vendors.get(po.vendor_id), "ref": po.vendor_so_number, "by": po.created_by,
                       "days": _days(po.created_at), "_date": po.created_at, "_label": po.code} for po in captured]},
            {"key": "vendor_shipped", "title": "Shipped By Vendor (Invoiced) But Not Received", "page": "purchase-orders.html",
             "help": "The vendor has invoiced (so it has normally shipped) but we haven't received everything.",
             "rows": sorted(billed_not_received, key=lambda r: -(r["days"] or 0))},
            {"key": "po_overdue", "title": "Purchase Orders Past Expected Date", "page": "purchase-orders.html",
             "help": "Ordered, not fully received, and the expected date has passed.", "rows": sorted(overdue_pos, key=lambda r: -r["days"])},
        ]
        bills = db.query(VendorBill).all()
        po_codes = {p.id: (p.code, p.vendor_id) for p in db.query(PurchaseOrder).all()}
        sections.append({"key": "bills_due", "title": "Vendor Invoices Overdue", "page": "purchase-orders.html",
                         "help": "Unpaid vendor invoices past their due date.",
                         "rows": [{"id": b.po_id, "code": po_codes.get(b.po_id, ("", None))[0], "vendor": vendors.get(po_codes.get(b.po_id, ("", None))[1]),
                                   "bill_number": b.bill_number, "balance": b.balance, "due": b.due_date.isoformat(), "days": _days(b.due_date),
                                   "_rid": b.id, "_date": b.bill_date or b.created_at, "_label": f"{b.bill_number} ({po_codes.get(b.po_id, ('', None))[0]})"}
                                  for b in bills if b.balance > 0.005 and b.due_date and b.due_date < now]})
        open_vp = [vp for vp in db.query(VendorPayment).all() if vp.unapplied > 0.005]
        sections.append({"key": "unapplied_payments", "title": "Vendor Payments Not Applied To A PO", "page": "purchase-orders.html",
                         "help": "Money sent to a vendor that isn't tied to a purchase order yet.",
                         "rows": [{"id": vp.id, "code": vp.code, "vendor": vendors.get(vp.vendor_id), "amount": vp.amount, "unapplied": vp.unapplied,
                                   "paid_date": vp.paid_date.isoformat() if vp.paid_date else None, "days": _days(vp.paid_date),
                                   "_date": vp.paid_date or vp.created_at, "_label": vp.code} for vp in open_vp]})
        # ---- sales money and data waiting on someone ----
        invoices = db.query(Invoice).filter(Invoice.status != "void").all()
        on_invoice = {sid for (sid,) in db.query(InvoiceShipment.shipment_id).join(Invoice).filter(Invoice.status != "void").all()}
        line_price = {l.id: l.unit_price for o in orders.values() for l in o.lines}
        value = lambda s: cents(sum(line_amount(l.quantity, line_price.get(l.order_line_id, 0)) for l in s.lines))
        sections += [
            {"key": "items_verify", "title": "AI-Created Items To Verify", "page": "stock-items.html",
             "help": "Made from a scanned PO; they can't be picked on orders until someone checks and verifies them.",
             "rows": [{"id": i.id, "code": i.code, "title": i.title, "group": i.category, "days": _days(i.created_at), "_date": i.created_at, "_label": i.code}
                      for i in db.query(StockItem).filter(StockItem.created_via == "ai-scan", StockItem.verified_by.is_(None)).all()]},
            {"key": "no_invoice", "title": "Shipped With No Invoice At All", "page": "invoices.html",
             "help": "Shipped or delivered and not on any invoice -- not even a draft.",
             "rows": sorted([ship_row(s, {"amount": value(s)}) for s in shipped if s.id not in on_invoice and s.status in SHIPPED],
                            key=lambda r: -(r["days"] or 0))},
            {"key": "draft_invoices", "title": "Draft Invoices Not Sent", "page": "invoices.html",
             "help": "Drafts (including MRPeasy Dummy invoices) -- send them or delete them.",
             "rows": [{"id": i.id, "code": i.code, "order_id": i.order_id, "order_code": orders[i.order_id].code if i.order_id in orders else None,
                       "customer": customers.get(i.customer_id), "amount": i.total, "days": _days(i.invoice_date), "_date": i.invoice_date, "_label": i.code}
                      for i in invoices if i.status == "draft"]},
            {"key": "invoices_overdue", "title": "Customer Invoices Overdue", "page": "invoices.html",
             "help": "Sent, not fully paid, and past the due date.",
             "rows": [{"id": i.id, "code": i.code, "customer": customers.get(i.customer_id), "balance": i.balance,
                       "due": i.due_date.isoformat(), "days": _days(i.due_date), "_date": i.invoice_date, "_label": i.code}
                      for i in invoices if i.status != "draft" and i.balance > 0.005 and i.due_date and i.due_date < now]},
            {"key": "not_booked", "title": "Confirmed Orders Not Fully Booked", "page": "customer-orders.html",
             "help": "Quantity still to book into a shipment.",
             "rows": sorted([{"id": o.id, "order_code": o.code, "customer": customers.get(o.customer_id), "po_number": o.po_number,
                              "amount": cents(sum(line_amount(max(0, l.quantity - l.shipped_quantity - l.booked_quantity), l.unit_price) for l in o.lines)),
                              "days": _days(o.created_at), "_date": o.created_at, "_label": o.code}
                             for o in orders.values() if o.status == "confirmed"
                             and any(l.quantity - l.shipped_quantity - l.booked_quantity > 1e-9 for l in o.lines)], key=lambda r: -r["amount"])},
            {"key": "captured_orders", "title": "Captured Orders To Validate", "page": "customer-orders.html",
             "help": "Quick-captured customer POs: check them, fill in the lines and validate.",
             "rows": [{"id": o.id, "order_code": o.code, "customer": customers.get(o.customer_id), "po_number": o.po_number, "by": o.created_by,
                       "days": _days(o.created_at), "_date": o.created_at, "_label": o.code} for o in orders.values() if o.status == "validation"]},
            {"key": "draft_orders", "title": "Draft Orders Not Confirmed", "page": "customer-orders.html",
             "help": "Entered but never confirmed.",
             "rows": [{"id": o.id, "order_code": o.code, "customer": customers.get(o.customer_id), "po_number": o.po_number,
                       "amount": cents(sum(line_amount(l.quantity, l.unit_price) for l in o.lines)), "days": _days(o.created_at), "_date": o.created_at, "_label": o.code}
                      for o in orders.values() if o.status == "draft"]},
        ]
        linked = {a for (a,) in db.query(MtrLink.attachment_id).distinct()}
        mtrs = db.query(Attachment).filter(Attachment.category == "mtr", Attachment.entity_type == "purchase_order").all()
        sections.append({"key": "mtr_unlinked", "title": "MTRs Not Linked To PO Lines", "page": "purchase-orders.html",
                         "help": "Uploaded MTRs that won't show up by item until their lines are ticked.",
                         "rows": [{"id": a.entity_id, "code": po_codes.get(a.entity_id, ("", None))[0], "filename": a.filename,
                                   "days": _days(a.created_at), "_rid": a.id, "_date": a.created_at, "_label": a.filename} for a in mtrs if a.id not in linked]})
    return sections


@router.get("/today")
def today_board(db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """The dashboard's Today panel: what the floor, purchasing and money people act on today (company calendar), only
    the parts this role may see."""
    from datetime import timedelta as td
    from app.models import InvoicePayment, PurchaseOrderPayment, ShipmentBox
    from app.services import clock
    today = clock.today()
    week_end = today + td(days=7)
    out = {"date": today.strftime("%Y-%m-%d")}
    if has(user, "shipments.view") or has(user, "shipments.work"):
        open_ships = db.query(Shipment).filter(Shipment.status.in_(("new", "ready"))).all()
        picked = lambda s: s.lines and all((l.picked_quantity or 0) >= l.quantity - 1e-9 for l in s.lines)
        day_start = clock.to_utc(today)
        out["floor"] = {
            "to_pick": sum(1 for s in open_ships if not picked(s)),
            "to_pack": sum(1 for s in open_ships if picked(s) and not s.packed_at),
            "ready_to_ship": sum(1 for s in open_ships if picked(s) and s.packed_at),
            "shipped_today": db.query(Shipment).filter(Shipment.ship_date >= day_start, Shipment.status.in_(SHIPPED)).count(),
        }
    if has(user, "orders.view"):
        due = db.query(CustomerOrder).filter(CustomerOrder.status.in_(("draft", "confirmed")), CustomerOrder.delivery_date.isnot(None)).all()
        out["orders"] = {"due_this_week": sum(1 for o in due if today <= o.delivery_date < week_end),
                         "overdue": sum(1 for o in due if o.delivery_date < today)}
    if has(user, "purchasing"):
        pos = db.query(PurchaseOrder).filter(PurchaseOrder.status.in_(("ordered", "partially_received")), PurchaseOrder.expected_date.isnot(None)).all()
        out["purchasing"] = {"arriving_this_week": sum(1 for p in pos if today <= p.expected_date < week_end),
                             "late": sum(1 for p in pos if p.expected_date < today)}
    if has(user, "invoices"):
        sent = [i for i in db.query(Invoice).filter(Invoice.status == "sent").all() if i.balance > 0.005]
        week_start = today - td(days=today.weekday())
        out["money"] = {
            "due_this_week": round(sum(i.balance for i in sent if i.due_date and today <= i.due_date < week_end), 2),
            "overdue": round(sum(i.balance for i in sent if i.due_date and i.due_date < today), 2),
            "overdue_count": sum(1 for i in sent if i.due_date and i.due_date < today),
            "collected_this_week": round(sum(p.amount for p in db.query(InvoicePayment).filter(InvoicePayment.paid_date >= week_start).all()), 2),
        }
        if has(user, "purchasing"):
            out["money"]["paid_out_this_week"] = round(sum(p.amount for p in db.query(PurchaseOrderPayment)
                                                           .filter(PurchaseOrderPayment.paid_date >= week_start).all()), 2)
    if has(user, "backups"):
        from app.config.settings import settings
        from app.services.backups import list_backups
        try:
            b = list_backups()
            last = max(b, key=lambda x: x["created"], default=None)
            when = datetime.fromisoformat(last["created"]) if last else None
            age_h = (datetime.now(when.tzinfo) - when).total_seconds() / 3600 if when else None
            out["backups"] = {"newest": last["created"] if last else None, "hours": round(age_h, 1) if age_h is not None else None, "count": len(b),
                              "every_hours": settings.backup_every_hours,
                              "copies_missing": sum(1 for ok in (last or {}).get("copies", []) if not ok)}
        except Exception as e:
            out["backups"] = {"error": str(e)}
    return out
