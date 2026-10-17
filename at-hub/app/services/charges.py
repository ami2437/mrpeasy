"""PO charges (freight, customs, duty, dispatch...) carried into the goods' cost as landed costs (user, 2026-10-17).

A charge on a PO is what the vendor (or a broker / forwarder) billed: it's part of the PO total, so payments reconcile. Its
COST belongs on the goods it came with: a landed cost spread over PO lines -- by quantity (every unit the same share) or
by value (each line's share of the dollars; the default for customs / duty, which are charged on value) -- so every lot
carries "purchase $0.15 + freight $0.03 + customs $0.02 = $0.20".

  - a charge on a PO that has item lines goes onto THAT PO's items by itself (auto_apply)
  - or anywhere: apply() to any PO(s) -- the container's other POs, or the goods of a charges-only PO -- changeable later
  - or left as a PO cost only (skip)
The landed cost remembers its charge (source_charge_id) and the charge its landed cost, so each shows the other, and the
money is counted once: the charge is the spend, the landed cost only moves its cost onto the goods.
Charges imported from MRPeasy are never applied by themselves -- they're listed (unapplied) for the user to decide."""
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import LandedCost, PurchaseOrder, PurchaseOrderCharge, Vendor

VALUE_TYPES = {"customs", "duty", "tariff", "brokerage"}
IMPORTED = "mrpeasy-import"


def default_split(charge_type: str) -> str:
    return "value" if charge_type in VALUE_TYPES else "quantity"


def lc_type(db: Session, charge_type: str) -> str:
    from app.services import type_lists
    keys = type_lists.keys(db, "landed_cost")
    if charge_type in keys:
        return charge_type
    for k in ("freight", "other"):
        if k in keys:
            return k
    return next(iter(keys))


def _has_lines(po: PurchaseOrder) -> bool:
    return any((l.quantity or 0) > 0 for l in po.lines)


def _lc(db, charge) -> Optional[LandedCost]:
    return db.get(LandedCost, charge.landed_cost_id) if charge.landed_cost_id else None


def apply(db: Session, charge: PurchaseOrderCharge, po_ids: List[int], split: str = None, by: str = None) -> LandedCost:
    """Carry this charge into the cost of the goods on these POs (its landed cost is made, or re-spread)."""
    from app.services.crud import LandedCostService, generate_code, recompute_lot_costs
    from app.services.type_lists import label_of as _label
    if split not in (None, "quantity", "value"):
        raise HTTPException(status_code=400, detail="Spread it by quantity or by value")
    po = db.get(PurchaseOrder, charge.po_id)
    split = split or default_split(charge.charge_type)
    lc = _lc(db, charge)
    old_lines = [a.po_line_id for a in lc.allocations] if lc else []
    if lc:
        lc.allocations.clear()
        db.flush()
        lc.amount, lc.split, lc.cost_type = charge.amount, split, lc_type(db, charge.charge_type)
    else:
        vendor = db.get(Vendor, po.vendor_id) if po else None
        what = _label(db, "charge", charge.charge_type) if charge.charge_type else "Charge"
        lc = LandedCost(code=generate_code(db, LandedCost, "LC"), cost_type=lc_type(db, charge.charge_type), amount=charge.amount,
                        description=f"{what} on {po.code}" + (f" (invoice {charge.bill_number})" if charge.bill_number else ""),
                        paid_to=vendor.name if vendor else None, reference=charge.bill_number, cost_date=charge.created_at,
                        notes=charge.description, split=split, source_charge_id=charge.id, created_by=by)
        db.add(lc)
        db.flush()
    new_lines = LandedCostService._allocate(db, lc, po_ids)
    charge.landed_cost_id, charge.lc_status = lc.id, None
    recompute_lot_costs(db, old_lines + new_lines)
    return lc


def auto_apply(db: Session, charge: PurchaseOrderCharge, by: str = None) -> None:
    """A new charge on a PO with item lines: onto that PO's own items."""
    if charge.landed_cost_id or charge.lc_status == "skip" or (charge.amount or 0) <= 0 or charge.created_by == IMPORTED:
        return
    po = db.get(PurchaseOrder, charge.po_id)
    if po and po.status != "cancelled" and _has_lines(po):
        apply(db, charge, [po.id], None, by)


def drop(db: Session, charge: PurchaseOrderCharge) -> None:
    """Take this charge's cost back off the goods (its landed cost goes)."""
    from app.services.crud import recompute_lot_costs
    lc = _lc(db, charge)
    if lc:
        lines = [a.po_line_id for a in lc.allocations]
        db.delete(lc)
        db.flush()
        recompute_lot_costs(db, lines)
    charge.landed_cost_id = None


def skip(db: Session, charge: PurchaseOrderCharge) -> None:
    drop(db, charge)
    charge.lc_status = "skip"


def respread_po(db: Session, po: PurchaseOrder, by: str = None) -> None:
    """The PO's lines changed: its charges' landed costs follow (same POs, same split); unapplied new charges go on now."""
    for c in list(po.charges):
        lc = _lc(db, c)
        if lc:
            po_ids = sorted({a.po_id for a in lc.allocations}) or [po.id]
            pos = [db.get(PurchaseOrder, i) for i in po_ids]
            if any(p and _has_lines(p) for p in pos):
                apply(db, c, [p.id for p in pos if p and _has_lines(p)], lc.split, by)
            else:
                drop(db, c)
        else:
            auto_apply(db, c, by)


def charge_out(db: Session, c: PurchaseOrderCharge) -> dict:
    lc = _lc(db, c)
    po_codes = []
    if lc:
        ids = sorted({a.po_id for a in lc.allocations})
        po_codes = [p.code for p in db.query(PurchaseOrder).filter(PurchaseOrder.id.in_(ids or [-1])).all()]
    return {"landed_cost_id": lc.id if lc else None, "landed_cost_code": lc.code if lc else None, "split": lc.split if lc else None,
            "applied_to": po_codes, "lc_status": c.lc_status}


def unapplied(db: Session) -> dict:
    """What hasn't reached any goods' cost: charges with no landed cost (new ones, and the imported ones to decide on)
    and landed costs spread over nothing (or only over cancelled POs)."""
    out_new, out_past = [], []
    pos = {p.id: p for p in db.query(PurchaseOrder).filter(PurchaseOrder.status != "cancelled").all()}
    vendors = dict(db.query(Vendor.id, Vendor.name).all())
    for c in db.query(PurchaseOrderCharge).filter(PurchaseOrderCharge.landed_cost_id.is_(None)).all():
        po = pos.get(c.po_id)
        if not po or c.lc_status == "skip" or (c.amount or 0) <= 0:
            continue
        row = {"charge_id": c.id, "po_id": po.id, "po_code": po.code, "vendor": vendors.get(po.vendor_id, ""), "charge_type": c.charge_type,
               "amount": c.amount, "description": c.description, "bill_number": c.bill_number, "po_status": po.status,
               "has_lines": _has_lines(po), "created_at": c.created_at.isoformat() if c.created_at else None}
        (out_past if c.created_by == IMPORTED else out_new).append(row)
    lcs = []
    for lc in db.query(LandedCost).all():
        live = [a for a in lc.allocations if a.po_id in pos]
        if not live:
            lcs.append({"id": lc.id, "code": lc.code, "description": lc.description, "amount": lc.amount, "cost_type": lc.cost_type,
                        "why": "on cancelled POs only" if lc.allocations else "not applied to any PO"})
    return {"charges": out_new, "past": out_past, "landed_costs": lcs}
