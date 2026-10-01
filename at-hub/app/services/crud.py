from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import (
    StockItem, Lot, InventoryTransaction, Customer, Vendor,
    CustomerOrder, CustomerOrderLine, PurchaseOrder, PurchaseOrderLine, PurchaseOrderPayment,
    Shipment, ShipmentLine, ShipmentBox, PalletWeight, Invoice, InvoiceLine, InvoicePayment,
    LandedCost, LandedCostAllocation, CompanyProfile, ProductGroup, VendorItem, VendorBill,
)


def get_company_profile(db: Session) -> CompanyProfile:
    """Single-row company profile (id=1), created with defaults on first use."""
    profile = db.query(CompanyProfile).filter(CompanyProfile.id == 1).first()
    if not profile:
        profile = CompanyProfile(id=1, name="American Traders LLC - ATind Supplies", email="sales@atindsupplies.com")
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def generate_code(db: Session, model, prefix: str) -> str:
    """Sequential codes, e.g. CO-0001: one past the highest number in use. (Counting rows
    would hand out a code that already exists once any record has been deleted.)"""
    codes = db.query(model.code).filter(model.code.like(f"{prefix}-%")).all()
    n = max((int(c[len(prefix) + 1:]) for (c,) in codes if c[len(prefix) + 1:].isdigit()), default=0)
    return f"{prefix}-{n + 1:04d}"


# ---- Lot numbering and costing ----
def next_lot_code(db: Session) -> str:
    """Sequential LOT-00001 numbers, unique across receipts and adjustments. Callers that
    create several lots in one transaction must flush between them so this sees the last one."""
    codes = db.query(Lot.lot_code).filter(Lot.lot_code.like("LOT-%")).all()
    n = max((int(c[4:]) for (c,) in codes if c[4:].isdigit()), default=0)
    return f"LOT-{n + 1:05d}"


def line_landed_per_unit(db: Session, po_line_id: int) -> float:
    """Sum of every landed cost's per-unit share on this PO line."""
    allocs = db.query(LandedCostAllocation).filter(LandedCostAllocation.po_line_id == po_line_id).all()
    return sum(a.per_unit for a in allocs)


def refresh_item_cost(db: Session, item: StockItem) -> None:
    """cost_price = weighted-average landed cost of the stock still sitting in lots, so
    inventory valuation and profit estimates follow real lot costs. Left alone when no
    costed stock remains (keeps the last known cost)."""
    lots = db.query(Lot).filter(
        Lot.item_id == item.id, Lot.quantity > 1e-9, Lot.unit_cost.isnot(None), Lot.status != "rejected",
    ).all()
    qty = sum(l.quantity for l in lots)
    if qty > 1e-9:
        item.cost_price = round(sum(l.quantity * l.unit_cost for l in lots) / qty, 5)


def recompute_lot_costs(db: Session, po_line_ids) -> None:
    """Re-derive unit_cost = base_unit_cost + landed share for every lot received on these
    PO lines -- run after landed costs change or a line's unit cost is corrected. Lots
    already shipped are updated too, so order profit picks up freight billed after the fact."""
    db.flush()
    item_ids = set()
    for line_id in set(po_line_ids):
        line = db.query(PurchaseOrderLine).filter(PurchaseOrderLine.id == line_id).first()
        if not line:
            continue
        extra = line_landed_per_unit(db, line_id)
        for lot in db.query(Lot).filter(Lot.po_line_id == line_id).all():
            if lot.base_unit_cost is None:
                lot.base_unit_cost = line.unit_cost
            lot.unit_cost = round(lot.base_unit_cost + extra, 6)
            item_ids.add(lot.item_id)
    db.flush()
    for item in db.query(StockItem).filter(StockItem.id.in_(item_ids)).all():
        refresh_item_cost(db, item)


def backfill_line_identity(db: Session) -> None:
    """One-off upgrades so every order line has a line number, and boxes / invoice lines
    created before per-line tracking point at the order line they belong to."""
    for order in db.query(CustomerOrder).all():
        next_no = max((l.line_no or 0 for l in order.lines), default=0)
        for line in sorted(order.lines, key=lambda l: l.id):
            if line.line_no is None:
                next_no += 1
                line.line_no = next_no
    for box in db.query(ShipmentBox).filter(ShipmentBox.order_line_id.is_(None)).all():
        line_ids = {sl.order_line_id for sl in box.shipment.lines if sl.item_id == box.item_id}
        if len(line_ids) == 1:
            box.order_line_id = line_ids.pop()
    for il in db.query(InvoiceLine).filter(InvoiceLine.order_line_id.is_(None), InvoiceLine.item_id.isnot(None)).all():
        shipment = db.query(Shipment).filter(Shipment.id == il.invoice.shipment_id).first() if il.invoice.shipment_id else None
        if shipment:
            line_ids = {sl.order_line_id for sl in shipment.lines if sl.item_id == il.item_id}
            if len(line_ids) == 1:
                il.order_line_id = line_ids.pop()
    db.commit()


def backfill_lot_costing(db: Session) -> None:
    """One-off upgrades for lots created before lot costing existed: split unit_cost into
    base + landed, record the starting quantity, and link purchase lots to their PO line."""
    for lot in db.query(Lot).all():
        if lot.base_unit_cost is None and lot.unit_cost is not None:
            lot.base_unit_cost = lot.unit_cost
        if lot.initial_quantity is None:
            received = db.query(func.sum(InventoryTransaction.quantity_delta)).filter(
                InventoryTransaction.lot_id == lot.id, InventoryTransaction.quantity_delta > 0,
            ).scalar()
            lot.initial_quantity = received or lot.quantity
        if lot.po_line_id is None and lot.source == "purchase" and lot.source_reference:
            lines = db.query(PurchaseOrderLine).join(PurchaseOrder).filter(
                PurchaseOrder.code == lot.source_reference, PurchaseOrderLine.item_id == lot.item_id,
            ).all()
            if len(lines) == 1:
                lot.po_line_id = lines[0].id
    db.commit()


# ---- Stock Items ----
DEFAULT_PRODUCT_GROUPS = ["ANCHOR", "BOLT", "NUT", "PIN", "RIVET", "SCREW", "STUD", "WASHER", "MISC"]


class ProductGroupService:
    @staticmethod
    def ensure_defaults(db: Session) -> None:
        """Seed the predefined groups, plus any category already typed on an item."""
        existing = {g.name for g in db.query(ProductGroup).all()}
        used = {c for (c,) in db.query(StockItem.category).distinct().all() if c}
        for name in sorted(set(DEFAULT_PRODUCT_GROUPS) | used):
            if name not in existing:
                db.add(ProductGroup(name=name))
        db.commit()

    @staticmethod
    def list(db: Session) -> list:
        counts = dict(db.query(StockItem.category, func.count(StockItem.id)).group_by(StockItem.category).all())
        return [{"id": g.id, "name": g.name, "item_count": counts.get(g.name, 0)}
                for g in db.query(ProductGroup).order_by(ProductGroup.name).all()]

    @staticmethod
    def create(db: Session, name: str) -> ProductGroup:
        name = (name or "").strip().upper()
        if not name:
            raise HTTPException(status_code=400, detail="Group name is required")
        if db.query(ProductGroup).filter(ProductGroup.name == name).first():
            raise HTTPException(status_code=400, detail=f"Group {name} already exists")
        group = ProductGroup(name=name)
        db.add(group)
        db.commit()
        db.refresh(group)
        return group

    @staticmethod
    def delete(db: Session, group_id: int) -> None:
        group = db.query(ProductGroup).filter(ProductGroup.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        in_use = db.query(StockItem).filter(StockItem.category == group.name).count()
        if in_use:
            raise HTTPException(status_code=400, detail=f"{in_use} item(s) are in {group.name} -- move them to another group first")
        db.delete(group)
        db.commit()

    @staticmethod
    def require(db: Session, name: Optional[str]) -> None:
        if not name:
            raise HTTPException(status_code=400, detail="Pick a product group for the item")
        if not db.query(ProductGroup).filter(ProductGroup.name == name).first():
            raise HTTPException(status_code=400, detail=f"Unknown product group '{name}'")


def price_history(db: Session, item_id: int) -> list:
    """Every price this item was sold at (customer order lines) and bought at (PO lines), newest first."""
    rows = []
    sales = (db.query(CustomerOrderLine, CustomerOrder, Customer)
             .join(CustomerOrder, CustomerOrder.id == CustomerOrderLine.order_id)
             .join(Customer, Customer.id == CustomerOrder.customer_id)
             .filter(CustomerOrderLine.item_id == item_id).all())
    for line, order, customer in sales:
        rows.append({"kind": "sale", "date": order.order_date, "doc_id": order.id, "doc_code": order.code,
                     "party": customer.name, "status": order.status, "quantity": line.quantity, "unit_price": line.unit_price})
    purchases = (db.query(PurchaseOrderLine, PurchaseOrder, Vendor)
                 .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
                 .join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
                 .filter(PurchaseOrderLine.item_id == item_id).all())
    for line, po, vendor in purchases:
        rows.append({"kind": "purchase", "date": po.order_date, "doc_id": po.id, "doc_code": po.code,
                     "party": vendor.name, "status": po.status, "quantity": line.quantity, "unit_price": line.unit_cost})
    rows.sort(key=lambda r: (r["date"] or datetime.min, r["doc_id"]), reverse=True)
    return rows


class StockItemService:
    @staticmethod
    def list(db: Session, q: Optional[str] = None, low_stock_only: bool = False) -> List[StockItem]:
        query = db.query(StockItem)
        if q:
            query = query.filter(
                (StockItem.code.ilike(f"%{q}%")) | (StockItem.title.ilike(f"%{q}%"))
                | (StockItem.barcode.ilike(f"%{q}%")) | (StockItem.category.ilike(f"%{q}%"))
            )
        if low_stock_only:
            # Compare against available (on_hand - booked), not raw on_hand -- stock already
            # promised to a confirmed order isn't free for a reorder decision to ignore.
            query = query.filter((StockItem.on_hand - StockItem.booked) <= StockItem.reorder_point)
        return query.order_by(StockItem.code).all()

    @staticmethod
    def get(db: Session, item_id: int) -> StockItem:
        item = db.query(StockItem).filter(StockItem.id == item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="Stock item not found")
        return item

    @staticmethod
    def create(db: Session, data) -> StockItem:
        if db.query(StockItem).filter(StockItem.code == data.code).first():
            raise HTTPException(status_code=400, detail=f"Item code '{data.code}' already exists")
        ProductGroupService.require(db, data.category)
        item = StockItem(**data.dict())
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    @staticmethod
    def update(db: Session, item_id: int, data, created_by: str = None) -> StockItem:
        item = StockItemService.get(db, item_id)
        updates = data.dict(exclude_unset=True)
        if "category" in updates:
            ProductGroupService.require(db, updates["category"])

        new_on_hand = updates.pop("on_hand", None)
        adj_cost = updates.pop("adjustment_unit_cost", None)
        adj_lot_code = (updates.pop("adjustment_lot_code", None) or "").strip()
        adj_note = (updates.pop("adjustment_note", None) or "").strip()

        for key, value in updates.items():
            setattr(item, key, value)

        if new_on_hand is not None and abs(new_on_hand - item.on_hand) > 1e-9:
            StockItemService._adjust(db, item, new_on_hand - item.on_hand, adj_cost, adj_lot_code, adj_note, created_by)
            refresh_item_cost(db, item)
        db.commit()
        db.refresh(item)
        return item

    @staticmethod
    def _adjust(db: Session, item: StockItem, delta: float, unit_cost: Optional[float],
                lot_code: str = "", note: str = "", created_by: str = None) -> Optional[Lot]:
        """Manual on-hand correction. Increases become a new costed lot; decreases consume lots."""
        note = note or "Manual on-hand correction via Stock Items edit"
        if delta > 0:
            # A correction that only bumped item.on_hand without creating a lot would be
            # invisible to shipping, which only ever pulls from Lots -- so a manual
            # increase has to create a real, shippable lot, not just move the counter.
            # And without a cost, anything booked from that lot can't be profit-tracked.
            if unit_cost is None or unit_cost < 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Enter the unit cost the added {delta:g} {item.unit or 'units'} of {item.code} were acquired at",
                )
            lot = Lot(
                item_id=item.id,
                lot_code=lot_code or next_lot_code(db),
                quantity=delta,
                initial_quantity=delta,
                base_unit_cost=unit_cost,
                unit_cost=unit_cost,
                status="available",
                source="adjustment",
                source_reference="Adjustment",
            )
            db.add(lot)
            db.flush()
            db.add(InventoryTransaction(
                item_id=item.id, lot_id=lot.id, quantity_delta=delta, type="adjustment",
                note=f"{note} (unit cost {unit_cost:g})", created_by=created_by,
            ))
            item.on_hand += delta
            return lot

        # Decrease: take unbooked stock first (oldest lot first) so open shipments keep
        # what they've booked, then dip into booked lots only if free stock can't cover it.
        # If lots can't cover it at all (e.g. a miscount with no lot history), zero out
        # what remains -- a manual correction represents ground truth and must succeed.
        remaining = -delta
        taken = {}
        for lot, free in ShipmentService.free_lot_quantities(db, item.id):
            if remaining <= 1e-9:
                break
            take = min(free, remaining)
            taken[lot.id] = (lot, take)
            remaining -= take
        if remaining > 1e-9:
            for lot in db.query(Lot).filter(
                Lot.item_id == item.id, Lot.status == "available", Lot.quantity > 0
            ).order_by(Lot.received_date).all():
                if remaining <= 1e-9:
                    break
                already = taken.get(lot.id, (lot, 0))[1]
                take = min(lot.quantity - already, remaining)
                if take > 1e-9:
                    taken[lot.id] = (lot, already + take)
                    remaining -= take
        for lot, take in taken.values():
            lot.quantity -= take
            db.add(InventoryTransaction(
                item_id=item.id, lot_id=lot.id, quantity_delta=-take, type="adjustment",
                note=note, created_by=created_by,
            ))
        if remaining > 1e-9:
            db.add(InventoryTransaction(
                item_id=item.id, lot_id=None, quantity_delta=-remaining, type="adjustment",
                note="Manual on-hand correction exceeded available lot quantity", created_by=created_by,
            ))
        item.on_hand += delta
        return None

    @staticmethod
    def bulk_set_pack_sizes(db: Session, entries) -> dict:
        """Paste-a-list bulk update of default_pack_size by item code, same idea as the
        existing portal's Pack Size Processor -- update the catalog for many items at once."""
        applied, not_found = [], []
        for entry in entries:
            item = db.query(StockItem).filter(StockItem.code == entry.code).first()
            if not item:
                not_found.append(entry.code)
                continue
            item.default_pack_size = entry.pack_size
            applied.append(entry.code)
        db.commit()
        return {"applied": applied, "not_found": not_found}


# ---- Customers / Vendors ----
class PartyService:
    def __init__(self, model):
        self.model = model

    def list(self, db: Session) -> List:
        return db.query(self.model).order_by(self.model.name).all()

    def get(self, db: Session, party_id: int):
        party = db.query(self.model).filter(self.model.id == party_id).first()
        if not party:
            raise HTTPException(status_code=404, detail=f"{self.model.__name__} not found")
        return party

    def create(self, db: Session, data):
        party = self.model(**data.dict())
        db.add(party)
        db.commit()
        db.refresh(party)
        return party

    def update(self, db: Session, party_id: int, data):
        party = self.get(db, party_id)
        for key, value in data.dict(exclude_unset=True).items():
            setattr(party, key, value)
        db.commit()
        db.refresh(party)
        return party


customer_service = PartyService(Customer)
vendor_service = PartyService(Vendor)


# ---- Lots ----
class LotService:
    @staticmethod
    def list(db: Session, item_id: Optional[int] = None) -> List[Lot]:
        query = db.query(Lot)
        if item_id:
            query = query.filter(Lot.item_id == item_id)
        return query.order_by(Lot.received_date).all()

    @staticmethod
    def get(db: Session, lot_id: int) -> Lot:
        lot = db.query(Lot).filter(Lot.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail="Lot not found")
        return lot

    @staticmethod
    def set_status(db: Session, lot_id: int, status: str) -> Lot:
        if status not in ("available", "on_hold", "rejected"):
            raise HTTPException(status_code=400, detail="status must be available, on_hold, or rejected")
        lot = LotService.get(db, lot_id)
        lot.status = status
        db.commit()
        db.refresh(lot)
        return lot

    @staticmethod
    def set_cost(db: Session, lot_id: int, unit_cost: float) -> Lot:
        """Set what this lot was acquired at; any landed costs on its PO line stay on top."""
        lot = LotService.get(db, lot_id)
        lot.base_unit_cost = unit_cost
        lot.unit_cost = round(unit_cost + (line_landed_per_unit(db, lot.po_line_id) if lot.po_line_id else 0), 6)
        db.flush()
        refresh_item_cost(db, db.query(StockItem).filter(StockItem.id == lot.item_id).first())
        db.commit()
        db.refresh(lot)
        return lot

    @staticmethod
    def expiring(db: Session, within_days: int = 30) -> List[Lot]:
        """Available lots with an expiry date within the next N days (or already past)."""
        cutoff = datetime.utcnow() + timedelta(days=within_days)
        return db.query(Lot).filter(
            Lot.status == "available", Lot.quantity > 0,
            Lot.expiry_date.isnot(None), Lot.expiry_date <= cutoff,
        ).order_by(Lot.expiry_date).all()


class InventoryTransactionService:
    @staticmethod
    def recent(db: Session, limit: int = 25) -> List[InventoryTransaction]:
        return db.query(InventoryTransaction).order_by(InventoryTransaction.created_at.desc()).limit(limit).all()


# ---- Customer Orders ----
class CustomerOrderService:
    @staticmethod
    def list(db: Session, status: Optional[str] = None) -> List[CustomerOrder]:
        query = db.query(CustomerOrder)
        if status:
            query = query.filter(CustomerOrder.status == status)
        return query.order_by(CustomerOrder.id.desc()).all()

    @staticmethod
    def get(db: Session, order_id: int) -> CustomerOrder:
        order = db.query(CustomerOrder).filter(CustomerOrder.id == order_id).first()
        if not order:
            raise HTTPException(status_code=404, detail="Customer order not found")
        return order

    @staticmethod
    def create(db: Session, data, created_by: str) -> CustomerOrder:
        customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
        if not customer:
            raise HTTPException(status_code=400, detail="Customer not found")
        if not data.lines:
            raise HTTPException(status_code=400, detail="Order must have at least one line")

        order = CustomerOrder(
            code=generate_code(db, CustomerOrder, "CO"),
            customer_id=data.customer_id,
            delivery_date=data.delivery_date,
            po_number=data.po_number,
            job_number=data.job_number,
            ship_to_address=data.ship_to_address or customer.shipping_address or customer.address,
            notes=data.notes,
            status="draft",
            created_by=created_by,
        )
        db.add(order)
        db.flush()

        for line_no, line in enumerate(data.lines, 1):
            if not db.query(StockItem).filter(StockItem.id == line.item_id).first():
                raise HTTPException(status_code=400, detail=f"Stock item {line.item_id} not found")
            if line.quantity <= 0:
                raise HTTPException(status_code=400, detail=f"Line #{line_no}: quantity must be greater than 0")
            db.add(CustomerOrderLine(
                order_id=order.id,
                line_no=line_no,
                item_id=line.item_id,
                quantity=line.quantity,
                unit_price=line.unit_price,
                delivery_date=line.delivery_date or data.delivery_date,
            ))

        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def confirm(db: Session, order_id: int) -> CustomerOrder:
        """Confirming is a commitment to the customer, not a stock reservation --
        stock is only checked and booked when a shipment is created."""
        order = CustomerOrderService.get(db, order_id)
        if order.status != "draft":
            raise HTTPException(status_code=400, detail=f"Order is already {order.status}")
        order.status = "confirmed"
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def update(db: Session, order_id: int, data) -> CustomerOrder:
        """Header fields -- always editable unless the order is cancelled."""
        order = CustomerOrderService.get(db, order_id)
        if order.status == "cancelled":
            raise HTTPException(status_code=400, detail="Cannot edit a cancelled order")
        updates = data.dict(exclude_unset=True)
        if "customer_id" in updates and updates["customer_id"] is not None:
            if not db.query(Customer).filter(Customer.id == updates["customer_id"]).first():
                raise HTTPException(status_code=400, detail="Customer not found")
        for key, value in updates.items():
            setattr(order, key, value)
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def _recompute_status(order: CustomerOrder) -> None:
        """Re-derive shipped/confirmed from line data. Draft and cancelled orders are left alone."""
        if order.status in ("draft", "cancelled"):
            return
        fully_shipped = all(l.shipped_quantity >= l.quantity - 1e-9 for l in order.lines)
        order.status = "shipped" if fully_shipped else "confirmed"

    @staticmethod
    def add_line(db: Session, order_id: int, data) -> CustomerOrder:
        order = CustomerOrderService.get(db, order_id)
        if order.status == "cancelled":
            raise HTTPException(status_code=400, detail="Cannot edit a cancelled order")
        if not db.query(StockItem).filter(StockItem.id == data.item_id).first():
            raise HTTPException(status_code=400, detail=f"Stock item {data.item_id} not found")
        if data.quantity <= 0:
            raise HTTPException(status_code=400, detail="Quantity must be greater than 0")
        db.add(CustomerOrderLine(
            order_id=order.id,
            line_no=max((l.line_no or 0 for l in order.lines), default=0) + 1,
            item_id=data.item_id,
            quantity=data.quantity,
            unit_price=data.unit_price,
            delivery_date=data.delivery_date or order.delivery_date,
        ))
        db.flush()
        db.refresh(order)
        CustomerOrderService._recompute_status(order)
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def update_line(db: Session, order_id: int, line_id: int, data) -> CustomerOrder:
        order = CustomerOrderService.get(db, order_id)
        if order.status == "cancelled":
            raise HTTPException(status_code=400, detail="Cannot edit a cancelled order")
        line = db.query(CustomerOrderLine).filter(
            CustomerOrderLine.id == line_id, CustomerOrderLine.order_id == order.id
        ).first()
        if not line:
            raise HTTPException(status_code=404, detail="Order line not found")

        updates = data.dict(exclude_unset=True)
        if "quantity" in updates and updates["quantity"] is not None:
            if updates["quantity"] < line.allocated_quantity - 1e-9:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot reduce quantity below {line.allocated_quantity}: {line.shipped_quantity} "
                           f"already shipped and {line.booked_quantity} booked into open shipments"
                )
        for key, value in updates.items():
            setattr(line, key, value)
        db.flush()
        CustomerOrderService._recompute_status(order)
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def remove_line(db: Session, order_id: int, line_id: int) -> CustomerOrder:
        order = CustomerOrderService.get(db, order_id)
        if order.status == "cancelled":
            raise HTTPException(status_code=400, detail="Cannot edit a cancelled order")
        line = db.query(CustomerOrderLine).filter(
            CustomerOrderLine.id == line_id, CustomerOrderLine.order_id == order.id
        ).first()
        if not line:
            raise HTTPException(status_code=404, detail="Order line not found")
        if line.allocated_quantity > 0:
            raise HTTPException(status_code=400, detail="Cannot remove a line that is booked into or shipped on a shipment")
        if len(order.lines) <= 1:
            raise HTTPException(status_code=400, detail="Order must have at least one line")
        db.delete(line)
        db.flush()
        db.refresh(order)
        CustomerOrderService._recompute_status(order)
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def cancel(db: Session, order_id: int) -> CustomerOrder:
        order = CustomerOrderService.get(db, order_id)
        if any(l.shipped_quantity > 0 for l in order.lines):
            raise HTTPException(status_code=400, detail="Cannot cancel an order that has already shipped")
        if any(l.booked_quantity > 0 for l in order.lines):
            raise HTTPException(status_code=400, detail="Cancel this order's open shipments first -- they still hold booked stock")
        order.status = "cancelled"
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def create_shipment(db: Session, order_id: int, data, created_by: str) -> Shipment:
        """Create a shipment and book the requested quantities into it from specific
        lots (oldest first). This is the one place stock availability is checked."""
        order = CustomerOrderService.get(db, order_id)
        if order.status in ("shipped", "invoiced", "cancelled"):
            raise HTTPException(status_code=400, detail=f"Order is already {order.status}")
        requested = [l for l in data.lines if l.quantity > 0]
        if not requested:
            raise HTTPException(status_code=400, detail="Enter a quantity to book on at least one line")

        shipment = Shipment(
            code=generate_code(db, Shipment, "SH"),
            order_id=order.id,
            status="new",
            carrier=data.carrier,
            tracking_number=data.tracking_number,
            shipping_cost=data.shipping_cost,
            notes=data.notes,
            created_by=created_by,
        )
        db.add(shipment)
        db.flush()

        for req in requested:
            line = db.query(CustomerOrderLine).filter(
                CustomerOrderLine.id == req.line_id,
                CustomerOrderLine.order_id == order.id,
            ).first()
            if not line:
                raise HTTPException(status_code=400, detail=f"Order line {req.line_id} not found on this order")
            item = db.query(StockItem).filter(StockItem.id == line.item_id).first()
            open_qty = line.quantity - line.allocated_quantity
            if req.quantity > open_qty + 1e-9:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot book {req.quantity} of {item.code}: only {open_qty} of the line is not "
                           f"already shipped or booked into another shipment"
                )

            lots_with_free = ShipmentService.free_lot_quantities(db, item.id)
            total_free = sum(free for _, free in lots_with_free)
            if total_free + 1e-9 < req.quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Not enough stock to book {item.code}: need {req.quantity}, only {total_free} free "
                           f"({item.on_hand} on hand, {item.booked} already booked into other shipments)"
                )

            remaining = req.quantity
            for lot, free in lots_with_free:
                if remaining <= 1e-9:
                    break
                take = min(free, remaining)
                remaining -= take
                db.add(ShipmentLine(
                    shipment_id=shipment.id,
                    order_line_id=line.id,
                    item_id=item.id,
                    lot_id=lot.id,
                    quantity=take,
                    picked_quantity=0,
                    unit_price=line.unit_price,
                ))
            item.booked += req.quantity
            # Flush so the next line's free-lot query sees what this one just booked
            # (two lines for the same item on one order must not double-book a lot).
            db.flush()

        if order.status == "draft":
            order.status = "confirmed"
        db.commit()
        db.refresh(shipment)
        return shipment


# ---- Shipments (booking -> picking -> shipped, plus packing list / labels) ----
class ShipmentService:
    OPEN_STATUSES = ("new", "ready")
    SHIPPED_STATUSES = ("shipped", "delivered", "invoiced")  # stock has left

    @staticmethod
    def list(db: Session) -> List[Shipment]:
        return db.query(Shipment).order_by(Shipment.id.desc()).all()

    @staticmethod
    def get(db: Session, shipment_id: int) -> Shipment:
        shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
        if not shipment:
            raise HTTPException(status_code=404, detail="Shipment not found")
        return shipment

    @staticmethod
    def free_lot_quantities(db: Session, item_id: int) -> list:
        """(lot, free qty) for each available lot, oldest first, where free = lot quantity
        minus what open shipments have already booked from that lot."""
        lots = db.query(Lot).filter(
            Lot.item_id == item_id, Lot.status == "available", Lot.quantity > 0,
        ).order_by(Lot.received_date).all()
        booked_by_lot = dict(db.query(ShipmentLine.lot_id, func.sum(ShipmentLine.quantity))
                             .join(Shipment, Shipment.id == ShipmentLine.shipment_id)
                             .filter(ShipmentLine.item_id == item_id,
                                     Shipment.status.in_(ShipmentService.OPEN_STATUSES))
                             .group_by(ShipmentLine.lot_id).all())
        result = []
        for lot in lots:
            free = lot.quantity - (booked_by_lot.get(lot.id) or 0)
            if free > 1e-9:
                result.append((lot, free))
        return result

    @staticmethod
    def reconcile_bookings(db: Session) -> None:
        """StockItem.booked is a cache of what open shipments hold -- rebuild it from them.
        Run at startup so stale reservations (e.g. from the old book-on-confirm logic) clear."""
        booked = dict(db.query(ShipmentLine.item_id, func.sum(ShipmentLine.quantity))
                      .join(Shipment, Shipment.id == ShipmentLine.shipment_id)
                      .filter(Shipment.status.in_(ShipmentService.OPEN_STATUSES))
                      .group_by(ShipmentLine.item_id).all())
        for item in db.query(StockItem).all():
            item.booked = booked.get(item.id) or 0
        # Shipments that shipped before picking existed were, by definition, fully picked.
        shipped_ids = [sid for (sid,) in db.query(Shipment.id).filter(Shipment.status.in_(ShipmentService.SHIPPED_STATUSES)).all()]
        if shipped_ids:
            db.query(ShipmentLine).filter(ShipmentLine.shipment_id.in_(shipped_ids)).update(
                {ShipmentLine.picked_quantity: ShipmentLine.quantity}, synchronize_session=False)
        db.commit()

    @staticmethod
    def update(db: Session, shipment_id: int, data) -> Shipment:
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status == "cancelled":
            raise HTTPException(status_code=400, detail="Cannot edit a cancelled shipment")
        for key, value in data.dict(exclude_unset=True).items():
            setattr(shipment, key, value)
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def confirm_booking(db: Session, shipment_id: int) -> Shipment:
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status != "new":
            raise HTTPException(status_code=400, detail=f"Shipment is {shipment.status}, bookings can only be confirmed on a new shipment")
        shipment.status = "ready"
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def pick(db: Session, shipment_id: int, data, created_by: str) -> Shipment:
        """Record picked quantities. Once every line is fully picked the shipment closes as shipped."""
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status != "ready":
            raise HTTPException(
                status_code=400,
                detail="Confirm the bookings before picking" if shipment.status == "new"
                       else f"Shipment is {shipment.status}, nothing to pick",
            )
        lines_by_id = {l.id: l for l in shipment.lines}
        if data.pick_all:
            for line in shipment.lines:
                line.picked_quantity = line.quantity
        else:
            for req in data.lines:
                line = lines_by_id.get(req.shipment_line_id)
                if not line:
                    raise HTTPException(status_code=400, detail=f"Line {req.shipment_line_id} is not on this shipment")
                if req.quantity < 0:
                    raise HTTPException(status_code=400, detail="Picked quantity cannot be negative")
                if (line.picked_quantity or 0) + req.quantity > line.quantity + 1e-9:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot pick {req.quantity}: only {line.quantity - (line.picked_quantity or 0)} "
                               f"left to pick on that line"
                    )
                line.picked_quantity = (line.picked_quantity or 0) + req.quantity

        if all((l.picked_quantity or 0) >= l.quantity - 1e-9 for l in shipment.lines):
            ShipmentService._close(db, shipment, created_by)
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def _close(db: Session, shipment: Shipment, created_by: str) -> None:
        """Everything is picked: stock physically leaves. Deduct lots/on-hand, release the booking."""
        for line in shipment.lines:
            lot = db.query(Lot).filter(Lot.id == line.lot_id).first() if line.lot_id else None
            item = db.query(StockItem).filter(StockItem.id == line.item_id).first()
            if lot and lot.quantity + 1e-9 < line.quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Lot {lot.lot_code} of {item.code} now only holds {lot.quantity}, but {line.quantity} "
                           f"is booked from it -- stock was adjusted after booking. Cancel and re-book this shipment."
                )
            if lot:
                lot.quantity -= line.quantity
            item.on_hand -= line.quantity
            item.booked = max(0, item.booked - line.quantity)
            line.order_line.shipped_quantity += line.quantity
            db.add(InventoryTransaction(
                item_id=item.id,
                lot_id=line.lot_id,
                quantity_delta=-line.quantity,
                type="shipment",
                reference=shipment.code,
                created_by=created_by,
            ))
        shipment.status = "shipped"
        shipment.ship_date = datetime.utcnow()
        db.flush()
        order = db.query(CustomerOrder).filter(CustomerOrder.id == shipment.order_id).first()
        CustomerOrderService._recompute_status(order)

    @staticmethod
    def cancel(db: Session, shipment_id: int) -> Shipment:
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status not in ShipmentService.OPEN_STATUSES:
            raise HTTPException(status_code=400, detail=f"Shipment is {shipment.status} and can no longer be cancelled")
        for line in shipment.lines:
            item = db.query(StockItem).filter(StockItem.id == line.item_id).first()
            item.booked = max(0, item.booked - line.quantity)
        db.query(ShipmentBox).filter(ShipmentBox.shipment_id == shipment.id).delete()
        db.query(PalletWeight).filter(PalletWeight.shipment_id == shipment.id).delete()
        shipment.status = "cancelled"
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def mark_delivered(db: Session, shipment: Shipment, delivered_at: Optional[datetime], by: str, commit: bool = True) -> Shipment:
        """Record the delivery date. A shipped shipment moves to "delivered"; an already
        invoiced one keeps its status but gets the date (it's used on the invoice)."""
        if shipment.status not in ShipmentService.SHIPPED_STATUSES:
            raise HTTPException(status_code=400, detail=f"{shipment.code} is {shipment.status} -- it has to ship before it can be delivered")
        when = delivered_at or datetime.utcnow()
        if shipment.ship_date and when < shipment.ship_date.replace(hour=0, minute=0, second=0, microsecond=0):
            raise HTTPException(status_code=400, detail=f"{shipment.code} shipped on {shipment.ship_date:%b %d, %Y} -- it can't be delivered before that")
        shipment.delivered_at = when
        shipment.delivered_by = by
        if shipment.status == "shipped":
            shipment.status = "delivered"
        if commit:
            db.commit()
            db.refresh(shipment)
        return shipment

    @staticmethod
    def clear_delivered(db: Session, shipment_id: int) -> Shipment:
        shipment = ShipmentService.get(db, shipment_id)
        shipment.delivered_at = None
        shipment.delivered_by = None
        if shipment.status == "delivered":
            shipment.status = "shipped"
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def unship(db: Session, shipment_id: int, created_by: str) -> Shipment:
        """Undo a shipped shipment: stock comes back to its lots/on-hand and stays booked,
        and the shipment returns to "new" so lines can be unbooked, edited, or cancelled.
        An invoiced shipment must have its invoice voided first."""
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status == "invoiced":
            live = db.query(Invoice).filter(Invoice.shipment_id == shipment.id, Invoice.status != "void").first()
            if live:
                raise HTTPException(status_code=400, detail=f"Shipment is invoiced on {live.code} -- void that invoice first")
        elif shipment.status not in ("shipped", "delivered"):
            raise HTTPException(status_code=400, detail=f"Shipment is {shipment.status} -- only shipped shipments can be un-shipped")
        for line in shipment.lines:
            lot = db.query(Lot).filter(Lot.id == line.lot_id).first() if line.lot_id else None
            item = db.query(StockItem).filter(StockItem.id == line.item_id).first()
            if lot:
                lot.quantity += line.quantity
            item.on_hand += line.quantity
            item.booked += line.quantity
            line.order_line.shipped_quantity = max(0, line.order_line.shipped_quantity - line.quantity)
            line.picked_quantity = 0
            db.add(InventoryTransaction(
                item_id=item.id,
                lot_id=line.lot_id,
                quantity_delta=line.quantity,
                type="shipment_reversal",
                reference=shipment.code,
                created_by=created_by,
            ))
        shipment.status = "new"
        shipment.ship_date = None
        shipment.delivered_at = None
        shipment.delivered_by = None
        db.flush()
        order = db.query(CustomerOrder).filter(CustomerOrder.id == shipment.order_id).first()
        CustomerOrderService._recompute_status(order)
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def delete(db: Session, shipment_id: int) -> None:
        """Delete a shipment that never shipped (open ones are released first)."""
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status in ShipmentService.SHIPPED_STATUSES:
            raise HTTPException(status_code=400, detail="Un-ship this shipment before deleting it")
        if db.query(Invoice).filter(Invoice.shipment_id == shipment.id).first():
            raise HTTPException(status_code=400, detail="An invoice references this shipment, so it can't be deleted")
        if shipment.status in ShipmentService.OPEN_STATUSES:
            for line in shipment.lines:
                item = db.query(StockItem).filter(StockItem.id == line.item_id).first()
                item.booked = max(0, item.booked - line.quantity)
        db.delete(shipment)
        db.commit()

    @staticmethod
    def unbook(db: Session, shipment_id: int, data) -> Shipment:
        """Release booked-but-unpicked quantity back to stock. Lines that drop to zero are
        removed; a shipment left with no lines is cancelled. Packing for the affected items
        is cleared since its box counts no longer match."""
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status not in ShipmentService.OPEN_STATUSES:
            raise HTTPException(status_code=400, detail=f"Shipment is {shipment.status} -- only new or ready shipments can be unbooked")
        if data.shipment_line_id:
            lines = [l for l in shipment.lines if l.id == data.shipment_line_id]
        elif data.order_line_id:
            # Newest lot first: undo booking in the reverse of the oldest-first order it was made.
            lines = sorted((l for l in shipment.lines if l.order_line_id == data.order_line_id),
                           key=lambda l: l.id, reverse=True)
        else:
            raise HTTPException(status_code=400, detail="Specify the shipment line or order line to unbook")
        if not lines:
            raise HTTPException(status_code=404, detail="That line is not on this shipment")

        unpicked = sum(l.quantity - (l.picked_quantity or 0) for l in lines)
        if data.quantity > unpicked + 1e-9:
            picked = sum(l.picked_quantity or 0 for l in lines)
            raise HTTPException(
                status_code=400,
                detail=f"Only {unpicked:g} can be unbooked" + (f" -- {picked:g} is already picked" if picked else ""),
            )

        remaining = data.quantity
        touched_lines = set()
        for line in lines:
            if remaining <= 1e-9:
                break
            take = min(line.quantity - (line.picked_quantity or 0), remaining)
            if take <= 1e-9:
                continue
            remaining -= take
            line.quantity -= take
            touched_lines.add(line.order_line_id)
            item = db.query(StockItem).filter(StockItem.id == line.item_id).first()
            item.booked = max(0, item.booked - take)
            if line.quantity <= 1e-9:
                shipment.lines.remove(line)

        for box in [b for b in shipment.boxes if b.order_line_id in touched_lines]:
            shipment.boxes.remove(box)
        if not shipment.lines:
            shipment.boxes.clear()
            shipment.pallets.clear()
            shipment.status = "cancelled"
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def set_boxes(db: Session, shipment_id: int, data) -> Shipment:
        """Replace the packing-list/box breakdown for this shipment (used for label printing)."""
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status == "cancelled":
            raise HTTPException(status_code=400, detail="Cannot pack a cancelled shipment")

        # Packing is per order line, never merged by item: the same item on two lines is
        # boxed (and labelled) as two separate lines.
        shipped_by_line, order_lines = ShipmentService.quantities_by_order_line(shipment)
        lines_by_item = {}
        for ol in order_lines.values():
            lines_by_item.setdefault(ol.item_id, []).append(ol)

        resolved, boxed_by_line = [], {}
        for box in data.boxes:
            ol = order_lines.get(box.order_line_id) if box.order_line_id else None
            if ol is None:
                candidates = lines_by_item.get(box.item_id, [])
                if box.order_line_id or len(candidates) != 1:
                    raise HTTPException(status_code=400, detail="Each box must name an order line that is on this shipment")
                ol = candidates[0]
            resolved.append((box, ol))
            boxed_by_line[ol.id] = boxed_by_line.get(ol.id, 0) + box.quantity_in_box

        for line_id in set(shipped_by_line) | set(boxed_by_line):
            shipped_qty = shipped_by_line.get(line_id, 0)
            boxed_qty = boxed_by_line.get(line_id, 0)
            if abs(boxed_qty - shipped_qty) > 1e-6:
                ol = order_lines[line_id]
                item = db.query(StockItem).filter(StockItem.id == ol.item_id).first()
                raise HTTPException(
                    status_code=400,
                    detail=f"Line #{ol.line_no} ({item.code if item else ol.item_id}): boxed {boxed_qty:g} "
                           f"but the shipment has {shipped_qty:g}"
                )

        db.query(ShipmentBox).filter(ShipmentBox.shipment_id == shipment.id).delete()
        for box, ol in resolved:
            db.add(ShipmentBox(
                shipment_id=shipment.id,
                order_line_id=ol.id,
                item_id=ol.item_id,
                box_number=box.box_number,
                quantity_in_box=box.quantity_in_box,
                lot_code=box.lot_code,
                pallet_number=box.pallet_number,
            ))
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def quantities_by_order_line(shipment: Shipment):
        """(quantity per order line id, order line by id) -- a line booked from several lots
        is still one line."""
        qty, lines = {}, {}
        for sl in shipment.lines:
            qty[sl.order_line_id] = qty.get(sl.order_line_id, 0) + sl.quantity
            lines[sl.order_line_id] = sl.order_line
        return qty, lines

    @staticmethod
    def set_pallet_weights(db: Session, shipment_id: int, data) -> Shipment:
        """Replace the weight/dimensions entries for this shipment's pallets."""
        shipment = ShipmentService.get(db, shipment_id)
        db.query(PalletWeight).filter(PalletWeight.shipment_id == shipment.id).delete()
        for p in data.pallets:
            db.add(PalletWeight(
                shipment_id=shipment.id,
                pallet_number=p.pallet_number,
                weight=p.weight,
                dimensions=p.dimensions,
            ))
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def unpacked(db: Session) -> List[Shipment]:
        """Non-cancelled, not-yet-invoiced shipments whose boxed quantity doesn't match
        their quantity -- i.e. still need a packing list / labels."""
        result = []
        for shipment in db.query(Shipment).filter(Shipment.status.notin_(("invoiced", "cancelled"))).order_by(Shipment.id.desc()).all():
            shipped_by_line, _ = ShipmentService.quantities_by_order_line(shipment)
            boxed_by_line = {}
            for box in shipment.boxes:
                boxed_by_line[box.order_line_id] = boxed_by_line.get(box.order_line_id, 0) + box.quantity_in_box
            if any(abs(boxed_by_line.get(line_id, 0) - qty) > 1e-6 for line_id, qty in shipped_by_line.items()):
                result.append(shipment)
        return result


# ---- Invoices ----
class InvoiceService:
    @staticmethod
    def list(db: Session) -> List[Invoice]:
        return db.query(Invoice).order_by(Invoice.id.desc()).all()

    @staticmethod
    def get(db: Session, invoice_id: int) -> Invoice:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found")
        return invoice

    @staticmethod
    def create_from_shipment(db: Session, shipment_id: int, data, created_by: str) -> Invoice:
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status not in ShipmentService.SHIPPED_STATUSES:
            raise HTTPException(status_code=400, detail=f"Shipment {shipment.code} is {shipment.status} -- only shipped shipments can be invoiced")
        if db.query(Invoice).filter(Invoice.shipment_id == shipment.id).first():
            raise HTTPException(status_code=400, detail=f"Shipment {shipment.code} has already been invoiced")

        order = db.query(CustomerOrder).filter(CustomerOrder.id == shipment.order_id).first()

        invoice = Invoice(
            code=generate_code(db, Invoice, "INV"),
            customer_id=order.customer_id,
            order_id=order.id,
            shipment_id=shipment.id,
            due_date=data.due_date,
            free_text=data.free_text or None,
            status="draft",
            created_by=created_by,
        )
        db.add(invoice)
        db.flush()

        # One invoice line per order line: a line booked from several lots collapses into one,
        # but two order lines for the same item stay separate (they may differ in price).
        qty_by_line, order_lines = ShipmentService.quantities_by_order_line(shipment)
        for line_id in sorted(qty_by_line, key=lambda i: (order_lines[i].line_no or 0, i)):
            ol = order_lines[line_id]
            item = db.query(StockItem).filter(StockItem.id == ol.item_id).first()
            db.add(InvoiceLine(
                invoice_id=invoice.id,
                item_id=ol.item_id,
                order_line_id=ol.id,
                description=item.title if item else f"Item {ol.item_id}",
                quantity=qty_by_line[line_id],
                unit_price=ol.unit_price,
            ))

        if data.shipping_charge and data.shipping_charge > 0:
            db.add(InvoiceLine(
                invoice_id=invoice.id,
                item_id=None,
                description="Shipping",
                quantity=1,
                unit_price=data.shipping_charge,
            ))

        shipment.status = "invoiced"
        db.commit()
        db.refresh(invoice)
        return invoice

    @staticmethod
    def update(db: Session, invoice_id: int, data) -> Invoice:
        """Edit line items / free text / due date while still in draft."""
        invoice = InvoiceService.get(db, invoice_id)
        if invoice.status != "draft":
            raise HTTPException(status_code=400, detail=f"Invoice is already {invoice.status}, cannot edit")

        if data.due_date is not None:
            invoice.due_date = data.due_date
        if data.free_text is not None:
            invoice.free_text = data.free_text

        if data.lines is not None:
            db.query(InvoiceLine).filter(InvoiceLine.invoice_id == invoice.id).delete()
            for line in data.lines:
                db.add(InvoiceLine(
                    invoice_id=invoice.id,
                    item_id=line.item_id,
                    order_line_id=line.order_line_id,
                    description=line.description,
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                ))

        db.commit()
        db.refresh(invoice)
        return invoice

    @staticmethod
    def set_status(db: Session, invoice_id: int, status: str) -> Invoice:
        if status not in ("sent", "paid", "void"):
            raise HTTPException(status_code=400, detail="status must be sent, paid, or void")
        invoice = InvoiceService.get(db, invoice_id)
        if status == "void" and invoice.payments:
            raise HTTPException(status_code=400, detail="Invoice has payments recorded against it, cannot void")
        invoice.status = status
        db.commit()
        db.refresh(invoice)
        return invoice


class InvoicePaymentService:
    @staticmethod
    def record(db: Session, invoice_id: int, data, created_by: str) -> Invoice:
        invoice = InvoiceService.get(db, invoice_id)
        if invoice.status != "sent":
            raise HTTPException(status_code=400, detail=f"Invoice is {invoice.status} -- payments can only be recorded against a sent invoice")
        if data.amount <= 0:
            raise HTTPException(status_code=400, detail="Payment amount must be greater than 0")
        if data.amount > invoice.balance + 0.005:
            raise HTTPException(status_code=400, detail=f"Payment of {data.amount:.2f} exceeds the open balance of {invoice.balance:.2f}")
        db.add(InvoicePayment(
            invoice_id=invoice.id,
            amount=data.amount,
            paid_date=data.paid_date or datetime.utcnow(),
            method=data.method,
            reference=data.reference,
            note=data.note,
            created_by=created_by,
        ))
        db.flush()
        db.refresh(invoice)
        if invoice.balance <= 0.005:
            invoice.status = "paid"
        db.commit()
        db.refresh(invoice)
        return invoice


# ---- Purchase Orders ----
class VendorItemService:
    """Vendor part # <-> our item. One vendor code maps to one of our items; one of our
    items can carry a different code at every vendor."""

    @staticmethod
    def list(db: Session, vendor_id: Optional[int] = None, item_id: Optional[int] = None) -> List[VendorItem]:
        q = db.query(VendorItem)
        if vendor_id:
            q = q.filter(VendorItem.vendor_id == vendor_id)
        if item_id:
            q = q.filter(VendorItem.item_id == item_id)
        return q.order_by(VendorItem.vendor_item_code).all()

    @staticmethod
    def find(db: Session, vendor_id: int, code: str) -> Optional[VendorItem]:
        code = (code or "").strip()
        if not code:
            return None
        return db.query(VendorItem).filter(VendorItem.vendor_id == vendor_id,
                                           func.lower(VendorItem.vendor_item_code) == code.lower()).first()

    @staticmethod
    def for_item(db: Session, vendor_id: int, item_id: int) -> Optional[VendorItem]:
        return (db.query(VendorItem).filter(VendorItem.vendor_id == vendor_id, VendorItem.item_id == item_id)
                .order_by(VendorItem.last_ordered_at.desc().nullslast(), VendorItem.id.desc()).first())

    @staticmethod
    def upsert(db: Session, vendor_id: int, item_id: int, code: str, description: Optional[str] = None,
               unit_cost: Optional[float] = None, ordered: bool = False) -> VendorItem:
        code = (code or "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="Vendor item # is required")
        if not db.query(StockItem).filter(StockItem.id == item_id).first():
            raise HTTPException(status_code=400, detail="Stock item not found")
        mapping = VendorItemService.find(db, vendor_id, code)
        if mapping and mapping.item_id != item_id:
            ours = db.query(StockItem).filter(StockItem.id == mapping.item_id).first()
            raise HTTPException(status_code=400,
                                detail=f"Vendor item # {code} is already mapped to our {ours.code if ours else mapping.item_id}")
        if not mapping:
            mapping = VendorItem(vendor_id=vendor_id, item_id=item_id, vendor_item_code=code)
            db.add(mapping)
        if description:
            mapping.vendor_description = description
        if unit_cost is not None:
            mapping.last_unit_cost = unit_cost
        if ordered:
            mapping.last_ordered_at = datetime.utcnow()
        db.flush()
        return mapping

    @staticmethod
    def delete(db: Session, vendor_id: int, mapping_id: int) -> None:
        mapping = db.query(VendorItem).filter(VendorItem.id == mapping_id, VendorItem.vendor_id == vendor_id).first()
        if not mapping:
            raise HTTPException(status_code=404, detail="Cross-reference not found")
        db.delete(mapping)
        db.commit()

    @staticmethod
    def resolve_line(db: Session, vendor_id: int, line) -> tuple:
        """(item_id, vendor_item_code, vendor_description) for a PO line being added: a vendor
        item # alone picks our item; our item alone picks up its known vendor #; both together
        teach the cross-reference."""
        code = (line.vendor_item_code or "").strip() or None
        item_id = line.item_id
        desc = (line.vendor_description or "").strip() or None
        if code:
            mapping = VendorItemService.find(db, vendor_id, code)
            if not item_id:
                if not mapping:
                    raise HTTPException(status_code=400, detail=f"Vendor item # {code} isn't mapped to any of our items yet -- pick the item once and it will be remembered")
                item_id = mapping.item_id
            if mapping and mapping.item_id == item_id:
                code = mapping.vendor_item_code  # keep the vendor's own spelling, not what was typed
            desc = desc or (mapping.vendor_description if mapping else None)
        elif item_id:
            mapping = VendorItemService.for_item(db, vendor_id, item_id)
            if mapping:
                code, desc = mapping.vendor_item_code, desc or mapping.vendor_description
        if not item_id:
            raise HTTPException(status_code=400, detail="Pick an item or enter a known vendor item #")
        if not db.query(StockItem).filter(StockItem.id == item_id).first():
            raise HTTPException(status_code=400, detail=f"Stock item {item_id} not found")
        return item_id, code, desc

    @staticmethod
    def learn(db: Session, vendor_id: int, item_id: int, code: Optional[str], desc: Optional[str], unit_cost: float) -> None:
        if code:
            VendorItemService.upsert(db, vendor_id, item_id, code, desc, unit_cost, ordered=True)


class PurchaseOrderService:
    @staticmethod
    def list(db: Session, status: Optional[str] = None) -> List[PurchaseOrder]:
        query = db.query(PurchaseOrder)
        if status:
            query = query.filter(PurchaseOrder.status == status)
        return query.order_by(PurchaseOrder.id.desc()).all()

    @staticmethod
    def get(db: Session, po_id: int) -> PurchaseOrder:
        po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")
        return po

    @staticmethod
    def create(db: Session, data, created_by: str) -> PurchaseOrder:
        if not db.query(Vendor).filter(Vendor.id == data.vendor_id).first():
            raise HTTPException(status_code=400, detail="Vendor not found")
        if not data.lines:
            raise HTTPException(status_code=400, detail="Order must have at least one line")

        po = PurchaseOrder(
            code=generate_code(db, PurchaseOrder, "PO"),
            vendor_id=data.vendor_id,
            expected_date=data.expected_date,
            notes=data.notes,
            status="draft",
            created_by=created_by,
        )
        db.add(po)
        db.flush()

        for line in data.lines:
            item_id, code, desc = VendorItemService.resolve_line(db, po.vendor_id, line)
            db.add(PurchaseOrderLine(
                po_id=po.id,
                item_id=item_id,
                quantity=line.quantity,
                unit_cost=line.unit_cost,
                vendor_item_code=code,
                vendor_description=desc,
            ))
            VendorItemService.learn(db, po.vendor_id, item_id, code, desc, line.unit_cost)

        db.commit()
        db.refresh(po)
        return po

    @staticmethod
    def mark_ordered(db: Session, po_id: int) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        if po.status != "draft":
            raise HTTPException(status_code=400, detail=f"Order is already {po.status}")
        po.status = "ordered"
        db.commit()
        db.refresh(po)
        return po

    @staticmethod
    def update(db: Session, po_id: int, data) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        if po.status == "cancelled":
            raise HTTPException(status_code=400, detail="Cannot edit a cancelled order")
        updates = data.dict(exclude_unset=True)
        if "vendor_id" in updates and updates["vendor_id"] is not None:
            if not db.query(Vendor).filter(Vendor.id == updates["vendor_id"]).first():
                raise HTTPException(status_code=400, detail="Vendor not found")
        for key, value in updates.items():
            setattr(po, key, value)
        db.commit()
        db.refresh(po)
        return po

    @staticmethod
    def add_line(db: Session, po_id: int, data) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        if po.status in ("received", "cancelled"):
            raise HTTPException(status_code=400, detail=f"Order is already {po.status}")
        item_id, code, desc = VendorItemService.resolve_line(db, po.vendor_id, data)
        db.add(PurchaseOrderLine(po_id=po.id, item_id=item_id, quantity=data.quantity, unit_cost=data.unit_cost,
                                 vendor_item_code=code, vendor_description=desc))
        VendorItemService.learn(db, po.vendor_id, item_id, code, desc, data.unit_cost)
        db.flush()
        if po.status != "draft":
            po.status = "partially_received" if any(l.received_quantity > 0 for l in po.lines) else "ordered"
        db.commit()
        db.refresh(po)
        return po

    @staticmethod
    def update_line(db: Session, po_id: int, line_id: int, data) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        if po.status in ("received", "cancelled"):
            raise HTTPException(status_code=400, detail=f"Order is already {po.status}")
        line = db.query(PurchaseOrderLine).filter(
            PurchaseOrderLine.id == line_id, PurchaseOrderLine.po_id == po.id
        ).first()
        if not line:
            raise HTTPException(status_code=404, detail="PO line not found")

        updates = data.dict(exclude_unset=True)
        if "quantity" in updates and updates["quantity"] is not None:
            if updates["quantity"] < line.received_quantity - 1e-9:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot reduce quantity below {line.received_quantity}, which has already been received"
                )
        cost_changed = "unit_cost" in updates and updates["unit_cost"] is not None and abs(updates["unit_cost"] - line.unit_cost) > 1e-9
        for key in ("vendor_item_code", "vendor_description"):
            if key in updates:
                updates[key] = (updates[key] or "").strip() or None
        for key, value in updates.items():
            setattr(line, key, value)
        if line.vendor_item_code:
            VendorItemService.learn(db, po.vendor_id, line.item_id, line.vendor_item_code, line.vendor_description, line.unit_cost)
        if cost_changed:
            # A corrected PO price flows into lots already received on this line.
            for lot in db.query(Lot).filter(Lot.po_line_id == line.id).all():
                lot.base_unit_cost = line.unit_cost
            recompute_lot_costs(db, [line.id])
        db.commit()
        db.refresh(po)
        return po

    @staticmethod
    def remove_line(db: Session, po_id: int, line_id: int) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        if po.status in ("received", "cancelled"):
            raise HTTPException(status_code=400, detail=f"Order is already {po.status}")
        line = db.query(PurchaseOrderLine).filter(
            PurchaseOrderLine.id == line_id, PurchaseOrderLine.po_id == po.id
        ).first()
        if not line:
            raise HTTPException(status_code=404, detail="PO line not found")
        if line.received_quantity > 0:
            raise HTTPException(status_code=400, detail="Cannot remove a line that has already been received")
        if line.allocations:
            codes = ", ".join(sorted({a.landed_cost.code for a in line.allocations}))
            raise HTTPException(status_code=400, detail=f"This line carries landed cost {codes} -- edit or delete that landed cost first")
        if len(po.lines) <= 1:
            raise HTTPException(status_code=400, detail="Order must have at least one line")
        db.delete(line)
        db.commit()
        db.refresh(po)
        return po

    @staticmethod
    def cancel(db: Session, po_id: int) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        if any(l.received_quantity > 0 for l in po.lines):
            raise HTTPException(status_code=400, detail="Cannot cancel an order that has already received stock")
        lc_codes = sorted({a.landed_cost.code for l in po.lines for a in l.allocations})
        if lc_codes:
            raise HTTPException(status_code=400, detail=f"Landed cost {', '.join(lc_codes)} is applied to this order -- remove it from that landed cost first")
        po.status = "cancelled"
        db.commit()
        db.refresh(po)
        return po

    @staticmethod
    def receive(db: Session, po_id: int, data, created_by: str) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        if po.status in ("received", "cancelled"):
            raise HTTPException(status_code=400, detail=f"Order is already {po.status}")

        # Validate every line up front so a bad line doesn't leave a half-received PO.
        resolved = []
        for recv_line in data.lines:
            if recv_line.quantity <= 0:
                continue
            line = db.query(PurchaseOrderLine).filter(
                PurchaseOrderLine.id == recv_line.line_id,
                PurchaseOrderLine.po_id == po.id,
            ).first()
            if not line:
                raise HTTPException(status_code=400, detail=f"PO line {recv_line.line_id} not found on this order")
            if line.received_quantity + recv_line.quantity > line.quantity + 1e-9:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot receive {recv_line.quantity} on line {line.id}: only "
                           f"{line.quantity - line.received_quantity} remains unreceived"
                )
            resolved.append((recv_line, line))
        if not resolved:
            raise HTTPException(status_code=400, detail="Enter a quantity to receive on at least one line")

        # Every receipt becomes its own lot (LOT-##### unless the user typed the supplier's
        # lot code). Bookings draw from lots, so this is what ties a shipped unit back to
        # the receipt -- and the price -- it came from.
        touched_items = {}
        for recv_line, line in resolved:
            item = db.query(StockItem).filter(StockItem.id == line.item_id).first()
            landed = line_landed_per_unit(db, line.id)
            lot = Lot(
                item_id=item.id,
                lot_code=(recv_line.lot_code or "").strip() or next_lot_code(db),
                quantity=recv_line.quantity,
                initial_quantity=recv_line.quantity,
                base_unit_cost=line.unit_cost,
                unit_cost=round(line.unit_cost + landed, 6),
                po_line_id=line.id,
                received_date=datetime.utcnow(),
                expiry_date=recv_line.expiry_date,
                status="available",
                source="purchase",
                source_reference=po.code,
            )
            db.add(lot)
            db.flush()

            db.add(InventoryTransaction(
                item_id=item.id,
                lot_id=lot.id,
                quantity_delta=recv_line.quantity,
                type="receipt",
                reference=po.code,
                note=f"Lot {lot.lot_code}: unit cost {line.unit_cost:g} + {landed:.5f} landed" if landed else f"Lot {lot.lot_code}",
                created_by=created_by,
            ))
            item.on_hand += recv_line.quantity
            line.received_quantity += recv_line.quantity
            touched_items[item.id] = item

        db.flush()
        for item in touched_items.values():
            refresh_item_cost(db, item)

        # Older clients may still send freight/tariff with the receipt -- turn each into a
        # landed cost on this PO so it's allocated the same way (by quantity) as any other.
        for amount, cost_type in ((data.freight_cost, "freight"), (data.tariff_cost, "tariff")):
            if amount and amount > 0:
                LandedCostService.create_internal(
                    db, description=f"{cost_type.title()} on {po.code} receipt", cost_type=cost_type,
                    amount=amount, po_ids=[po.id], created_by=created_by,
                )

        fully_received = all(l.received_quantity >= l.quantity - 1e-9 for l in po.lines)
        po.status = "received" if fully_received else "partially_received"
        db.commit()
        db.refresh(po)
        return po


class PurchaseOrderPaymentService:
    @staticmethod
    def record(db: Session, po_id: int, data, created_by: str) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        if data.amount <= 0:
            raise HTTPException(status_code=400, detail="Payment amount must be greater than 0")
        if data.vendor_bill_id:
            bill = db.query(VendorBill).filter(VendorBill.id == data.vendor_bill_id, VendorBill.po_id == po.id).first()
            if not bill:
                raise HTTPException(status_code=400, detail="That vendor invoice isn't on this purchase order")
            if data.amount > bill.balance + 0.005:
                raise HTTPException(status_code=400, detail=f"Vendor invoice {bill.bill_number} only has {bill.balance:,.2f} left to pay")
        db.add(PurchaseOrderPayment(
            vendor_bill_id=data.vendor_bill_id,
            po_id=po.id,
            amount=data.amount,
            currency=data.currency,
            paid_date=data.paid_date,
            method=data.method,
            reference=data.reference,
            note=data.note,
            created_by=created_by,
        ))
        db.commit()
        db.refresh(po)
        return po


class VendorBillService:
    @staticmethod
    def create(db: Session, po_id: int, data, created_by: str) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        number = (data.bill_number or "").strip()
        if not number:
            raise HTTPException(status_code=400, detail="Enter the vendor's invoice #")
        if data.amount <= 0:
            raise HTTPException(status_code=400, detail="Invoice amount must be greater than 0")
        if any(b.bill_number.lower() == number.lower() for b in po.bills):
            raise HTTPException(status_code=400, detail=f"Vendor invoice {number} is already recorded on {po.code}")
        db.add(VendorBill(po_id=po.id, bill_number=number, bill_date=data.bill_date or datetime.utcnow(),
                          due_date=data.due_date, amount=round(data.amount, 2), note=data.note,
                          attachment_id=data.attachment_id, created_by=created_by))
        db.commit()
        db.refresh(po)
        return po

    @staticmethod
    def delete(db: Session, po_id: int, bill_id: int) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        bill = db.query(VendorBill).filter(VendorBill.id == bill_id, VendorBill.po_id == po.id).first()
        if not bill:
            raise HTTPException(status_code=404, detail="Vendor invoice not found")
        if bill.payments:
            raise HTTPException(status_code=400, detail=f"Payments are recorded against {bill.bill_number} -- it can't be deleted")
        db.delete(bill)
        db.commit()
        db.refresh(po)
        return po


# ---- Landed costs ----
LANDED_COST_TYPES = ("freight", "tariff", "customs", "brokerage", "insurance", "other")


class LandedCostService:
    @staticmethod
    def list(db: Session) -> List[LandedCost]:
        return db.query(LandedCost).order_by(LandedCost.id.desc()).all()

    @staticmethod
    def get(db: Session, lc_id: int) -> LandedCost:
        lc = db.query(LandedCost).filter(LandedCost.id == lc_id).first()
        if not lc:
            raise HTTPException(status_code=404, detail="Landed cost not found")
        return lc

    @staticmethod
    def plan(db: Session, amount: float, po_ids: List[int]) -> List[dict]:
        """Split amount over every line of the selected POs in proportion to line quantity,
        so each unit across the selection carries the same landed cost per unit."""
        if amount is None or amount <= 0:
            raise HTTPException(status_code=400, detail="Amount must be greater than 0")
        po_ids = list(dict.fromkeys(po_ids or []))
        if not po_ids:
            raise HTTPException(status_code=400, detail="Select at least one purchase order to apply this cost to")
        pos = db.query(PurchaseOrder).filter(PurchaseOrder.id.in_(po_ids)).all()
        if len(pos) != len(po_ids):
            raise HTTPException(status_code=400, detail="One or more selected purchase orders were not found")
        cancelled = [po.code for po in pos if po.status == "cancelled"]
        if cancelled:
            raise HTTPException(status_code=400, detail=f"Cannot apply landed costs to cancelled order {', '.join(cancelled)}")
        lines = [l for po in sorted(pos, key=lambda p: p.id) for l in po.lines if l.quantity > 0]
        total_qty = sum(l.quantity for l in lines)
        if total_qty <= 0:
            raise HTTPException(status_code=400, detail="The selected purchase orders have no quantity to spread the cost over")
        per_unit = amount / total_qty
        return [{
            "po_id": l.po_id, "po_line_id": l.id, "item_id": l.item_id,
            "quantity": l.quantity, "amount": per_unit * l.quantity, "per_unit": per_unit,
        } for l in lines]

    @staticmethod
    def _validate(data) -> None:
        if not (data.description or "").strip():
            raise HTTPException(status_code=400, detail="Description is required")
        if data.cost_type not in LANDED_COST_TYPES:
            raise HTTPException(status_code=400, detail=f"Type must be one of: {', '.join(LANDED_COST_TYPES)}")

    @staticmethod
    def _allocate(db: Session, lc: LandedCost, po_ids: List[int]) -> List[int]:
        line_ids = []
        for p in LandedCostService.plan(db, lc.amount, po_ids):
            db.add(LandedCostAllocation(
                landed_cost_id=lc.id, po_id=p["po_id"], po_line_id=p["po_line_id"],
                item_id=p["item_id"], quantity=p["quantity"], amount=p["amount"],
            ))
            line_ids.append(p["po_line_id"])
        return line_ids

    @staticmethod
    def create_internal(db: Session, description: str, cost_type: str, amount: float, po_ids: List[int],
                        created_by: str = None, **fields) -> LandedCost:
        """Create and allocate without committing -- callers own the transaction."""
        lc = LandedCost(
            code=generate_code(db, LandedCost, "LC"), description=description.strip(), cost_type=cost_type,
            amount=amount, created_by=created_by, **fields,
        )
        db.add(lc)
        db.flush()
        recompute_lot_costs(db, LandedCostService._allocate(db, lc, po_ids))
        return lc

    @staticmethod
    def create(db: Session, data, created_by: str) -> LandedCost:
        LandedCostService._validate(data)
        lc = LandedCostService.create_internal(
            db, description=data.description, cost_type=data.cost_type, amount=data.amount,
            po_ids=data.po_ids, created_by=created_by, paid_to=data.paid_to, reference=data.reference,
            cost_date=data.cost_date, notes=data.notes,
        )
        db.commit()
        db.refresh(lc)
        return lc

    @staticmethod
    def update(db: Session, lc_id: int, data) -> LandedCost:
        """Edit and re-spread: old allocations are dropped and lots on both the old and new
        PO lines are re-costed."""
        LandedCostService._validate(data)
        lc = LandedCostService.get(db, lc_id)
        old_line_ids = [a.po_line_id for a in lc.allocations]
        for field in ("description", "cost_type", "amount", "paid_to", "reference", "cost_date", "notes"):
            setattr(lc, field, getattr(data, field))
        lc.description = lc.description.strip()
        lc.allocations.clear()
        db.flush()
        new_line_ids = LandedCostService._allocate(db, lc, data.po_ids)
        recompute_lot_costs(db, old_line_ids + new_line_ids)
        db.commit()
        db.refresh(lc)
        return lc

    @staticmethod
    def delete(db: Session, lc_id: int) -> None:
        lc = LandedCostService.get(db, lc_id)
        line_ids = [a.po_line_id for a in lc.allocations]
        db.delete(lc)
        recompute_lot_costs(db, line_ids)
        db.commit()


# ---- Order profit ----
class OrderProfitService:
    @staticmethod
    def calculate(db: Session, order_id: int) -> dict:
        """Revenue vs. lot cost for every unit on the order:
        shipped  -- the lots the units actually left from (actual)
        booked   -- the lots open shipments reserved (committed)
        unbooked -- projected from the free lots booking would draw next, oldest first
        Landed costs are already inside each lot's unit_cost."""
        order = CustomerOrderService.get(db, order_id)
        buckets = {k: {"quantity": 0.0, "revenue": 0.0, "cost": 0.0, "profit": 0.0} for k in ("shipped", "booked", "unbooked")}
        missing, warnings, lines_out = {}, [], []
        free_lots_by_item = {}  # item_id -> [[lot, free]], consumed as unbooked lines are projected

        def note_missing(lot, item, qty):
            entry = missing.setdefault(lot.id, {
                "lot_id": lot.id, "lot_code": lot.lot_code, "item_code": item.code,
                "quantity": 0.0, "source": lot.source_reference or lot.source,
            })
            entry["quantity"] += qty

        for line in sorted(order.lines, key=lambda l: l.id):
            item = db.query(StockItem).filter(StockItem.id == line.item_id).first()
            comps = []
            for sl in line._active_shipment_lines():
                kind = "shipped" if sl.shipment.status in ShipmentService.SHIPPED_STATUSES else "booked"
                lot = db.query(Lot).filter(Lot.id == sl.lot_id).first() if sl.lot_id else None
                unit_cost = lot.unit_cost if lot else None
                if lot is None:
                    warnings.append(f"{sl.shipment.code}: {sl.quantity:g} × {item.code} has no lot, so its cost is unknown")
                elif unit_cost is None:
                    note_missing(lot, item, sl.quantity)
                comps.append({
                    "kind": kind, "quantity": sl.quantity, "unit_cost": unit_cost,
                    "cost": sl.quantity * (unit_cost or 0), "revenue": sl.quantity * line.unit_price,
                    "shipment_id": sl.shipment.id, "shipment_code": sl.shipment.code,
                    "lot_id": lot.id if lot else None, "lot_code": lot.lot_code if lot else None,
                    "lot_source": (lot.source_reference or lot.source) if lot else None,
                })

            open_qty = line.quantity - line.allocated_quantity
            if open_qty > 1e-9:
                if item.id not in free_lots_by_item:
                    free_lots_by_item[item.id] = [[lot, free] for lot, free in ShipmentService.free_lot_quantities(db, item.id)]
                remaining = open_qty
                for slot in free_lots_by_item[item.id]:
                    if remaining <= 1e-9:
                        break
                    lot, free = slot
                    take = min(free, remaining)
                    if take <= 1e-9:
                        continue
                    slot[1] -= take
                    remaining -= take
                    if lot.unit_cost is None:
                        note_missing(lot, item, take)
                    comps.append({
                        "kind": "unbooked", "quantity": take, "unit_cost": lot.unit_cost,
                        "cost": take * (lot.unit_cost or 0), "revenue": take * line.unit_price,
                        "lot_id": lot.id, "lot_code": lot.lot_code,
                        "lot_source": lot.source_reference or lot.source, "estimated": True,
                    })
                if remaining > 1e-9:
                    est = item.cost_price or 0
                    warnings.append(f"{remaining:g} × {item.code} not in stock yet -- costed at the item's average cost ({est:g})")
                    comps.append({
                        "kind": "unbooked", "quantity": remaining, "unit_cost": est,
                        "cost": remaining * est, "revenue": remaining * line.unit_price, "estimated": True,
                    })

            revenue = sum(c["revenue"] for c in comps)
            cost = sum(c["cost"] for c in comps)
            for c in comps:
                b = buckets[c["kind"]]
                b["quantity"] += c["quantity"]
                b["revenue"] += c["revenue"]
                b["cost"] += c["cost"]
                b["profit"] += c["revenue"] - c["cost"]
            lines_out.append({
                "line_id": line.id, "line_no": line.line_no, "item_id": item.id, "item_code": item.code, "item_title": item.title,
                "quantity": line.quantity, "unit_price": line.unit_price,
                "revenue": revenue, "cost": cost, "profit": revenue - cost,
                "margin_pct": (revenue - cost) / revenue * 100 if revenue > 1e-9 else None,
                "components": comps,
            })

        shipping_cost = sum(s.shipping_cost or 0 for s in db.query(Shipment).filter(
            Shipment.order_id == order.id, Shipment.status != "cancelled").all())
        other_charges = sum(l.quantity * l.unit_price
                            for inv in db.query(Invoice).filter(Invoice.order_id == order.id, Invoice.status != "void").all()
                            for l in inv.lines if l.item_id is None)
        revenue = sum(b["revenue"] for b in buckets.values())
        cogs = sum(b["cost"] for b in buckets.values())
        net = revenue - cogs + other_charges - shipping_cost
        if missing:
            warnings.insert(0, f"{len(missing)} lot(s) have no cost recorded -- enter what they were acquired at for an accurate profit")
        return {
            "order_id": order.id, "order_code": order.code, "lines": lines_out,
            **buckets,
            "revenue": revenue, "cogs": cogs, "gross_profit": revenue - cogs,
            "other_charges": other_charges, "shipping_cost": shipping_cost, "net_profit": net,
            "margin_pct": net / (revenue + other_charges) * 100 if revenue + other_charges > 1e-9 else None,
            "missing_costs": list(missing.values()), "warnings": warnings,
        }
