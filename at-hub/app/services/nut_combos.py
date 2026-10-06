"""Bolts and nuts sent together as assembled units (ShipmentCombo). On a shipment, N bolts of one order line and N x ratio
nuts of another can go out combined: one row of N on the pick/pack screens, the boxes, labels and the packing list, with
the note "Bolts and nuts combined". Underneath nothing merges -- each item leaves its own lots and each order line counts
its own shipped quantity -- so the next shipment can send the rest combined or separately.

    packing_quantities(shipment) -> ({order line id: qty to box}, {id: order line}): a nut's combined part rides in its bolt's boxes
    suggestions(db, shipment)    -> pairs worth combining: a $0 line whose item is the bolt's code + -NUT/-NUTS, both booked here
    combine / uncombine          -> add / change / remove a pair (open shipments only; the packing is re-checked after)
    fit_to_bookings(shipment)    -> after an unbook, shrink combos that no longer fit (or drop them)
"""
import math
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import HTTPException

from app.models import ShipmentCombo, StockItem
from app.services.item_naming import normalize_code
from app.services.nut_pairing import bolt_code

DEFAULT_NOTE = "Bolts and nuts combined"
OPEN = ("new", "ready")
FREE = 0.005  # a line priced under half a cent is the free companion nut


def booked_by_line(shipment) -> Dict[int, float]:
    out: Dict[int, float] = {}
    for sl in shipment.lines:
        out[sl.order_line_id] = out.get(sl.order_line_id, 0) + sl.quantity
    return out


def absorbed(shipment) -> Dict[int, float]:
    """{nut order line id: its quantity inside assembled units} -- packed in the bolt's boxes, not its own."""
    out: Dict[int, float] = {}
    for c in shipment.combos:
        out[c.member_line_id] = out.get(c.member_line_id, 0) + c.member_quantity
    return out


def packing_quantities(shipment):
    """(qty to box per order line, order line by id). A nut fully inside assembled units has nothing left to box."""
    qty, lines = {}, {}
    for sl in shipment.lines:
        qty[sl.order_line_id] = qty.get(sl.order_line_id, 0) + sl.quantity
        lines[sl.order_line_id] = sl.order_line
    for lid, q in absorbed(shipment).items():
        if lid in qty:
            qty[lid] -= q
            if qty[lid] <= 1e-9:
                del qty[lid]
    return qty, lines


def combo_of(shipment, line_id: int) -> Optional[ShipmentCombo]:
    return next((c for c in shipment.combos if line_id in (c.lead_line_id, c.member_line_id)), None)


def _codes(db, shipment) -> Dict[int, str]:
    ids = {sl.item_id for sl in shipment.lines} | {sl.order_line.item_id for sl in shipment.lines if sl.order_line}
    return {i.id: i.code for i in db.query(StockItem).filter(StockItem.id.in_(ids or {0})).all()}


def _ratio(lead, member) -> int:
    """Nuts per bolt from the order itself: 2 when the nut line is exactly twice the bolt line, else 1."""
    if lead.quantity and member.quantity and abs(member.quantity - 2 * lead.quantity) < 1e-9:
        return 2
    return 1


def suggestions(db, shipment) -> List[dict]:
    """Pairs worth combining, not combined yet: the nut line is $0 (a charged nut is never suggested -- it can still be
    combined by hand) and its item code is the bolt's + -NUT / -NUTS. Quantity = as many full sets as both have booked."""
    if shipment.status not in OPEN:
        return []
    booked = booked_by_line(shipment)
    lines = {sl.order_line_id: sl.order_line for sl in shipment.lines if sl.order_line}
    codes = _codes(db, shipment)
    by_code = {}
    for ol in sorted(lines.values(), key=lambda l: (l.line_no or 0, l.id)):
        by_code.setdefault(normalize_code(codes.get(ol.item_id, "")).upper(), ol)
    out = []
    for ol in sorted(lines.values(), key=lambda l: (l.line_no or 0, l.id)):
        b = bolt_code(codes.get(ol.item_id, ""))
        lead = by_code.get(b) if b else None
        if not lead or lead.id == ol.id or (ol.unit_price or 0) >= FREE:
            continue
        if combo_of(shipment, ol.id) or combo_of(shipment, lead.id):
            continue
        ratio = _ratio(lead, ol)
        qty = min(booked.get(lead.id, 0), math.floor(booked.get(ol.id, 0) / ratio + 1e-9))
        if qty < 1:
            continue
        out.append({"lead_line_id": lead.id, "member_line_id": ol.id, "lead_line_no": lead.line_no, "member_line_no": ol.line_no,
                    "lead_code": codes.get(lead.item_id, ""), "member_code": codes.get(ol.item_id, ""), "ratio": ratio,
                    "quantity": qty, "lead_booked": booked.get(lead.id, 0), "member_booked": booked.get(ol.id, 0),
                    "note": DEFAULT_NOTE})
    return out


def _repack(db, shipment, line_ids) -> None:
    """The packing of these lines changed: their boxes go (the bolt is re-boxed as assembled units, the nut's separate
    part gets its own) and an accepted packing is re-checked before it ships."""
    for box in [b for b in shipment.boxes if b.order_line_id in line_ids]:
        shipment.boxes.remove(box)
    shipment.packed_at = shipment.packed_by = None


def _check_open(shipment) -> None:
    if shipment.status not in OPEN:
        raise HTTPException(status_code=400, detail=f"{shipment.code} is {shipment.status} -- bolts and nuts are combined before it ships")


def combine(db, shipment, data, by: str) -> ShipmentCombo:
    """Combine (or change) a bolt line + nut line on this shipment."""
    _check_open(shipment)
    booked = booked_by_line(shipment)
    lead_id, member_id = data.lead_line_id, data.member_line_id
    if lead_id == member_id:
        raise HTTPException(status_code=400, detail="Pick two different lines to combine")
    for lid, what in ((lead_id, "bolt"), (member_id, "nut")):
        if booked.get(lid, 0) <= 1e-9:
            raise HTTPException(status_code=400, detail=f"The {what} line isn't booked on {shipment.code}")
    existing = next((c for c in shipment.combos if c.lead_line_id == lead_id and c.member_line_id == member_id), None)
    for c in shipment.combos:
        if c is existing:
            continue
        if {c.lead_line_id, c.member_line_id} & {lead_id, member_id}:
            raise HTTPException(status_code=400, detail="One of these lines is already combined on this shipment -- split that first")
    ratio = data.ratio or 1
    lines = {sl.order_line_id: sl.order_line for sl in shipment.lines}
    lead, member = lines[lead_id], lines[member_id]
    if data.quantity > booked[lead_id] + 1e-9:
        raise HTTPException(status_code=400, detail=f"Line #{lead.line_no} has only {booked[lead_id]:g} booked here -- can't combine {data.quantity:g}")
    if data.quantity * ratio > booked[member_id] + 1e-9:
        raise HTTPException(status_code=400, detail=f"{data.quantity:g} sets need {data.quantity * ratio:g} of line #{member.line_no}, "
                                                    f"but only {booked[member_id]:g} is booked here")
    note = (data.note or "").strip() or DEFAULT_NOTE
    if existing:
        changed = abs(existing.quantity - data.quantity) > 1e-9 or abs((existing.ratio or 1) - ratio) > 1e-9
        existing.quantity, existing.ratio, existing.note = data.quantity, ratio, note
        if changed:
            _repack(db, shipment, {lead_id, member_id})
        combo = existing
    else:
        combo = ShipmentCombo(lead_line_id=lead_id, member_line_id=member_id, quantity=data.quantity, ratio=ratio,
                              note=note, created_by=by, created_at=datetime.utcnow())
        shipment.combos.append(combo)
        _repack(db, shipment, {lead_id, member_id})
    shipment.updated_by = by
    return combo


def uncombine(db, shipment, combo_id: int, by: str) -> None:
    _check_open(shipment)
    combo = next((c for c in shipment.combos if c.id == combo_id), None)
    if not combo:
        raise HTTPException(status_code=404, detail="That combination isn't on this shipment")
    _repack(db, shipment, {combo.lead_line_id, combo.member_line_id})
    shipment.combos.remove(combo)
    shipment.updated_by = by


def fit_to_bookings(db, shipment) -> bool:
    """After bookings shrink: a combo larger than what's still booked shrinks to fit, or goes when no full set is left.
    Returns True when something changed (its packing is then re-checked)."""
    booked = booked_by_line(shipment)
    changed = False
    for c in list(shipment.combos):
        fit = min(booked.get(c.lead_line_id, 0), math.floor(booked.get(c.member_line_id, 0) / (c.ratio or 1) + 1e-9))
        if fit >= c.quantity - 1e-9:
            continue
        changed = True
        _repack(db, shipment, {c.lead_line_id, c.member_line_id})
        if fit < 1:
            shipment.combos.remove(c)
        else:
            c.quantity = float(fit)
    return changed


def fully_combined_lines(shipment) -> set:
    """Nut lines with nothing of their own to box (fully inside assembled units) -- left off pallet item lists."""
    qty, _ = packing_quantities(shipment)
    return {c.member_line_id for c in shipment.combos if c.member_line_id not in qty}
