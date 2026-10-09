"""Vendor shipments: the PO's "Shipped" stage -- the goods left the vendor's warehouse (carrier, tracking, ETA...).

A PO can ship in several. A shipment is in transit until its goods are received, then it completes itself:
receipts are counted against the shipments in the order they shipped (settle), so a partial receipt completes the
first shipment and leaves the next one in transit. A shipment without lines stands for everything still to come
on the PO and completes when the whole PO is received. While something is in transit and nothing is received yet,
the PO's status is "shipped" (PurchaseOrderService.refresh_status)."""
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import PurchaseOrder, VendorShipment, VendorShipmentLine
from app.services import clock

TEXT = ("carrier", "ship_mode", "tracking_number", "pro_number", "bol_number", "container_number", "vendor_ref",
        "freight_terms", "package_type", "note")


def _goods(po):
    return [l for l in po.lines if not getattr(l, "is_charge", False)]


def settle(po: PurchaseOrder, when: datetime = None) -> None:
    """Mark each shipment received / in transit from what's been received on the PO."""
    if not po.vendor_shipments:
        return
    goods = _goods(po)
    all_in = bool(goods) and all((l.received_quantity or 0) >= l.quantity - 1e-9 for l in goods)
    left = {l.id: l.received_quantity or 0 for l in goods}
    order = sorted(po.vendor_shipments, key=lambda s: (s.shipped_date or s.created_at or datetime.min, s.id or 0))
    for s in order:
        if s.lines:
            done = True
            for sl in s.lines:
                have = left.get(sl.po_line_id, 0)
                done = done and have >= sl.quantity - 1e-9
                left[sl.po_line_id] = max(0.0, have - sl.quantity)
        else:
            done = all_in
        done = done or all_in
        if done and s.status != "received":
            s.status, s.received_at = "received", when or datetime.utcnow()
        elif not done and s.status == "received":
            s.status, s.received_at = "in_transit", None  # a receipt was undone


def in_transit(po: PurchaseOrder) -> list:
    return [s for s in po.vendor_shipments if s.status == "in_transit"]


def _apply(db: Session, po: PurchaseOrder, s: VendorShipment, data) -> None:
    d = data.dict()
    for k in TEXT:
        setattr(s, k, (d.get(k) or "").strip() or None)
    s.shipped_date = clock.calendar_from_input(d["shipped_date"]) if d.get("shipped_date") else clock.today()
    s.eta = clock.calendar_from_input(d["eta"]) if d.get("eta") else None
    if s.eta and s.shipped_date and s.eta < s.shipped_date:
        raise HTTPException(status_code=400, detail="The ETA is before the ship date")
    s.packages = d.get("packages") if d.get("packages") else None
    s.weight = d.get("weight") if d.get("weight") else None
    by_id = {l.id: l for l in _goods(po)}
    s.lines.clear()
    db.flush()
    for ln in d.get("lines") or []:
        if not ln["quantity"] or ln["quantity"] <= 0:
            continue
        if ln["po_line_id"] not in by_id:
            raise HTTPException(status_code=400, detail="That line isn't on this purchase order")
        s.lines.append(VendorShipmentLine(po_line_id=ln["po_line_id"], quantity=ln["quantity"]))
    if d.get("update_expected", True) and s.eta:
        po.expected_date = s.eta


def _po(db: Session, po_id: int) -> PurchaseOrder:
    from app.services.crud import PurchaseOrderService
    return PurchaseOrderService.get(db, po_id)


def _done(db: Session, po: PurchaseOrder) -> PurchaseOrder:
    from app.services.crud import PurchaseOrderService
    db.flush()
    db.expire(po, ["vendor_shipments"])
    PurchaseOrderService.refresh_status(po)
    db.commit()
    db.refresh(po)
    return po


def create(db: Session, po_id: int, data, created_by: str) -> PurchaseOrder:
    po = _po(db, po_id)
    if po.status == "validation":
        raise HTTPException(status_code=400, detail=f"{po.code} was quick-captured -- validate it first")
    if po.status in ("received", "cancelled"):
        raise HTTPException(status_code=400, detail=f"{po.code} is {po.status} -- nothing left to ship")
    if po.status == "draft":  # it shipped, so it was ordered
        po.status = "ordered"
    s = VendorShipment(po_id=po.id, created_by=created_by, status="in_transit")
    db.add(s)
    _apply(db, po, s, data)
    return _done(db, po)


def _get(db: Session, po: PurchaseOrder, sid: int) -> VendorShipment:
    s = db.query(VendorShipment).filter(VendorShipment.id == sid, VendorShipment.po_id == po.id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Vendor shipment not found")
    return s


def update(db: Session, po_id: int, sid: int, data) -> PurchaseOrder:
    po = _po(db, po_id)
    _apply(db, po, _get(db, po, sid), data)
    return _done(db, po)


def delete(db: Session, po_id: int, sid: int) -> PurchaseOrder:
    po = _po(db, po_id)
    db.delete(_get(db, po, sid))
    return _done(db, po)
