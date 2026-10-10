"""Correcting a receipt of goods (the lots one Receive click made): its date, a line's quantity / lot # / PO line, or
undoing the whole receipt. Stock, the stock ledger, the PO's received quantities, landed costs and the PO status all
follow; nothing that already went out (booked or shipped from the lot) can be taken back -- the refusal says where it went.

    set_date(db, po, lot_ids, day, by, tz)      the receipt's (or one line's) received date -- a company calendar day
    undo(db, po, lot_ids, by)                   the whole receipt reversed (only while none of it is booked / shipped)
    edit_line(db, po, lot_id, changes, by, tz)  {quantity, lot_code, po_line_id, received_date}

Each change is written to the stock ledger (type "receipt_correction" / "receipt_undo") and the PO's History (the
request middleware) with who and when."""
from datetime import datetime, time

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import InventoryTransaction, Lot, PurchaseOrder, PurchaseOrderLine, Shipment, ShipmentLine, StockItem
from app.services import clock


def _lots(db: Session, po: PurchaseOrder, lot_ids) -> list:
    line_ids = {l.id for l in po.lines}
    lots = db.query(Lot).filter(Lot.id.in_(list(lot_ids or []) or [0])).all()
    if not lots or len(lots) != len(set(lot_ids)) or any(l.po_line_id not in line_ids for l in lots):
        raise HTTPException(status_code=400, detail=f"Those lots aren't a receipt on {po.code} -- reload the PO")
    return lots


def _out_of(db: Session, lot: Lot) -> dict:
    """What has left the lot or is promised from it: shipped / used, booked on open shipments (by shipment), moved on."""
    from app.services.crud import ShipmentService
    booked = (db.query(Shipment.code, func.sum(ShipmentLine.quantity)).join(Shipment, Shipment.id == ShipmentLine.shipment_id)
              .filter(ShipmentLine.lot_id == lot.id, Shipment.status != "cancelled").group_by(Shipment.code).all())
    open_booked = (db.query(func.coalesce(func.sum(ShipmentLine.quantity), 0)).join(Shipment, Shipment.id == ShipmentLine.shipment_id)
                   .filter(ShipmentLine.lot_id == lot.id, Shipment.status.in_(ShipmentService.OPEN_STATUSES)).scalar() or 0)
    gone = round((lot.initial_quantity or 0) - (lot.quantity or 0), 6)
    return {"gone": max(0.0, gone), "booked": float(open_booked), "used": max(0.0, gone) + float(open_booked),
            "where": [f"{code} ({q:g})" for code, q in booked if q]}


def _moment(day: str, keep: datetime, tz: str = None) -> datetime:
    """A calendar day picked by the user, keeping the receipt's time of day (so its lots stay one receipt)."""
    try:
        d = datetime.fromisoformat(str(day)[:10]).date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Pick a date")
    if d > clock.today(tz).date():
        raise HTTPException(status_code=400, detail="The receipt date can't be in the future")
    local = clock.local(keep, tz) if keep else None
    t = local.time() if local else time(12, 0)
    return clock.to_utc(datetime.combine(d, t), tz)


def _receipt_txs(db: Session, lot: Lot):
    return db.query(InventoryTransaction).filter(InventoryTransaction.lot_id == lot.id, InventoryTransaction.type == "receipt").all()


def set_date(db: Session, po: PurchaseOrder, lot_ids, day: str, by: str, tz: str = None):
    lots = _lots(db, po, lot_ids)
    for lot in lots:
        new = _moment(day, lot.received_date, tz)
        lot.received_date = new
        for tx in _receipt_txs(db, lot):
            tx.created_at = new
    db.commit()
    db.refresh(po)
    return po


def undo(db: Session, po: PurchaseOrder, lot_ids, by: str):
    from app.services.crud import PurchaseOrderService, recompute_lot_costs, refresh_item_cost
    lots = _lots(db, po, lot_ids)
    problems = []
    for lot in lots:
        o = _out_of(db, lot)
        if o["used"] > 1e-9:
            problems.append(f"lot {lot.lot_code}: {o['used']:g} already booked / shipped" + (f" on {', '.join(o['where'])}" if o["where"] else ""))
        if db.query(Lot).filter(Lot.parent_lot_id == lot.id).first():
            problems.append(f"lot {lot.lot_code}: some of it was transferred to another item")
    if problems:
        raise HTTPException(status_code=400, detail="This receipt can't be undone -- " + "; ".join(problems)
                                                    + ". Correct the quantities that are still on the shelf instead.")
    lines, items = set(), {}
    for lot in lots:
        qty = lot.initial_quantity or 0
        line = db.get(PurchaseOrderLine, lot.po_line_id)
        item = db.get(StockItem, lot.item_id)
        # the ledger keeps the receipt and its reversal (no longer tied to a lot that doesn't exist any more)
        for tx in db.query(InventoryTransaction).filter(InventoryTransaction.lot_id == lot.id).all():
            tx.lot_id = None
        db.add(InventoryTransaction(item_id=item.id, lot_id=None, quantity_delta=-qty, type="receipt_undo", reference=po.code,
                                    note=f"Receipt undone: lot {lot.lot_code} ({qty:g})", created_by=by))
        item.on_hand -= qty
        line.received_quantity = max(0.0, (line.received_quantity or 0) - qty)
        lines.add(line.id)
        items[item.id] = item
        db.delete(lot)
    db.flush()
    recompute_lot_costs(db, list(lines))
    for item in items.values():
        refresh_item_cost(db, item)
    PurchaseOrderService.refresh_status(po)
    db.commit()
    db.refresh(po)
    return po


def edit_line(db: Session, po: PurchaseOrder, lot_id: int, ch: dict, by: str, tz: str = None):
    from app.services.crud import PurchaseOrderService, recompute_lot_costs, refresh_item_cost
    lot = _lots(db, po, [lot_id])[0]
    line = db.get(PurchaseOrderLine, lot.po_line_id)
    item = db.get(StockItem, lot.item_id)
    notes, lines = [], {line.id}
    # move to another line of this PO (received against the wrong line) -- same item only
    if ch.get("po_line_id") and ch["po_line_id"] != line.id:
        to = db.get(PurchaseOrderLine, ch["po_line_id"])
        if not to or to.po_id != po.id:
            raise HTTPException(status_code=400, detail=f"That line isn't on {po.code}")
        if to.item_id != lot.item_id:
            raise HTTPException(status_code=400, detail="Only to a line for the same item -- received the wrong item? Undo the receipt and receive it again")
        qty = lot.initial_quantity or 0
        if (to.received_quantity or 0) + qty > to.quantity + 1e-9:
            raise HTTPException(status_code=400, detail=f"That line only has {to.quantity - (to.received_quantity or 0):g} left to receive")
        line.received_quantity = max(0.0, (line.received_quantity or 0) - qty)
        to.received_quantity = (to.received_quantity or 0) + qty
        lot.po_line_id, lot.base_unit_cost = to.id, to.unit_cost
        notes.append(f"moved from line {line.id} to line {to.id}")
        lines.add(to.id)
        line = to
    # quantity
    if ch.get("quantity") is not None:
        new = float(ch["quantity"])
        old = lot.initial_quantity or 0
        if new <= 0:
            raise HTTPException(status_code=400, detail="To take the whole line off, undo the receipt (or enter what really came in)")
        o = _out_of(db, lot)
        if new < o["used"] - 1e-9:
            raise HTTPException(status_code=400, detail=f"Lot {lot.lot_code}: {o['used']:g} is already booked / shipped"
                                                        + (f" ({', '.join(o['where'])})" if o["where"] else "") + f" -- it can't go below that")
        delta = round(new - old, 6)
        if delta and (line.received_quantity or 0) + delta > line.quantity + 1e-9:
            raise HTTPException(status_code=400, detail=f"The PO line is for {line.quantity:g} -- {line.received_quantity:g} is received already. "
                                                        "Raise the PO line's quantity first if more came in")
        if delta:
            lot.initial_quantity = new
            lot.quantity = (lot.quantity or 0) + delta
            item.on_hand += delta
            line.received_quantity = (line.received_quantity or 0) + delta
            db.add(InventoryTransaction(item_id=item.id, lot_id=lot.id, quantity_delta=delta, type="receipt_correction", reference=po.code,
                                        note=f"Receipt corrected: lot {lot.lot_code} {old:g} -> {new:g}", created_by=by))
            notes.append(f"quantity {old:g} -> {new:g}")
    # lot #
    if ch.get("lot_code") is not None:
        code = (ch["lot_code"] or "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="The lot # can't be empty")
        if code != lot.lot_code:
            notes.append(f"lot # {lot.lot_code} -> {code}")
            lot.lot_code = code
    # date
    if ch.get("received_date"):
        lot.received_date = _moment(ch["received_date"], lot.received_date, tz)
        for tx in _receipt_txs(db, lot):
            tx.created_at = lot.received_date
    db.flush()
    recompute_lot_costs(db, list(lines))
    refresh_item_cost(db, item)
    PurchaseOrderService.refresh_status(po)
    db.commit()
    db.refresh(po)
    return po
