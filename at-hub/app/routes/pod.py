"""Proof of delivery (pod.html): what a driver needs at the customer's dock and nothing more -- the shipment, who it's
for and where, the customer's PO #, the items and quantities, boxes and pallets. No prices, no other records."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import require_perm
from app.models import Attachment, Customer, CustomerOrder, Shipment, StockItem

router = APIRouter(prefix="/api/pod", tags=["pod"], dependencies=[Depends(require_perm("pod.upload"))])

SHIPPED = ("shipped", "delivered", "invoiced")


def _delivery(s: Shipment, order, customer_name: str, items: dict, pods: int) -> dict:
    qty, desc = {}, {}
    for l in s.lines:
        qty[l.order_line_id] = qty.get(l.order_line_id, 0) + l.quantity
        it = items.get(l.item_id)
        desc[l.order_line_id] = (it.code if it else "", it.title if it else "")
    pallets = {}
    for b in s.boxes:
        if b.pallet_number:
            pallets.setdefault(b.pallet_number, {"pallet_number": b.pallet_number, "boxes": 0, "weight": None, "dimensions": None})["boxes"] += 1
    for p in s.pallets:
        row = pallets.setdefault(p.pallet_number, {"pallet_number": p.pallet_number, "boxes": 0, "weight": None, "dimensions": None})
        row["weight"], row["dimensions"] = p.weight, p.dimensions
    return {"id": s.id, "code": s.code, "status": s.status, "ship_date": s.ship_date, "delivered_at": s.delivered_at,
            "customer": customer_name, "order_code": order.code if order else None, "po_number": order.po_number if order else None,
            "job_number": order.job_number if order else None, "ship_to": order.ship_to_address if order else None,
            "carrier": s.carrier, "lines": [{"item_code": desc[k][0], "description": desc[k][1], "quantity": q} for k, q in qty.items()],
            "boxes": len(s.boxes), "pallets": sorted(pallets.values(), key=lambda p: str(p["pallet_number"])), "pods": pods}


@router.get("/deliveries")
def deliveries(scope: str = "shipped", db: Session = Depends(get_db)):
    """scope=shipped: waiting for proof of delivery; scope=all: also delivered / invoiced (newest 200)."""
    q = db.query(Shipment).filter(Shipment.status.in_(("shipped",) if scope == "shipped" else SHIPPED)).order_by(Shipment.id.desc())
    ships = q.limit(200).all()
    orders = {o.id: o for o in db.query(CustomerOrder).filter(CustomerOrder.id.in_({s.order_id for s in ships} or {0})).all()}
    names = {c.id: c.name for c in db.query(Customer).filter(Customer.id.in_({o.customer_id for o in orders.values()} or {0})).all()}
    item_ids = {l.item_id for s in ships for l in s.lines}
    items = {i.id: i for i in db.query(StockItem).filter(StockItem.id.in_(item_ids or {0})).all()}
    pods = {}
    for (eid,) in db.query(Attachment.entity_id).filter(Attachment.entity_type == "shipment", Attachment.category == "pod",
                                                       Attachment.entity_id.in_([s.id for s in ships] or [0])).all():
        pods[eid] = pods.get(eid, 0) + 1
    out = []
    for s in ships:
        o = orders.get(s.order_id)
        out.append(_delivery(s, o, names.get(o.customer_id, "") if o else "", items, pods.get(s.id, 0)))
    return out
