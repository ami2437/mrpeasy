import re
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import (
    StockItemCreate, StockItemUpdate, StockItemResponse, InventoryTransactionResponse,
    BulkPackSizeRequest, BulkPackSizeResult, ProductGroupCreate, ProductGroupResponse, PriceHistoryEntry, PackSizeHistoryEntry,
)
from fastapi import Response
from app.services.crud import StockItemService, InventoryTransactionService, ProductGroupService, price_history
from app.dependencies import get_current_active_user, require_any, require_perm
from app.services.permissions import has
from app.models import User

router = APIRouter(prefix="/api/stock-items", tags=["stock-items"], dependencies=[Depends(require_any("stock.view", "orders.view", "shipments.view", "shipments.work", "purchasing", "quotes", "invoices"))])


@router.get("/", response_model=list[StockItemResponse])
def list_items(q: str | None = Query(None), low_stock_only: bool = Query(False), db: Session = Depends(get_db)):
    return StockItemService.list(db, q=q, low_stock_only=low_stock_only)


@router.post("/", response_model=StockItemResponse, dependencies=[Depends(require_perm("stock.edit"))])
def create_item(data: StockItemCreate, db: Session = Depends(get_db)):
    return StockItemService.create(db, data)


@router.get("/activity/recent", response_model=list[InventoryTransactionResponse])
def recent_activity(limit: int = Query(25), db: Session = Depends(get_db)):
    return InventoryTransactionService.recent(db, limit=limit)


@router.post("/pack-sizes/bulk", response_model=BulkPackSizeResult, dependencies=[Depends(require_any("stock.edit", "shipments.work"))])
def bulk_pack_sizes(data: BulkPackSizeRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Paste-a-list bulk update of default_pack_size by item code. Old sizes are kept in the history."""
    return StockItemService.bulk_set_pack_sizes(db, data.entries, by=current_user.username,
                                                source=data.source or "bulk paste", reference=data.reference)


@router.delete("/pack-sizes/history/{entry_id}", status_code=204, dependencies=[Depends(require_any("stock.edit", "shipments.work"))])
def delete_pack_size_history(entry_id: int, db: Session = Depends(get_db)):
    """Remove an old pack size from an item's history (the current default is untouched)."""
    StockItemService.delete_pack_size_history(db, entry_id)
    return Response(status_code=204)


@router.get("/pack-sizes/history", response_model=list[PackSizeHistoryEntry])
def pack_size_history(item_id: int | None = Query(None), db: Session = Depends(get_db)):
    """Every pack size change, newest first (one item with ?item_id=)."""
    return StockItemService.pack_size_history(db, item_id)


@router.get("/groups/list", response_model=list[ProductGroupResponse])
def list_groups(db: Session = Depends(get_db)):
    return ProductGroupService.list(db)


@router.post("/groups/list", response_model=ProductGroupResponse, dependencies=[Depends(require_perm("stock.edit"))])
def create_group(data: ProductGroupCreate, db: Session = Depends(get_db)):
    return ProductGroupService.create(db, data.name)


@router.post("/groups/{group_id}/merge", dependencies=[Depends(require_perm("stock.edit"))])
def merge_group(group_id: int, into_id: int = Query(...), db: Session = Depends(get_db)):
    """Move all of a group's items into another group and remove it."""
    return ProductGroupService.merge(db, group_id, into_id)


@router.delete("/groups/{group_id}", status_code=204, dependencies=[Depends(require_perm("stock.edit"))])
def delete_group(group_id: int, db: Session = Depends(get_db)):
    ProductGroupService.delete(db, group_id)
    return Response(status_code=204)


@router.get("/{item_id}/price-history", response_model=list[PriceHistoryEntry], dependencies=[Depends(require_perm("money.view"))])
def item_price_history(item_id: int, db: Session = Depends(get_db)):
    """All sale and purchase prices for this item, newest first."""
    StockItemService.get(db, item_id)
    return price_history(db, item_id)


@router.delete("/{item_id}", status_code=204, dependencies=[Depends(require_perm("stock.edit"))])
def delete_item(item_id: int, db: Session = Depends(get_db)):
    """Only for an item that was never used; a used one gets 409 'USED|...' and should be archived instead."""
    StockItemService.delete(db, item_id)
    return Response(status_code=204)


@router.post("/{item_id}/verify", response_model=StockItemResponse, dependencies=[Depends(require_perm("stock.edit"))])
def verify_item(item_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return StockItemService.verify(db, item_id, current_user.username)


@router.get("/{item_id}/usage", dependencies=[Depends(require_perm("stock.edit"))])
def item_usage(item_id: int, db: Session = Depends(get_db)):
    """Where the item is used (decides delete vs archive)."""
    from app.services.crud import _item_references
    StockItemService.get(db, item_id)
    return _item_references(db, item_id)


@router.get("/{item_id}", response_model=StockItemResponse)
def get_item(item_id: int, db: Session = Depends(get_db)):
    return StockItemService.get(db, item_id)


@router.put("/{item_id}", response_model=StockItemResponse)
def update_item(item_id: int, data: StockItemUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    if not has(current_user, "stock.edit") and (not has(current_user, "shipments.work") or set(data.model_dump(exclude_unset=True)) - {"default_pack_size"}):
        raise HTTPException(status_code=403, detail="Your role can only change an item's pack size")
    return StockItemService.update(db, item_id, data, created_by=current_user.username)


@router.get("/analytics/summary")
def analytics(days: int = Query(90, ge=1, le=3650), limit: int = Query(15, ge=1, le=100), db: Session = Depends(get_db)):
    """Top sellers (by units shipped and by revenue) and slow movers over the last `days`."""
    from datetime import datetime, timedelta
    from sqlalchemy import func
    from app.models import InventoryTransaction, ShipmentLine, Shipment, StockItem, CustomerOrder
    since = datetime.utcnow() - timedelta(days=days)
    items = {i.id: i for i in db.query(StockItem).all()}
    rows = (db.query(ShipmentLine.item_id, func.sum(ShipmentLine.picked_quantity),
                     func.sum(ShipmentLine.picked_quantity * ShipmentLine.unit_price),
                     func.count(func.distinct(CustomerOrder.customer_id)), func.count(func.distinct(Shipment.order_id)))
            .join(Shipment, Shipment.id == ShipmentLine.shipment_id)
            .join(CustomerOrder, CustomerOrder.id == Shipment.order_id)
            .filter(Shipment.ship_date >= since, Shipment.status != "cancelled")
            .group_by(ShipmentLine.item_id).all())
    sold = [{"item_id": iid, "code": items[iid].code if iid in items else iid, "title": items[iid].title if iid in items else "",
             "category": items[iid].category if iid in items else None,
             "units": round(q or 0), "revenue": round(rev or 0, 2), "customers": nc, "orders": no,
             "on_hand": items[iid].on_hand if iid in items else None} for iid, q, rev, nc, no in rows if (q or 0) > 0]
    moved = {iid for (iid,) in db.query(InventoryTransaction.item_id).filter(InventoryTransaction.created_at >= since,
                                                                           InventoryTransaction.quantity_delta < 0).distinct()}
    last_out = dict(db.query(InventoryTransaction.item_id, func.max(InventoryTransaction.created_at))
                    .filter(InventoryTransaction.quantity_delta < 0).group_by(InventoryTransaction.item_id).all())
    slow = sorted([{"item_id": i.id, "code": i.code, "title": i.title, "on_hand": i.on_hand,
                    "last_out": last_out[i.id].isoformat() if last_out.get(i.id) else None}
                   for i in items.values() if (i.on_hand or 0) > 0 and i.id not in moved],
                  key=lambda r: -(r["on_hand"] or 0))
    return {
        "days": days,
        "top_units": sorted(sold, key=lambda r: -r["units"])[:limit],
        "top_revenue": sorted(sold, key=lambda r: -r["revenue"])[:limit],
        "slow_movers": slow[:limit],
        "totals": {"units": sum(r["units"] for r in sold), "revenue": round(sum(r["revenue"] for r in sold), 2), "items_sold": len(sold)},
    }


_CODE = re.compile(r"[A-Za-z][\w-]*\d[\w-]*")


def _attach_links(db: Session, movements: list) -> None:
    """Each movement's documents, from the codes in its reference and note -- shipment (+ its order and
    the customer's PO #), purchase order, customer order, invoice, other item -- so the page can link them."""
    from app.models import CustomerOrder, Invoice, PurchaseOrder, Shipment, StockItem
    tokens = {tok.rstrip("-") for m in movements for tok in _CODE.findall(f"{m['reference'] or ''} {m['note'] or ''}")}
    if not tokens:
        return
    found = {}
    for kind, model in (("shipment", Shipment), ("purchase_order", PurchaseOrder), ("customer_order", CustomerOrder),
                        ("invoice", Invoice), ("item", StockItem)):
        for rec in db.query(model).filter(model.code.in_(tokens)).all():
            found.setdefault(rec.code, (kind, rec))
    orders = {o.id: o for o in db.query(CustomerOrder).filter(CustomerOrder.id.in_(
        {rec.order_id for kind, rec in found.values() if kind == "shipment"})).all()}
    for m in movements:
        links, seen = [], set()

        def add(kind, rid, code, label=None):
            if (kind, rid) not in seen:
                seen.add((kind, rid))
                links.append({"kind": kind, "id": rid, "code": code, "label": label or code})
        for tok in _CODE.findall(f"{m['reference'] or ''} {m['note'] or ''}"):
            hit = found.get(tok.rstrip("-"))
            if not hit:
                continue
            kind, rec = hit
            if kind == "item":
                m["ref_item_id"] = rec.id if rec.code == m["reference"] else m.get("ref_item_id")
                continue
            add(kind, rec.id, rec.code)
            if kind == "shipment" and rec.order_id in orders:
                o = orders[rec.order_id]
                add("customer_order", o.id, o.code)
                if o.po_number and ("cust_po", o.id) not in seen:
                    seen.add(("cust_po", o.id))
                    links.append({"kind": "customer_order", "id": o.id, "code": o.code, "label": f"Cust PO {o.po_number}", "po": True})
        m["links"] = links


@router.get("/{item_id}/movements")
def movements(item_id: int, limit: int = Query(500, ge=1, le=5000), db: Session = Depends(get_db)):
    """Every stock movement of one item, newest first, with the running on-hand balance
    and the lot it touched."""
    from app.models import InventoryTransaction, Lot
    item = StockItemService.get(db, item_id)
    txs = (db.query(InventoryTransaction).filter(InventoryTransaction.item_id == item_id)
           .order_by(InventoryTransaction.created_at.desc(), InventoryTransaction.id.desc()).limit(limit).all())
    lots = {l.id: l for l in db.query(Lot).filter(Lot.id.in_({t.lot_id for t in txs if t.lot_id}))} if txs else {}
    balance = item.on_hand or 0
    out = []
    for t in txs:
        lot = lots.get(t.lot_id)
        src = lot.parent_lot if lot is not None and lot.parent_lot_id else None  # drawn from generic stock
        out.append({"id": t.id, "date": t.created_at.isoformat() if t.created_at else None, "type": t.type,
                    "quantity": t.quantity_delta, "balance": round(balance, 4), "lot": lot.lot_code if lot else None,
                    "unit_cost": lot.unit_cost if lot else None,
                    "from_item_id": src.item_id if src else None, "from_item_code": src.item.code if src and src.item else None,
                    "from_lot": src.lot_code if src else None,
                    "reference": t.reference, "note": t.note, "by": t.created_by})
        balance -= t.quantity_delta
    _attach_links(db, out)
    total_in = sum(t.quantity_delta for t in txs if t.quantity_delta > 0)
    total_out = -sum(t.quantity_delta for t in txs if t.quantity_delta < 0)
    return {"item_id": item.id, "code": item.code, "title": item.title, "on_hand": item.on_hand,
            "total_in": total_in, "total_out": total_out, "movements": out}


# ---- generic stock: transfer from / back to the generic item this one draws from ----
from pydantic import BaseModel as _BM  # noqa: E402


class _Qty(_BM):
    quantity: float
    reference: Optional[str] = None
    source_id: Optional[int] = None  # generic item to draw from (default: the linked one)


class _Ids(_BM):
    item_ids: list[int]


@router.post("/generic-sources")
def generic_sources(data: _Ids, db: Session = Depends(get_db)):
    """For the order screen's Book column: {item_id: [generic items it can draw from, best first]} (empty when switched off)."""
    from app.models import StockItem
    from app.services import stock_transfer
    if not stock_transfer.enabled(db):
        return {}
    items = db.query(StockItem).filter(StockItem.id.in_(set(data.item_ids))).all()
    return {i.id: s for i in items if (s := stock_transfer.sources(db, i))}


@router.get("/{item_id}/family")
def item_family(item_id: int, db: Session = Depends(get_db)):
    from app.services import stock_transfer
    return stock_transfer.family(db, StockItemService.get(db, item_id))


@router.post("/{item_id}/transfer-from-parent", dependencies=[Depends(require_perm("stock.edit"))])
def transfer_from_parent(item_id: int, data: _Qty, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    from app.services import stock_transfer
    lots = stock_transfer.from_parent(db, item_id, data.quantity, current_user.username, data.reference, data.source_id)
    return {"lots": [{"id": l.id, "lot_code": l.lot_code, "quantity": l.quantity} for l in lots]}


@router.post("/{item_id}/return-to-parent", dependencies=[Depends(require_perm("stock.edit"))])
def return_to_parent(item_id: int, data: _Qty, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    from app.services import stock_transfer
    return {"returned": stock_transfer.to_parent(db, item_id, data.quantity, current_user.username)}
