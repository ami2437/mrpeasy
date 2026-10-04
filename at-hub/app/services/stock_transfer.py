"""Generic stock shared by specific items: bulk 5/8 2H nuts bought by the hundred thousand, used by
15420-NUT, 33251-NUT, ... An item can name a generic item it "draws from" (parent_item_id).

Transfer from generic: free stock leaves the generic item's lots (oldest first) and becomes new lots
on the specific item, coded <generic lot>-T1, -T2 ..., each keeping the generic lot's cost, its PO line
(so landed costs and MTRs still trace) and a link back (parent_lot_id). Both items' on-hand and
movement history update. Return to generic reverses unbooked transferred stock.

Nothing else changes: booking, shipping and costing keep working lot by lot as before."""
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import InventoryTransaction, Lot, StockItem


def check_parent(db: Session, item: StockItem, parent_id: Optional[int]) -> Optional[int]:
    if not parent_id:
        return None
    parent = db.query(StockItem).filter(StockItem.id == parent_id).first()
    if not parent:
        raise HTTPException(status_code=400, detail="That generic item doesn't exist")
    if parent.id == item.id:
        raise HTTPException(status_code=400, detail="An item can't draw from itself")
    if parent.parent_item_id:
        raise HTTPException(status_code=400, detail=f"{parent.code} itself draws from another item -- pick the top generic item")
    if db.query(StockItem).filter(StockItem.parent_item_id == item.id).first():
        raise HTTPException(status_code=400, detail=f"{item.code} is a generic item other items draw from, so it can't draw from one")
    return parent.id


def _next_suffix(db: Session, parent_lot_code: str) -> str:
    codes = {c for (c,) in db.query(Lot.lot_code).filter(Lot.lot_code.like(f"{parent_lot_code}-T%")).all()}
    n = 1
    while f"{parent_lot_code}-T{n}" in codes:
        n += 1
    return f"{parent_lot_code}-T{n}"


def from_parent(db: Session, item_id: int, quantity: float, by: str, reference: str = None) -> List[Lot]:
    from app.services.crud import ShipmentService, refresh_item_cost
    item = db.query(StockItem).filter(StockItem.id == item_id).first()
    if not item or not item.parent_item_id:
        raise HTTPException(status_code=400, detail="This item doesn't draw from a generic item")
    if quantity is None or quantity <= 0 or abs(quantity - round(quantity)) > 1e-9:
        raise HTTPException(status_code=400, detail="Transfer a whole number greater than 0")
    parent = db.query(StockItem).filter(StockItem.id == item.parent_item_id).first()
    free = ShipmentService.free_lot_quantities(db, parent.id)
    have = sum(q for _, q in free)
    if have < quantity - 1e-9:
        raise HTTPException(status_code=400, detail=f"{parent.code} has only {have:,.0f} free to transfer")
    left, made = quantity, []
    for lot, q in free:
        if left <= 1e-9:
            break
        take = min(q, left)
        lot.quantity -= take
        new = Lot(item_id=item.id, lot_code=_next_suffix(db, lot.lot_code), quantity=take, initial_quantity=take,
                  base_unit_cost=lot.base_unit_cost, unit_cost=lot.unit_cost, po_line_id=lot.po_line_id, received_date=lot.received_date,
                  status="available", source="transfer", source_reference=f"{parent.code} {lot.lot_code}", parent_lot_id=lot.id)
        db.add(new)
        db.flush()
        note = reference or f"Transfer {parent.code} -> {item.code}"
        db.add(InventoryTransaction(item_id=parent.id, lot_id=lot.id, quantity_delta=-take, type="transfer", reference=item.code, note=note, created_by=by))
        db.add(InventoryTransaction(item_id=item.id, lot_id=new.id, quantity_delta=take, type="transfer", reference=parent.code, note=note, created_by=by))
        made.append(new)
        left -= take
    parent.on_hand -= quantity
    item.on_hand += quantity
    refresh_item_cost(db, item)
    refresh_item_cost(db, parent)
    db.commit()
    return made


def to_parent(db: Session, item_id: int, quantity: float, by: str) -> float:
    """Put unbooked transferred stock back into the generic lots it came from (newest transfer first)."""
    from app.services.crud import ShipmentService, refresh_item_cost
    item = db.query(StockItem).filter(StockItem.id == item_id).first()
    if not item or not item.parent_item_id:
        raise HTTPException(status_code=400, detail="This item doesn't draw from a generic item")
    if quantity is None or quantity <= 0:
        raise HTTPException(status_code=400, detail="Return a quantity greater than 0")
    free = [(lot, q) for lot, q in ShipmentService.free_lot_quantities(db, item.id) if lot.parent_lot_id]
    have = sum(q for _, q in free)
    if have < quantity - 1e-9:
        raise HTTPException(status_code=400, detail=f"Only {have:,.0f} transferred and unbooked can go back")
    parent = db.query(StockItem).filter(StockItem.id == item.parent_item_id).first()
    left = quantity
    for lot, q in reversed(free):
        if left <= 1e-9:
            break
        take = min(q, left)
        src = db.query(Lot).filter(Lot.id == lot.parent_lot_id).first()
        lot.quantity -= take
        src.quantity += take
        db.add(InventoryTransaction(item_id=item.id, lot_id=lot.id, quantity_delta=-take, type="transfer", reference=parent.code,
                                    note=f"Returned to {parent.code}", created_by=by))
        db.add(InventoryTransaction(item_id=parent.id, lot_id=src.id, quantity_delta=take, type="transfer", reference=item.code,
                                    note=f"Returned from {item.code}", created_by=by))
        left -= take
    parent.on_hand += quantity
    item.on_hand -= quantity
    refresh_item_cost(db, item)
    refresh_item_cost(db, parent)
    db.commit()
    return quantity


def family(db: Session, item: StockItem) -> dict:
    """For an item page: its generic item (with free stock) or, for a generic one, the items drawing from it."""
    from app.services.crud import ShipmentService
    if item.parent_item_id:
        p = db.query(StockItem).filter(StockItem.id == item.parent_item_id).first()
        return {"parent": {"id": p.id, "code": p.code, "title": p.title, "free": sum(q for _, q in ShipmentService.free_lot_quantities(db, p.id))},
                "transferred_free": sum(q for lot, q in ShipmentService.free_lot_quantities(db, item.id) if lot.parent_lot_id), "children": []}
    kids = db.query(StockItem).filter(StockItem.parent_item_id == item.id).order_by(StockItem.code).all()
    return {"parent": None, "transferred_free": 0,
            "children": [{"id": k.id, "code": k.code, "title": k.title, "on_hand": k.on_hand, "booked": k.booked} for k in kids]}
