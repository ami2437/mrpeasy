"""What has been billed against each customer order line, across every shipment and invoice.

$0 order lines (the nut of a bolt + nut kit) are left out -- their quantity bills nothing.
Nothing is stored as a running total: invoice lines already point at their order line (and shipment), so
the ledger is summed from them every time and can never drift.

    invoice_differences(db, invoice, lines) -> lines billing a different quantity than their shipments delivered
    record_variances(...)                    -> keep the accepted ones against the order (BillingVariance)
    order_ledger(db, order)                  -> per order line: ordered / shipped / billed
    unbalanced_orders(db)                    -> orders with nothing left to ship or bill whose billing != shipped
"""
from typing import Dict, List

from sqlalchemy.orm import Session

from app.models import BillingVariance, CustomerOrder, CustomerOrderLine, Invoice, InvoiceLine, Shipment, StockItem

EPS = 1e-6


def _delivered_on(invoice: Invoice) -> Dict[int, float]:
    """Quantity per order line that the invoice's shipments delivered."""
    out: Dict[int, float] = {}
    for sh in invoice.shipments:
        if sh.status == "cancelled":
            continue
        for sl in sh.lines:
            out[sl.order_line_id] = out.get(sl.order_line_id, 0) + sl.quantity
    return out


def invoice_differences(db: Session, invoice: Invoice, lines) -> List[dict]:
    """Order lines this invoice would bill a different quantity of than its shipments delivered (over or under).
    `lines` = the invoice lines as they'd be saved (objects or dicts with order_line_id / quantity)."""
    if not invoice.shipments:
        return []  # an invoice not tied to a shipment has nothing to compare with
    delivered = _delivered_on(invoice)
    billed: Dict[int, float] = {}
    for l in lines:
        olid = l.get("order_line_id") if isinstance(l, dict) else l.order_line_id
        qty = l.get("quantity") if isinstance(l, dict) else l.quantity
        if olid:
            billed[olid] = billed.get(olid, 0) + float(qty or 0)
    out = []
    for olid in sorted(set(delivered) | set(billed)):
        d, b = delivered.get(olid, 0), billed.get(olid, 0)
        if abs(d - b) > EPS:
            ol = db.get(CustomerOrderLine, olid)
            if ol and not (ol.unit_price or 0):
                continue  # a $0 line (e.g. the nut of a bolt + nut kit): its quantity bills nothing
            item = db.get(StockItem, ol.item_id) if ol else None
            out.append({"order_line_id": olid, "line_no": ol.line_no if ol else None, "item_code": item.code if item else "",
                        "delivered": d, "billed": b, "difference": b - d})
    return out


def record_variances(db: Session, invoice: Invoice, diffs: List[dict], by: str, reason: str = None) -> None:
    """The invoice's accepted differences, replacing what was recorded for it before."""
    db.query(BillingVariance).filter(BillingVariance.invoice_id == invoice.id).delete()
    for d in diffs:
        db.add(BillingVariance(order_id=invoice.order_id, order_line_id=d["order_line_id"], invoice_id=invoice.id,
                               delivered_qty=d["delivered"], billed_qty=d["billed"], reason=(reason or "").strip() or None, accepted_by=by))


def clear_variances(db: Session, invoice: Invoice) -> None:
    db.query(BillingVariance).filter(BillingVariance.invoice_id == invoice.id).delete()


def order_ledger(db: Session, order: CustomerOrder) -> List[dict]:
    """Per order line: ordered, shipped and billed (all non-void invoices of the order, every shipment)."""
    billed: Dict[int, float] = {}
    rows = (db.query(InvoiceLine.order_line_id, InvoiceLine.quantity).join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .filter(Invoice.order_id == order.id, Invoice.status != "void", InvoiceLine.order_line_id.isnot(None)).all())
    for olid, qty in rows:
        billed[olid] = billed.get(olid, 0) + (qty or 0)
    return [{"order_line_id": l.id, "line_no": l.line_no, "item_id": l.item_id, "ordered": l.quantity, "unit_price": l.unit_price or 0,
             "shipped": l.shipped_quantity or 0, "billed": billed.get(l.id, 0)} for l in order.lines]


def unbalanced_orders(db: Session) -> List[dict]:
    """Orders where billing is finished -- nothing open to ship, every shipment that left is on a live invoice --
    yet some line's billed quantity differs from what shipped. These need correcting (a credit or a new invoice)."""
    out = []
    by_order: Dict[int, list] = {}
    for s in db.query(Shipment).filter(Shipment.status != "cancelled").all():
        by_order.setdefault(s.order_id, []).append(s)
    for order in db.query(CustomerOrder).filter(CustomerOrder.status != "cancelled").all():
        shipments = by_order.get(order.id, [])
        if not shipments or any(s.status in ("new", "ready") for s in shipments):
            continue
        if any(s.status != "invoiced" for s in shipments):
            continue  # still something to bill
        bad = [r for r in order_ledger(db, order) if r["unit_price"] and abs(r["billed"] - r["shipped"]) > EPS]
        if bad:
            out.append({"order": order, "lines": bad})
    return out
