"""Quick Insights: the money state of sales, shipping, invoicing and purchasing at a glance (the pop-up on the
Customer Orders, Shipments, Invoices and Purchase Orders screens). Every figure is open work right now, except the
"this month" ones; each order / shipment line is rounded to the cent like invoices (services/money.py), so the pieces
of a total add up to it. A section is left out when the person can't open that screen."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, selectinload

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_perm
from app.models import CustomerOrder, Invoice, InvoiceShipment, PurchaseOrder, Shipment, User
from app.services import clock
from app.services.crud import po_left_to_pay
from app.services.money import cents, line_amount
from app.services.permissions import has

router = APIRouter(prefix="/api/insights", tags=["insights"], dependencies=[Depends(require_perm("money.view"))])


def _fig(key, label, amount, count=None, hint=None, tone=None, link=None):
    return {"key": key, "label": label, "amount": cents(amount), "count": count, "hint": hint, "tone": tone, "link": link}


def _orders(db: Session) -> dict:
    orders = db.query(CustomerOrder).options(selectinload(CustomerOrder.lines)).filter(CustomerOrder.status != "cancelled").all()
    open_ = [o for o in orders if o.status == "confirmed"]
    shipped = booked = unbooked = 0.0
    for o in open_:
        for l in o.lines:
            s = min(l.shipped_quantity or 0, l.quantity)
            b = min(l.booked_quantity, max(0, l.quantity - s))
            shipped += line_amount(s, l.unit_price)
            booked += line_amount(b, l.unit_price)
            unbooked += line_amount(max(0, l.quantity - s - b), l.unit_price)
    pending = [o for o in orders if o.status in ("draft", "validation")]
    pending_amt = sum(line_amount(l.quantity, l.unit_price) for o in pending for l in o.lines)
    total = shipped + booked + unbooked
    return {"key": "orders", "title": "Customer Orders", "page": "customer-orders.html",
            "headline": _fig("open", "Open orders", total, len(open_), "confirmed, not fully shipped"),
            "bar": ["shipped", "booked", "unbooked"],
            "figures": [
                _fig("shipped", "Shipped so far", shipped, hint="of the open orders", tone="pos"),
                _fig("booked", "In shipments, not shipped", booked, hint="booked / picking / packing", tone="info"),
                _fig("unbooked", "Not booked yet", unbooked, hint="still to put on a shipment", tone="warn"),
                _fig("drafts", "Draft / to validate", pending_amt, len(pending), "not confirmed yet"),
            ]}


def _shipment_value(s: Shipment) -> float:
    return sum(line_amount(sl.quantity, sl.order_line.unit_price if sl.order_line else sl.unit_price) for sl in s.lines)


def _shipments(db: Session) -> dict:
    ships = (db.query(Shipment).options(selectinload(Shipment.lines))
             .filter(Shipment.status.in_(("new", "ready", "shipped", "delivered", "invoiced"))).all())
    draft_inv = {sid for (sid,) in db.query(InvoiceShipment.shipment_id).join(Invoice, Invoice.id == InvoiceShipment.invoice_id)
                 .filter(Invoice.status == "draft").all()}
    buckets = {k: [0.0, 0] for k in ("booked", "to_pick", "picked", "packed", "in_transit", "delivered", "draft_invoice")}

    def add(k, s):
        buckets[k][0] += _shipment_value(s)
        buckets[k][1] += 1

    for s in ships:
        if s.status in ("new", "ready"):
            picked = any((sl.picked_quantity or 0) > 0 for sl in s.lines)
            add("packed" if s.packed_at else "picked" if picked else "to_pick" if s.status == "ready" else "booked", s)
        elif s.status == "shipped":
            add("in_transit", s)
        elif s.status == "delivered":
            add("delivered", s)
        elif s.status == "invoiced" and s.id in draft_inv:
            add("draft_invoice", s)
    in_process = sum(buckets[k][0] for k in ("booked", "to_pick", "picked", "packed"))
    n_process = sum(buckets[k][1] for k in ("booked", "to_pick", "picked", "packed"))
    not_billed = buckets["in_transit"][0] + buckets["delivered"][0]
    f = lambda k, label, hint, tone=None, link=None: _fig(k, label, buckets[k][0], buckets[k][1], hint, tone, link)
    return {"key": "shipments", "title": "Shipments", "page": "shipments.html",
            "headline": _fig("process", "In process", in_process, n_process, "booked, not shipped yet"),
            "bar": ["booked", "to_pick", "picked", "packed"],
            "figures": [
                f("booked", "Booked", "waiting for the booking to be confirmed", "muted"),
                f("to_pick", "Ready to pick", "booking confirmed, nothing picked", "info"),
                f("picked", "Picked", "picked, packing not accepted", "info"),
                f("packed", "Packed", "ready to ship", "pos"),
            ],
            "after": [
                _fig("not_billed", "Shipped, not invoiced", not_billed, buckets["in_transit"][1] + buckets["delivered"][1],
                     "money waiting to be billed", "warn" if not_billed > 0.005 else "pos", "invoices.html?view=ready"),
                f("in_transit", "· shipped, not delivered", "on the way (or delivery not recorded)", None, "invoices.html?view=ready"),
                f("delivered", "· delivered, not invoiced", "bill these now", "warn", "invoices.html?view=ready"),
                f("draft_invoice", "On a draft invoice", "invoice made, not sent yet", "muted", "invoices.html"),
            ]}


def _invoices(db: Session) -> dict:
    invs = (db.query(Invoice).options(selectinload(Invoice.lines), selectinload(Invoice.payments))
            .filter(Invoice.status != "void").all())
    today = clock.today()
    month_start = today.replace(day=1)
    out = [i for i in invs if i.status in ("sent", "paid") and i.balance > 0.005]
    overdue = [i for i in out if i.due_date and i.due_date < today]
    drafts = [i for i in invs if i.status == "draft"]
    billed = [i for i in invs if i.status in ("sent", "paid")]
    billed_month = [i for i in billed if i.invoice_date and i.invoice_date >= month_start]
    paid_month = [p for i in invs for p in i.payments if p.paid_date and p.paid_date >= month_start]
    unpaid = sum(i.balance for i in out)
    over_amt = sum(i.balance for i in overdue)
    return {"key": "invoices", "title": "Invoices", "page": "invoices.html",
            "headline": _fig("unpaid", "Unpaid", unpaid, len(out), "sent, still owed by customers", "warn" if unpaid > 0.005 else "pos"),
            "bar": ["current", "overdue"],
            "figures": [
                _fig("current", "Not due yet", unpaid - over_amt, len(out) - len(overdue), tone="info"),
                _fig("overdue", "Overdue", over_amt, len(overdue), "past the due date", "neg" if overdue else "pos"),
                _fig("drafts", "Draft, not sent", sum(i.total for i in drafts), len(drafts), "send them to get paid", "muted"),
                _fig("paid_total", "Collected (all time)", sum(i.amount_paid for i in billed), None,
                     f"of ${cents(sum(i.total for i in billed)):,.2f} invoiced", "pos"),
            ],
            "after": [
                _fig("billed_month", "Invoiced this month", sum(i.total for i in billed_month), len(billed_month)),
                _fig("paid_month", "Collected this month", sum(p.amount for p in paid_month), len(paid_month), "payments", "pos"),
            ]}


def _purchasing(db: Session) -> dict:
    pos = (db.query(PurchaseOrder).options(selectinload(PurchaseOrder.lines), selectinload(PurchaseOrder.payments),
                                           selectinload(PurchaseOrder.bills), selectinload(PurchaseOrder.charges))
           .filter(PurchaseOrder.status != "cancelled").all())
    today = clock.today()
    live = [p for p in pos if p.status not in ("draft", "validation")]
    open_ = [p for p in live if p.status in ("ordered", "partially_received")]
    received = not_received = 0.0
    for p in open_:
        for l in p.lines:
            r = min(l.received_quantity or 0, l.quantity)
            received += line_amount(r, l.unit_cost)
            not_received += line_amount(max(0, l.quantity - r), l.unit_cost)
    owed = [(p, po_left_to_pay(p)) for p in live]
    owed = [(p, a) for p, a in owed if a > 0.005]
    bills_due = [b for p in live for b in p.bills if b.balance > 0.005 and b.due_date and b.due_date < today]
    rec_not_billed = 0.0
    n_rnb = 0
    for p in live:
        got = sum(line_amount(min(l.received_quantity or 0, l.quantity), l.unit_cost) for l in p.lines)
        gap = got - sum(b.amount for b in p.bills)
        if gap > 0.5:
            rec_not_billed += gap
            n_rnb += 1
    drafts = [p for p in pos if p.status in ("draft", "validation")]
    return {"key": "purchasing", "title": "Purchase Orders", "page": "purchase-orders.html",
            "headline": _fig("open", "Open POs", received + not_received, len(open_), "ordered, not fully received"),
            "bar": ["received", "not_received"],
            "figures": [
                _fig("received", "Received so far", received, hint="of the open POs", tone="pos"),
                _fig("not_received", "Not received yet", not_received, hint="still coming in", tone="warn"),
                _fig("drafts", "Draft / to validate", sum(p.order_total for p in drafts), len(drafts), "not ordered yet", "muted"),
            ],
            "after": [
                _fig("owed", "We owe vendors", sum(a for _, a in owed), len(owed), "PO totals (or bills) less payments", "warn" if owed else "pos"),
                _fig("bills_overdue", "· vendor bills overdue", sum(b.balance for b in bills_due), len(bills_due),
                     "past their due date", "neg" if bills_due else "pos"),
                _fig("rec_not_billed", "Received, not billed by vendor", rec_not_billed, n_rnb, "expect these bills", "muted"),
                _fig("paid_total", "Paid to vendors (all time)", sum(p.amount_paid for p in live), None, tone="pos"),
            ]}


SECTIONS = [("orders", "orders.view", _orders), ("shipments", "shipments.view", _shipments),
            ("invoices", "invoices", _invoices), ("purchasing", "purchasing", _purchasing)]


@router.get("/")
def insights(db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    return {"as_of": clock.today().isoformat(),
            "sections": [fn(db) for key, perm, fn in SECTIONS if has(user, perm)]}
