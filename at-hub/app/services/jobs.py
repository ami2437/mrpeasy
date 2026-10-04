"""Job #s live on customer orders. Stock reaches a job through the shipments that used it, so a lot -- and the PO
line it was received on -- can be found by the job it went to (search boxes on Lots, POs, MTRs, Landed Costs)."""
from typing import Dict, Set

from sqlalchemy.orm import Session

from app.models import CustomerOrder, Lot, Shipment, ShipmentLine


def jobs_by_lot(db: Session) -> Dict[int, Set[str]]:
    rows = (db.query(ShipmentLine.lot_id, CustomerOrder.job_number)
            .join(Shipment, Shipment.id == ShipmentLine.shipment_id)
            .join(CustomerOrder, CustomerOrder.id == Shipment.order_id)
            .filter(ShipmentLine.lot_id.isnot(None), CustomerOrder.job_number.isnot(None), CustomerOrder.job_number != "",
                    Shipment.status != "cancelled").distinct().all())
    out: Dict[int, Set[str]] = {}
    for lot_id, job in rows:
        out.setdefault(lot_id, set()).add(job)
    return out


def jobs_by_po(db: Session) -> Dict[int, Set[str]]:
    """PO id -> the jobs its received material shipped to."""
    from app.models import PurchaseOrderLine
    by_lot = jobs_by_lot(db)
    if not by_lot:
        return {}
    out: Dict[int, Set[str]] = {}
    for lot_id, po_id in (db.query(Lot.id, PurchaseOrderLine.po_id).join(PurchaseOrderLine, PurchaseOrderLine.id == Lot.po_line_id)
                          .filter(Lot.id.in_(by_lot.keys())).all()):
        out.setdefault(po_id, set()).update(by_lot[lot_id])
    return out
