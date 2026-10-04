"""Generic stock shared by specific items: bulk 5/8-11 2H HDG nuts bought by the hundred thousand (58-NUT),
used by 15420-NUT, 15421-NUT, ... A generic item is flagged is_generic; a specific item draws from one it
is linked to (parent_item_id), or from any generic item whose size and spec match its title.

Drawing (transfer): free stock leaves the generic item's lots (oldest first) and becomes new lots on the
specific item, coded <generic lot>-T1, -T2 ..., each keeping the generic lot's cost, its PO line (so landed
costs and MTRs still trace) and a link back (parent_lot_id). In movement history the generic item reads like
the supplier: "transfer +3,800 from 58-NUT lot L00512". The first draw remembers the link for next time.
Return to generic reverses unbooked transferred stock.

Booking can draw the shortfall in the same step (create_shipment, draw_from_item_id). Everything after that
-- booking, shipping, costing -- keeps working lot by lot as before.

Company Settings -> Generic stock off: no offers, no draws (transferred lots stay ordinary lots)."""
import re
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import InventoryTransaction, Lot, StockItem


def enabled(db: Session) -> bool:
    from app.services.crud import get_company_profile
    return bool(getattr(get_company_profile(db), "generic_stock_enabled", True))


def _require_enabled(db: Session) -> None:
    if not enabled(db):
        raise HTTPException(status_code=400, detail="Generic stock is switched off (Company Settings)")


# ---- size / spec of a nut, from its title ----
_WAX = re.compile(r"WAX", re.I)
_HEAVY = re.compile(r"(?<![A-Z])(HVY|HEAVY)(?![A-Z])", re.I)
# 5/8-11, 1-1/2"-8, 3/4" - 10 -- but not the "194-2" inside A194-2H
_SIZE = re.compile(r"(?<![\dA-Za-z/.])(\d+-\d+/\d+|\d+/\d+|\d+)\s*\"?\s*-\s*(\d+)(?![\d/A-WYZa-wyz])")


def spec(title: str) -> dict:
    """{dia, tpi, grade, heavy, finish, wax} from a title like '5/8-11 A194 GR 2H HVY HEX NUT DOM. HDG'.
    Missing parts are None (titles often leave the finish out)."""
    from app.services.item_match import features
    from app.services.item_naming import _nut_grade
    t = title or ""
    f = features(t)
    fin = f["finish"] - {"galv"} or f["finish"]  # hdg / mechgalv beat the generic "galv"
    size = _SIZE.search(t.replace("_", " "))
    return {"dia": size.group(1) if size else None, "tpi": size.group(2) if size else None,
            "grade": _nut_grade(t.replace("GR 2H", "2H").replace("GR2H", "2H")), "heavy": bool(_HEAVY.search(t)),
            "finish": min(fin) if fin else None, "wax": bool(_WAX.search(t)),
            # a nut itself -- not a bolt "w/ A194-2H HEX NUT"
            "nut": "nut" in f["type"] and not f["type"] & {"bolt", "stud", "screw", "rod", "washer", "anchor"}}


def match(a: dict, g: dict) -> Optional[str]:
    """How the specific item `a` fits generic `g`: "exact", "check" (a part is unstated on one side), or None."""
    if not (a["nut"] and g["nut"]):
        return None
    if not (a["dia"] and a["tpi"] and a["grade"]) or (a["dia"], a["tpi"], a["grade"]) != (g["dia"], g["tpi"], g["grade"]):
        return None
    if a["wax"] != g["wax"]:  # wax-dipped is a different part
        return None
    if a["finish"] and g["finish"] and a["finish"] != g["finish"]:
        return None
    return "exact" if a["finish"] == g["finish"] and a["heavy"] == g["heavy"] else "check"


def sources(db: Session, item: StockItem) -> List[dict]:
    """Generic items this item can draw from, best first: its linked one, then size/spec matches."""
    from app.services.crud import ShipmentService
    if not enabled(db) or item.is_generic:
        return []
    out, seen = [], set()

    def add(g: StockItem, how: str, why: str):
        free = sum(q for _, q in ShipmentService.free_lot_quantities(db, g.id))
        out.append({"id": g.id, "code": g.code, "title": g.title, "free": free, "cost": g.cost_price, "match": how, "why": why})
        seen.add(g.id)

    if item.parent_item_id:
        p = db.query(StockItem).filter(StockItem.id == item.parent_item_id).first()
        if p:
            add(p, "linked", "linked on the item page / used before")
    mine = spec(item.title)
    if mine["dia"]:
        for g in db.query(StockItem).filter(StockItem.is_generic == True, StockItem.is_active == True).all():  # noqa: E712
            if g.id in seen or g.id == item.id:
                continue
            how = match(mine, spec(g.title))
            if how:
                add(g, how, "same size, thread and grade" + ("" if how == "exact" else " -- check the finish"))
    return out


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
    if item.is_generic or db.query(StockItem).filter(StockItem.parent_item_id == item.id).first():
        raise HTTPException(status_code=400, detail=f"{item.code} is a generic item other items draw from, so it can't draw from one")
    parent.is_generic = True  # whatever others draw from is generic stock
    return parent.id


def _next_suffix(db: Session, parent_lot_code: str) -> str:
    codes = {c for (c,) in db.query(Lot.lot_code).filter(Lot.lot_code.like(f"{parent_lot_code}-T%")).all()}
    n = 1
    while f"{parent_lot_code}-T{n}" in codes:
        n += 1
    return f"{parent_lot_code}-T{n}"


def draw(db: Session, item: StockItem, source_id: Optional[int], quantity: float, by: str,
         reference: str = None, commit: bool = True) -> List[Lot]:
    """Transfer `quantity` from generic item `source_id` (default: the linked one) into new -T lots on `item`."""
    from app.services.crud import ShipmentService, refresh_item_cost
    _require_enabled(db)
    source_id = source_id or item.parent_item_id
    if not source_id:
        raise HTTPException(status_code=400, detail=f"{item.code} has no generic item to draw from")
    if quantity is None or quantity <= 0 or abs(quantity - round(quantity)) > 1e-9:
        raise HTTPException(status_code=400, detail="Transfer a whole number greater than 0")
    parent = db.query(StockItem).filter(StockItem.id == source_id).first()
    if not parent or parent.id == item.id:
        raise HTTPException(status_code=400, detail="That generic item doesn't exist")
    if parent.id != item.parent_item_id and not parent.is_generic:
        raise HTTPException(status_code=400, detail=f"{parent.code} isn't marked as generic stock")
    if item.is_generic:
        raise HTTPException(status_code=400, detail=f"{item.code} is generic stock itself")
    free = ShipmentService.free_lot_quantities(db, parent.id)
    have = sum(q for _, q in free)
    if have < quantity - 1e-9:
        raise HTTPException(status_code=400, detail=f"{parent.code} has only {have:,.0f} free to transfer to {item.code} (need {quantity:,.0f})")
    left, made = quantity, []
    note = f"Transfer {parent.code} -> {item.code}" + (f" ({reference})" if reference else "")
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
        db.add(InventoryTransaction(item_id=parent.id, lot_id=lot.id, quantity_delta=-take, type="transfer", reference=item.code, note=note, created_by=by))
        db.add(InventoryTransaction(item_id=item.id, lot_id=new.id, quantity_delta=take, type="transfer", reference=parent.code, note=note, created_by=by))
        made.append(new)
        left -= take
    parent.on_hand -= quantity
    item.on_hand += quantity
    if not item.parent_item_id:
        item.parent_item_id = parent.id  # remembered: next time it's the first offer
    refresh_item_cost(db, item)
    refresh_item_cost(db, parent)
    if commit:
        db.commit()
    else:
        db.flush()
    return made


def from_parent(db: Session, item_id: int, quantity: float, by: str, reference: str = None, source_id: Optional[int] = None) -> List[Lot]:
    item = db.query(StockItem).filter(StockItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return draw(db, item, source_id, quantity, by, reference)


def to_parent(db: Session, item_id: int, quantity: float, by: str) -> float:
    """Put unbooked transferred stock back into the generic lots it came from (newest transfer first)."""
    from app.services.crud import ShipmentService, refresh_item_cost
    item = db.query(StockItem).filter(StockItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if quantity is None or quantity <= 0:
        raise HTTPException(status_code=400, detail="Return a quantity greater than 0")
    free = [(lot, q) for lot, q in ShipmentService.free_lot_quantities(db, item.id) if lot.parent_lot_id]
    have = sum(q for _, q in free)
    if have < quantity - 1e-9:
        raise HTTPException(status_code=400, detail=f"Only {have:,.0f} transferred and unbooked can go back")
    left = quantity
    touched = set()
    for lot, q in reversed(free):
        if left <= 1e-9:
            break
        take = min(q, left)
        src = db.query(Lot).filter(Lot.id == lot.parent_lot_id).first()
        parent = db.query(StockItem).filter(StockItem.id == src.item_id).first()
        lot.quantity -= take
        src.quantity += take
        parent.on_hand += take
        touched.add(parent.id)
        db.add(InventoryTransaction(item_id=item.id, lot_id=lot.id, quantity_delta=-take, type="transfer", reference=parent.code,
                                    note=f"Returned to {parent.code}", created_by=by))
        db.add(InventoryTransaction(item_id=parent.id, lot_id=src.id, quantity_delta=take, type="transfer", reference=item.code,
                                    note=f"Returned from {item.code}", created_by=by))
        left -= take
    item.on_hand -= quantity
    refresh_item_cost(db, item)
    for pid in touched:
        refresh_item_cost(db, db.query(StockItem).filter(StockItem.id == pid).first())
    db.commit()
    return quantity


def family(db: Session, item: StockItem) -> dict:
    """For an item page: the generic items it can draw from, or -- for a generic one -- the items that do."""
    from app.services.crud import ShipmentService
    on = enabled(db)
    transferred = sum(q for lot, q in ShipmentService.free_lot_quantities(db, item.id) if lot.parent_lot_id)
    if item.is_generic:
        kids = db.query(StockItem).filter(StockItem.parent_item_id == item.id).order_by(StockItem.code).all()
        g = spec(item.title)
        linked = {k.id for k in kids}
        matching = [k for k in db.query(StockItem).filter(StockItem.is_generic == False, StockItem.is_active == True).all()  # noqa: E712
                    if k.id not in linked and k.id != item.id and match(spec(k.title), g)] if g["dia"] else []
        row = lambda k: {"id": k.id, "code": k.code, "title": k.title, "on_hand": k.on_hand, "booked": k.booked}  # noqa: E731
        return {"enabled": on, "is_generic": True, "parent": None, "sources": [], "transferred_free": 0,
                "free": sum(q for _, q in ShipmentService.free_lot_quantities(db, item.id)),
                "children": [row(k) for k in kids], "matching": [row(k) for k in sorted(matching, key=lambda k: k.code)]}
    return {"enabled": on, "is_generic": False, "parent_item_id": item.parent_item_id, "sources": sources(db, item),
            "transferred_free": transferred, "children": [], "matching": []}
