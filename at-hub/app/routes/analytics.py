"""Reports pack (reports.html tabs): sales & margin, customer statements, inventory value, vendor performance, lot trace,
reorder suggestions. Shipments are the sales event: revenue = shipped qty x order price, cost = shipped qty x the
lot's landed unit cost. Months and "today" are the company's (app/services/clock.py)."""
from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_perm
from app.models import (Attachment, Customer, CustomerOrder, CustomerOrderLine, Invoice, Lot, MtrLink, PurchaseOrder,
                        PurchaseOrderLine, Shipment, ShipmentLine, StockItem, User, Vendor)
from app.services import clock

router = APIRouter(prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(require_perm("reports"))])
SHIPPED = ("shipped", "delivered", "invoiced")


def _shipped_lines(db: Session, since: datetime, until: datetime = None):
    """(shipment, shipment line, order line, lot) for everything shipped since `since` (UTC), and before `until` if given."""
    q = (db.query(ShipmentLine, Shipment).join(Shipment, Shipment.id == ShipmentLine.shipment_id)
         .filter(Shipment.status.in_(SHIPPED), Shipment.ship_date >= since))
    if until:
        q = q.filter(Shipment.ship_date < until)
    rows = q.all()
    ol_ids = {sl.order_line_id for sl, _ in rows}
    order_lines = {l.id: l for l in db.query(CustomerOrderLine).filter(CustomerOrderLine.id.in_(ol_ids or {0})).all()}
    lots = {l.id: l for l in db.query(Lot).filter(Lot.id.in_({sl.lot_id for sl, _ in rows if sl.lot_id} or {0})).all()}
    return [(s, sl, order_lines.get(sl.order_line_id), lots.get(sl.lot_id)) for sl, s in rows]


@router.get("/sales")
def sales(months: int = 12, date_from: str = None, date_to: str = None, db: Session = Depends(get_db)):
    """Revenue, cost and margin by month, customer and item over the last `months` months -- or a custom range
    (date_from / date_to, YYYY-MM-DD, company calendar days, both included)."""
    months = max(1, min(36, months))
    today = clock.today()
    first = datetime(today.year, today.month, 1)
    for _ in range(months - 1):
        first = (first - timedelta(days=1)).replace(day=1)
    since, until = clock.to_utc(first), None
    if date_from:
        start = datetime.fromisoformat(date_from[:10])
        since, first = clock.to_utc(start), datetime(start.year, start.month, 1)
    if date_to:
        end = datetime.fromisoformat(date_to[:10]) + timedelta(days=1)
        until, today = clock.to_utc(end), end - timedelta(days=1)
    orders = {o.id: o for o in db.query(CustomerOrder).all()}
    customers = {c.id: c.name for c in db.query(Customer).all()}
    items = {i.id: i for i in db.query(StockItem).all()}
    by_month, by_customer, by_item = defaultdict(lambda: [0.0, 0.0, 0]), defaultdict(lambda: [0.0, 0.0, 0]), defaultdict(lambda: [0.0, 0.0, 0.0])
    no_cost = 0
    for s, sl, ol, lot in _shipped_lines(db, since, until):
        rev = sl.quantity * ((ol.unit_price if ol else sl.unit_price) or 0)
        cost = sl.quantity * (lot.unit_cost or 0) if lot else 0
        if not lot or not lot.unit_cost:
            no_cost += 1
        m = clock.local(s.ship_date).strftime("%Y-%m")
        cust = customers.get(orders[s.order_id].customer_id, "?") if s.order_id in orders else "?"
        for bucket, key in ((by_month, m), (by_customer, cust)):
            bucket[key][0] += rev
            bucket[key][1] += cost
        by_item[sl.item_id][0] += rev
        by_item[sl.item_id][1] += cost
        by_item[sl.item_id][2] += sl.quantity
    month_keys, d = [], first
    while d <= today:
        month_keys.append(d.strftime("%Y-%m"))
        d = (d + timedelta(days=32)).replace(day=1)
    row = lambda k, v: {"key": k, "revenue": round(v[0], 2), "cost": round(v[1], 2), "margin": round(v[0] - v[1], 2),
                        "margin_pct": round((v[0] - v[1]) / v[0] * 100, 1) if v[0] else None}
    return {"months": [row(k, by_month.get(k, [0, 0, 0])) for k in month_keys],
            "customers": sorted((row(k, v) for k, v in by_customer.items()), key=lambda r: -r["revenue"]),
            "items": sorted(({**row(items[k].code if k in items else str(k), v), "title": items[k].title if k in items else "",
                              "quantity": v[2], "item_id": k} for k, v in by_item.items()), key=lambda r: -r["revenue"])[:100],
            "lines_without_cost": no_cost}


def _aging(db: Session):
    today = clock.today()
    out = defaultdict(lambda: {"current": 0.0, "d30": 0.0, "d60": 0.0, "d90": 0.0, "d90p": 0.0, "credit": 0.0, "total": 0.0, "invoices": [], "credits": []})
    for inv in db.query(Invoice).filter(Invoice.status == "sent").all():
        bal = inv.balance
        if bal <= 0.005:
            continue
        due = inv.due_date or ((inv.invoice_date or today) + timedelta(days=30))
        late = (today - datetime.combine(due.date(), datetime.min.time())).days
        b = "current" if late <= 0 else "d30" if late <= 30 else "d60" if late <= 60 else "d90" if late <= 90 else "d90p"
        a = out[inv.customer_id]
        a[b] += bal
        a["total"] += bal
        a["invoices"].append({"id": inv.id, "code": inv.code, "invoice_date": inv.invoice_date, "due_date": due, "total": inv.total,
                              "paid": inv.amount_paid, "balance": round(bal, 2), "days_late": max(0, late), "bucket": b})
    # credit memos not used up yet: the customer owes that much less
    from app.services.credit_memos import open_credits
    for m in open_credits(db):
        a = out[m.customer_id]
        a["credit"] -= m.remaining
        a["total"] -= m.remaining
        a["credits"].append({"id": m.id, "code": m.code, "memo_date": m.memo_date, "total": m.total, "remaining": m.remaining})
    return out


@router.get("/ar-aging")
def ar_aging(db: Session = Depends(get_db)):
    """Open (sent, unpaid) invoices by customer and how late they are -- the statements tab."""
    names = {c.id: c.name for c in db.query(Customer).all()}
    rows = []
    for cid, a in _aging(db).items():
        rows.append({"customer_id": cid, "customer": names.get(cid, "?"), "credits": a["credits"], **{k: round(v, 2) for k, v in a.items() if k not in ("invoices", "credits")},
                     "invoices": sorted(a["invoices"], key=lambda i: i["due_date"])})
    return sorted(rows, key=lambda r: -r["total"])


@router.get("/statement/{customer_id}.pdf")
def statement_pdf(customer_id: int, db: Session = Depends(get_db)):
    """A statement of account for one customer: every open invoice and the aging totals."""
    pdf, name = build_statement(db, customer_id)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


def build_statement(db: Session, customer_id: int):
    """(pdf bytes, file name) of the customer's statement -- the Reports tab and overdue reminders use it."""
    from reportlab.lib.units import inch
    from reportlab.platypus import Spacer
    from app.services import pdf as P
    from app.services.crud import get_company_profile
    cust = db.get(Customer, customer_id)
    if not cust:
        raise HTTPException(status_code=404, detail="Customer not found")
    a = _aging(db).get(customer_id) or {"current": 0, "d30": 0, "d60": 0, "d90": 0, "d90p": 0, "credit": 0, "total": 0, "invoices": [], "credits": []}
    company = get_company_profile(db)
    today = clock.today()
    story = P._header(company, "STATEMENT", today.strftime("%b %d, %Y"))
    story += [P._two_boxes(P._party_box("Customer", cust.name, P.address_lines(cust.address or cust.shipping_address)),
                           P._meta_table([("Statement date", today.strftime("%b %d, %Y")), ("Open invoices", str(len(a["invoices"]))),
                                          ("Balance due", P.money(a["total"]))])), Spacer(1, 12)]
    rows = [[i["code"], P.date(i["invoice_date"]), P.date(i["due_date"]), str(i["days_late"]) if i["days_late"] else "—",
             P.money(i["total"]), P.money(i["paid"]), P.money(i["balance"])] for i in sorted(a["invoices"], key=lambda i: i["due_date"])]
    rows += [[f"Credit {c['code']}", P.date(c["memo_date"]), "", "", P.money(-c["total"]), P.money(-(c["total"] - c["remaining"])), P.money(-c["remaining"])]
             for c in a["credits"]]
    story.append(P._data_table(["Invoice", "Date", "Due", "Days late", "Total", "Paid", "Balance"], rows or [["No open invoices", "", "", "", "", "", ""]],
                               [1.25 * inch, 0.95 * inch, 0.95 * inch, 0.8 * inch, 1.1 * inch, 1.05 * inch, 1.2 * inch], right_cols=(3, 4, 5, 6)))
    keys = ("current", "d30", "d60", "d90", "d90p") + (("credit",) if a["credits"] else ()) + ("total",)
    heads = ["Current", "1-30 days", "31-60 days", "61-90 days", "Over 90"] + (["Credits"] if a["credits"] else []) + ["Total due"]
    story += [Spacer(1, 14), P._data_table(heads, [[P.money(a[k]) for k in keys]], [7.3 * inch / len(keys)] * len(keys),
                                           right_cols=tuple(range(len(keys))))]
    if company.invoice_notes:
        story += P._notes_box([("PAYMENT INSTRUCTIONS", company.invoice_notes)])
    pdf = P._build(story, P._footer_text(company), f"Statement {cust.name}")
    name = f"Statement-{''.join(ch for ch in cust.name if ch.isalnum() or ch in ' -_').strip()}-{today:%Y-%m-%d}.pdf"
    return pdf, name


@router.get("/inventory")
def inventory(slow_days: int = 90, db: Session = Depends(get_db)):
    """Stock value by product group (lot quantity x landed unit cost) and slow movers: stock nothing shipped from lately."""
    items = {i.id: i for i in db.query(StockItem).all()}
    by_group = defaultdict(lambda: {"value": 0.0, "units": 0.0, "items": set()})
    value_by_item = defaultdict(float)
    for lot in db.query(Lot).filter(Lot.quantity > 0).all():
        it = items.get(lot.item_id)
        g = (it.category if it else None) or "Ungrouped"
        v = lot.quantity * (lot.unit_cost or 0)
        by_group[g]["value"] += v
        by_group[g]["units"] += lot.quantity
        by_group[g]["items"].add(lot.item_id)
        value_by_item[lot.item_id] += v
    last_out = dict(db.query(ShipmentLine.item_id, Shipment.ship_date).join(Shipment, Shipment.id == ShipmentLine.shipment_id)
                    .filter(Shipment.status.in_(SHIPPED)).order_by(Shipment.ship_date).all())
    cutoff = datetime.utcnow() - timedelta(days=slow_days)
    slow = []
    for iid, v in value_by_item.items():
        it = items.get(iid)
        lo = last_out.get(iid)
        if it and it.on_hand > 0 and (lo is None or lo < cutoff):
            slow.append({"item_id": iid, "code": it.code, "title": it.title, "on_hand": it.on_hand, "value": round(v, 2),
                         "last_out": lo, "days": (datetime.utcnow() - lo).days if lo else None})
    groups = sorted(({"group": g, "value": round(x["value"], 2), "units": x["units"], "items": len(x["items"])} for g, x in by_group.items()),
                    key=lambda r: -r["value"])
    return {"total": round(sum(g["value"] for g in groups), 2), "groups": groups,
            "slow": sorted(slow, key=lambda r: -r["value"])[:100], "slow_days": slow_days}


@router.get("/vendors")
def vendor_performance(months: int = 12, db: Session = Depends(get_db)):
    """Per vendor: POs, spend, on-time first receipts (by the expected date) and average lead time in days."""
    since = clock.today() - timedelta(days=31 * months)
    vendors = {v.id: v.name for v in db.query(Vendor).all()}
    first_receipt = {}
    for po_id, rd in (db.query(PurchaseOrderLine.po_id, Lot.received_date).join(Lot, Lot.po_line_id == PurchaseOrderLine.id).all()):
        if rd and (po_id not in first_receipt or rd < first_receipt[po_id]):
            first_receipt[po_id] = rd
    stats = defaultdict(lambda: {"pos": 0, "spend": 0.0, "received": 0, "on_time": 0, "late": 0, "lead_days": []})
    for po in db.query(PurchaseOrder).filter(PurchaseOrder.status != "cancelled", PurchaseOrder.order_date >= since).all():
        s = stats[po.vendor_id]
        s["pos"] += 1
        s["spend"] += po.order_total or 0
        fr = first_receipt.get(po.id)
        if fr:
            day = clock.local(fr).date()
            s["received"] += 1
            if po.order_date:
                s["lead_days"].append((day - po.order_date.date()).days)
            if po.expected_date:
                s["on_time" if day <= po.expected_date.date() else "late"] += 1
    out = []
    for vid, s in stats.items():
        judged = s["on_time"] + s["late"]
        out.append({"vendor_id": vid, "vendor": vendors.get(vid, "?"), "pos": s["pos"], "spend": round(s["spend"], 2), "received": s["received"],
                    "on_time": s["on_time"], "late": s["late"], "on_time_pct": round(s["on_time"] / judged * 100) if judged else None,
                    "avg_lead_days": round(sum(s["lead_days"]) / len(s["lead_days"]), 1) if s["lead_days"] else None})
    return sorted(out, key=lambda r: -r["spend"])


@router.get("/lot-trace")
def lot_trace(q: str, db: Session = Depends(get_db)):
    """A lot (or heat) number -> where it came from (PO, vendor, MTRs) and every customer it went to."""
    q = (q or "").strip()
    if len(q) < 2:
        raise HTTPException(status_code=400, detail="Type at least 2 characters of the lot #")
    lots = db.query(Lot).filter(Lot.lot_code.ilike(f"%{q}%")).order_by(Lot.lot_code).limit(25).all()
    items = {i.id: i for i in db.query(StockItem).filter(StockItem.id.in_({l.item_id for l in lots} or {0})).all()}
    customers = {c.id: c.name for c in db.query(Customer).all()}
    out = []
    for lot in lots:
        line = db.get(PurchaseOrderLine, lot.po_line_id) if lot.po_line_id else None
        po = db.get(PurchaseOrder, line.po_id) if line else None
        vendor = db.get(Vendor, po.vendor_id) if po else None
        mtrs = []
        if line:
            for link in db.query(MtrLink).filter(MtrLink.po_line_id == line.id).all():
                att = db.get(Attachment, link.attachment_id)
                if att:
                    mtrs.append({"id": att.id, "filename": att.filename, "heat_number": getattr(link, "heat_number", None)})
        went = []
        for sl, s in (db.query(ShipmentLine, Shipment).join(Shipment, Shipment.id == ShipmentLine.shipment_id)
                      .filter(ShipmentLine.lot_id == lot.id, Shipment.status != "cancelled").all()):
            o = db.get(CustomerOrder, s.order_id)
            went.append({"shipment_id": s.id, "shipment": s.code, "status": s.status, "ship_date": s.ship_date, "quantity": sl.quantity,
                         "order_id": s.order_id, "order": o.code if o else None, "po_number": o.po_number if o else None,
                         "customer": customers.get(o.customer_id) if o else None})
        it = items.get(lot.item_id)
        out.append({"lot_id": lot.id, "lot_code": lot.lot_code, "item_id": lot.item_id, "item_code": it.code if it else "", "item_title": it.title if it else "",
                    "received_date": lot.received_date, "initial_quantity": lot.initial_quantity, "quantity": lot.quantity, "source": lot.source,
                    "po_id": po.id if po else None, "po": po.code if po else lot.source_reference, "vendor": vendor.name if vendor else None,
                    "mtrs": mtrs, "shipments": sorted(went, key=lambda w: w["ship_date"] or datetime.max)})
    return out


def _reorder_rows(db: Session):
    items = db.query(StockItem).filter(StockItem.is_active.isnot(False)).all()
    on_order = defaultdict(float)
    last_vendor = {}
    for line, po in (db.query(PurchaseOrderLine, PurchaseOrder).join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
                     .filter(PurchaseOrder.status != "cancelled").order_by(PurchaseOrder.id).all()):
        if po.status in ("draft", "ordered", "partially_received"):
            on_order[line.item_id] += max(0, line.quantity - line.received_quantity)
        last_vendor[line.item_id] = (po.vendor_id, line.unit_cost, po.code)
    demand = defaultdict(float)
    for l, o in (db.query(CustomerOrderLine, CustomerOrder).join(CustomerOrder, CustomerOrder.id == CustomerOrderLine.order_id)
                 .filter(CustomerOrder.status.in_(("draft", "confirmed"))).all()):
        demand[l.item_id] += max(0, l.quantity - l.shipped_quantity - l.booked_quantity)
    vendors = {v.id: v.name for v in db.query(Vendor).all()}
    rows = []
    for it in items:
        if it.parent_item_id:  # draws from a generic item: reorder that one instead
            continue
        available = (it.on_hand or 0) - (it.booked or 0)
        supply = available + on_order[it.id]
        short = demand[it.id] - supply
        rp = it.reorder_point or 0
        low = rp > 0 and supply - demand[it.id] <= rp
        if short <= 0 and not low:
            continue
        suggest = max(short, (rp * 2 - (supply - demand[it.id])) if rp else 0)
        if it.default_pack_size:
            pack = it.default_pack_size
            suggest = -(-suggest // pack) * pack
        vid, cost, last_po = last_vendor.get(it.id, (None, it.cost_price or 0, None))
        rows.append({"item_id": it.id, "code": it.code, "title": it.title, "on_hand": it.on_hand, "available": available,
                     "on_order": on_order[it.id], "open_demand": demand[it.id], "reorder_point": rp, "short": max(0, short),
                     "suggest": round(max(suggest, 1)), "vendor_id": vid, "vendor": vendors.get(vid), "unit_cost": cost, "last_po": last_po,
                     "why": "Open orders need more than on hand + on order" if short > 0 else "At or below the reorder point"})
    return sorted(rows, key=lambda r: (-r["short"], r["code"]))


@router.get("/reorder")
def reorder(db: Session = Depends(get_db)):
    """Items to buy: open customer demand beyond what's on hand and on order, or at / below the reorder point."""
    return _reorder_rows(db)


class ReorderLine(BaseModel):
    item_id: int
    quantity: float
    vendor_id: int
    unit_cost: Optional[float] = 0


class ReorderIn(BaseModel):
    lines: List[ReorderLine]


@router.post("/reorder/create-pos", dependencies=[Depends(require_perm("purchasing"))])
def reorder_create(data: ReorderIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """Draft POs from the ticked suggestions, one per vendor (nothing is sent -- check and send them as usual)."""
    from types import SimpleNamespace
    from app.services.crud import PurchaseOrderService
    by_vendor = defaultdict(list)
    for l in data.lines:
        if l.quantity > 0:
            by_vendor[l.vendor_id].append(l)
    if not by_vendor:
        raise HTTPException(status_code=400, detail="Tick at least one item with a vendor and a quantity")
    made = []
    for vid, lines in by_vendor.items():
        po = PurchaseOrderService.create(db, SimpleNamespace(
            vendor_id=vid, expected_date=None, vendor_so_number=None, notes="Drafted from Reports > Reorder suggestions",
            lines=[SimpleNamespace(item_id=l.item_id, quantity=round(l.quantity), unit_cost=round(l.unit_cost or 0, 5), vendor_item_code=None,
                                   vendor_description=None, notes=None, print_notes=True) for l in lines]), user.username)
        made.append({"id": po.id, "code": po.code})
    return {"created": made}
