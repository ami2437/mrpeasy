"""What has been billed against each customer order line, across every shipment and invoice.

A $0 order line (the nut of a bolt + nut kit) left off an invoice altogether isn't a difference; billed at all, it's checked.
Nothing is stored as a running total: invoice lines already point at their order line (and shipment), so
the ledger is summed from them every time and can never drift.

    invoice_differences(db, invoice, lines) -> lines billing a different quantity than their shipments delivered
    record_variances(...)                    -> keep the accepted ones against the order (BillingVariance)
    order_ledger(db, order)                  -> per order line: ordered / shipped / billed + what doesn't add up
    unbalanced_orders(db)                    -> orders with nothing left to ship or bill whose billing != shipped
    order_mismatches(db)                     -> orders billed / shipped past what was ordered, or shipments not adding up
"""
from typing import Dict, List

from sqlalchemy.orm import Session

from app.models import BillingVariance, CustomerOrder, CustomerOrderLine, Invoice, InvoiceLine, Shipment, ShipmentLine, StockItem

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


def billed_elsewhere(db: Session, invoice: Invoice) -> Dict[int, Dict[str, float]]:
    """Per order line: what OTHER live invoices bill from this invoice's shipments (its lines were split onto them),
    as {order_line_id: {invoice code: qty}}."""
    sids = [s.id for s in invoice.shipments]
    if not sids:
        return {}
    rows = (db.query(InvoiceLine.order_line_id, InvoiceLine.quantity, Invoice.code)
            .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .filter(Invoice.id != invoice.id, Invoice.status != "void", InvoiceLine.shipment_id.in_(sids),
                    InvoiceLine.order_line_id.isnot(None)).all())
    out: Dict[int, Dict[str, float]] = {}
    for olid, qty, code in rows:
        out.setdefault(olid, {})
        out[olid][code] = out[olid].get(code, 0) + float(qty or 0)
    return out


def invoice_differences(db: Session, invoice: Invoice, lines) -> List[dict]:
    """Order lines this invoice would bill a different quantity of than its shipments delivered (over or under).
    `lines` = the invoice lines as they'd be saved (objects or dicts with order_line_id / quantity). What other live
    invoices bill from the same shipments (split lines) counts too: delivered = billed here + billed there."""
    if not invoice.shipments:
        return []  # an invoice not tied to a shipment has nothing to compare with
    delivered = _delivered_on(invoice)
    others = billed_elsewhere(db, invoice)
    billed: Dict[int, float] = {}
    for l in lines:
        olid = l.get("order_line_id") if isinstance(l, dict) else l.order_line_id
        qty = l.get("quantity") if isinstance(l, dict) else l.quantity
        if olid:
            billed[olid] = billed.get(olid, 0) + float(qty or 0)
    out = []
    for olid in sorted(set(delivered) | set(billed)):
        d, b = delivered.get(olid, 0), billed.get(olid, 0)
        e = sum(others.get(olid, {}).values())
        if abs(d - b - e) > EPS:
            ol = db.get(CustomerOrderLine, olid)
            if ol and not (ol.unit_price or 0) and b + e <= EPS:
                continue  # a $0 line left off the invoice (the nut of a bolt + nut kit): nothing billed, nothing to check
            item = db.get(StockItem, ol.item_id) if ol else None
            out.append({"order_line_id": olid, "line_no": ol.line_no if ol else None, "item_code": item.code if item else "",
                        "delivered": d, "billed": b, "elsewhere": e, "elsewhere_codes": sorted(others.get(olid, {})),
                        "difference": b + e - d})
    return out


def record_variances(db: Session, invoice: Invoice, diffs: List[dict], by: str, reason: str = None) -> None:
    """The invoice's accepted differences, replacing what was recorded for it before."""
    db.query(BillingVariance).filter(BillingVariance.invoice_id == invoice.id).delete()
    for d in diffs:
        db.add(BillingVariance(order_id=invoice.order_id, order_line_id=d["order_line_id"], invoice_id=invoice.id,
                               delivered_qty=d["delivered"], billed_qty=d["billed"] + d.get("elsewhere", 0),
                               reason=(reason or "").strip() or None, accepted_by=by))


def clear_variances(db: Session, invoice: Invoice) -> None:
    db.query(BillingVariance).filter(BillingVariance.invoice_id == invoice.id).delete()


def refile_variances(db: Session, invoice: Invoice, by: str, reasons: Dict[int, tuple]) -> None:
    """After lines moved between invoices: the invoice's differences as they stand now, keeping the reasons
    (order_line_id -> (reason, accepted_by)) given when they were accepted."""
    diffs = invoice_differences(db, invoice, invoice.lines)
    clear_variances(db, invoice)
    for d in diffs:
        reason, who = reasons.get(d["order_line_id"], (None, by))
        db.add(BillingVariance(order_id=invoice.order_id, order_line_id=d["order_line_id"], invoice_id=invoice.id,
                               delivered_qty=d["delivered"], billed_qty=d["billed"] + d["elsewhere"], reason=reason, accepted_by=who))


SHIPPED_STATUSES = ("shipped", "delivered", "invoiced")


def order_ledger(db: Session, order: CustomerOrder) -> List[dict]:
    """Per order line: ordered, shipped and billed (all non-void invoices of the order, every shipment) -- the customer
    order is the source of truth, so each line also lists what doesn't add up against it ("problems"):
      over_ordered_billed   billed more than was ordered
      over_ordered_shipped  shipped more than was ordered
      shipments_disagree    the line's shipped count differs from its shipments added up
      billed_vs_shipped     billed differs from what shipped (only a problem once nothing is left to bill)"""
    billed: Dict[int, float] = {}
    rows = (db.query(InvoiceLine.order_line_id, InvoiceLine.quantity).join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .filter(Invoice.order_id == order.id, Invoice.status != "void", InvoiceLine.order_line_id.isnot(None)).all())
    for olid, qty in rows:
        billed[olid] = billed.get(olid, 0) + (qty or 0)
    on_shipments: Dict[int, float] = {}
    for sh in db.query(Shipment).filter(Shipment.order_id == order.id, Shipment.status.in_(SHIPPED_STATUSES)).all():
        for sl in sh.lines:
            on_shipments[sl.order_line_id] = on_shipments.get(sl.order_line_id, 0) + sl.quantity
    out = []
    for l in order.lines:
        ordered, shipped, b = l.quantity or 0, l.shipped_quantity or 0, billed.get(l.id, 0)
        counted = on_shipments.get(l.id, 0)
        problems = []
        if b > ordered + EPS and (l.unit_price or 0):
            problems.append("over_ordered_billed")
        if shipped > ordered + EPS:
            problems.append("over_ordered_shipped")
        if abs(counted - shipped) > EPS:
            problems.append("shipments_disagree")
        if abs(b - shipped) > EPS and ((l.unit_price or 0) or b > EPS):
            problems.append("billed_vs_shipped")
        out.append({"order_line_id": l.id, "line_no": l.line_no, "item_id": l.item_id, "ordered": ordered, "unit_price": l.unit_price or 0,
                    "shipped": shipped, "shipped_on_shipments": counted, "billed": b, "problems": problems})
    return out


def order_mismatches(db: Session) -> List[dict]:
    """Orders where something doesn't add up against the order itself, whatever stage they're at: billed or shipped
    more than was ordered, or the shipped count disagreeing with the shipments."""
    from sqlalchemy import func
    billed = dict(db.query(InvoiceLine.order_line_id, func.sum(InvoiceLine.quantity)).join(Invoice, Invoice.id == InvoiceLine.invoice_id)
                  .filter(Invoice.status != "void", InvoiceLine.order_line_id.isnot(None)).group_by(InvoiceLine.order_line_id).all())
    counted = dict(db.query(ShipmentLine.order_line_id, func.sum(ShipmentLine.quantity)).join(Shipment, Shipment.id == ShipmentLine.shipment_id)
                   .filter(Shipment.status.in_(SHIPPED_STATUSES)).group_by(ShipmentLine.order_line_id).all())
    by_order: Dict[int, list] = {}
    for l in (db.query(CustomerOrderLine).join(CustomerOrder, CustomerOrder.id == CustomerOrderLine.order_id)
              .filter(CustomerOrder.status != "cancelled").all()):
        ordered, shipped, b, c = l.quantity or 0, l.shipped_quantity or 0, billed.get(l.id) or 0, counted.get(l.id) or 0
        problems = ([p for p, bad in (("over_ordered_billed", b > ordered + EPS and (l.unit_price or 0)),
                                      ("over_ordered_shipped", shipped > ordered + EPS),
                                      ("shipments_disagree", abs(c - shipped) > EPS)) if bad])
        if problems:
            by_order.setdefault(l.order_id, []).append({"order_line_id": l.id, "line_no": l.line_no, "item_id": l.item_id, "ordered": ordered,
                                                        "shipped": shipped, "shipped_on_shipments": c, "billed": b, "problems": problems})
    orders = {o.id: o for o in db.query(CustomerOrder).filter(CustomerOrder.id.in_(list(by_order))).all()} if by_order else {}
    return [{"order": orders[oid], "lines": sorted(rows, key=lambda r: r["line_no"] or 0)} for oid, rows in by_order.items()]


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
        bad = [r for r in order_ledger(db, order) if abs(r["billed"] - r["shipped"]) > EPS and (r["unit_price"] or r["billed"] > EPS)]
        if bad:
            out.append({"order": order, "lines": bad})
    return out


def delivered_for(invoice: Invoice) -> Dict[int, float]:
    """What the invoice's shipments delivered per order line -- the invoice screen warns as you type."""
    return _delivered_on(invoice)


def open_differences(db: Session, invoice: Invoice) -> List[dict]:
    """The saved invoice's differences, with the reason given when they were accepted."""
    diffs = invoice_differences(db, invoice, invoice.lines)
    why = {v.order_line_id: (v.reason, v.accepted_by, v.accepted_at) for v in db.query(BillingVariance).filter(BillingVariance.invoice_id == invoice.id).all()}
    for d in diffs:
        r = why.get(d["order_line_id"])
        d.update({"reason": r[0], "accepted_by": r[1], "accepted_at": r[2].isoformat() + "Z" if r and r[2] else None} if r else {"reason": None, "accepted_by": None, "accepted_at": None})
    return diffs
