"""Receiving history: who received what, how much and when, for one PO or across all of them.

A receipt is the lots made by one Receive click: every lot carries its PO line, quantity and landed cost, and its
"receipt" stock transaction says who did it. Lots received together (same person, same minute) are one receipt.
MRPeasy history comes in with the importer's name ("MRPeasy import")."""
from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.models import ActivityLog, InventoryTransaction, Lot, PurchaseOrder, PurchaseOrderLine, StockItem, Vendor


def receipts(db: Session, po_ids: Optional[Iterable[int]] = None, since: Optional[datetime] = None) -> list:
    """Newest first: [{po_id, po_code, vendor, when, by, lines: [{item, qty, lot, unit_cost, ...}], units}]"""
    q = (db.query(Lot, PurchaseOrderLine, PurchaseOrder).join(PurchaseOrderLine, PurchaseOrderLine.id == Lot.po_line_id)
         .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id))
    if po_ids is not None:
        q = q.filter(PurchaseOrder.id.in_(list(po_ids) or [0]))
    if since is not None:
        q = q.filter(Lot.received_date >= since)
    rows = q.all()
    if not rows:
        return []
    lot_ids = [lot.id for lot, _, _ in rows]
    who = {t.lot_id: t for t in db.query(InventoryTransaction).filter(InventoryTransaction.lot_id.in_(lot_ids),
                                                                       InventoryTransaction.type == "receipt").all()}
    items = {i.id: i for i in db.query(StockItem).filter(StockItem.id.in_({lot.item_id for lot, _, _ in rows})).all()}
    vendors = {v.id: v.name for v in db.query(Vendor).filter(Vendor.id.in_({po.vendor_id for _, _, po in rows})).all()}
    groups = {}
    for lot, line, po in rows:
        tx = who.get(lot.id)
        by = (tx.created_by if tx else None) or "unknown"
        when = lot.received_date or (tx.created_at if tx else None)
        key = (po.id, by, when.replace(second=0, microsecond=0) if when else None)
        g = groups.setdefault(key, {"po_id": po.id, "po_code": po.code, "vendor": vendors.get(po.vendor_id, ""), "when": when,
                                    "by": "MRPeasy import" if by == "mrpeasy-import" else by, "lines": [], "units": 0.0})
        it = items.get(lot.item_id)
        g["lines"].append({"lot_id": lot.id, "lot_code": lot.lot_code, "item_id": lot.item_id, "item_code": it.code if it else "",
                           "item_title": it.title if it else "", "quantity": lot.initial_quantity or 0, "left": lot.quantity,
                           "unit_cost": lot.unit_cost, "base_unit_cost": lot.base_unit_cost, "po_line_id": line.id,
                           "vendor_item_code": line.vendor_item_code, "note": tx.note if tx else None})
        g["units"] += lot.initial_quantity or 0
    out = sorted(groups.values(), key=lambda g: g["when"] or datetime.min, reverse=True)
    for g in out:
        g["lines"].sort(key=lambda l: l["item_code"])
        g["value"] = round(sum((l["quantity"] or 0) * (l["unit_cost"] or 0) for l in g["lines"]), 2)
    return out


def po_events(db: Session, po: PurchaseOrder) -> dict:
    """When / by whom the PO was marked ordered (from the activity log; older POs: the order date)."""
    row = (db.query(ActivityLog).filter(ActivityLog.entity_type == "purchase_order", ActivityLog.entity_id == po.id,
                                        ActivityLog.action == "mark-ordered").order_by(ActivityLog.at.desc()).first())
    return {"ordered_at": row.at if row else None, "ordered_by": row.by if row else None}


def recent(db: Session, days: int = 30) -> list:
    return receipts(db, since=datetime.utcnow() - timedelta(days=max(1, min(days, 3650))))
