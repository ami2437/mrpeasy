from datetime import datetime
from typing import List, Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import (
    StockItem, Lot, InventoryTransaction, Customer, Vendor,
    CustomerOrder, CustomerOrderLine, PurchaseOrder, PurchaseOrderLine,
)


def generate_code(db: Session, model, prefix: str) -> str:
    """Simple sequential code generator, e.g. CO-0001. Fine for single-user, low-volume use."""
    count = db.query(func.count(model.id)).scalar() or 0
    return f"{prefix}-{count + 1:04d}"


# ---- Stock Items ----
class StockItemService:
    @staticmethod
    def list(db: Session, q: Optional[str] = None, low_stock_only: bool = False) -> List[StockItem]:
        query = db.query(StockItem)
        if q:
            query = query.filter((StockItem.code.ilike(f"%{q}%")) | (StockItem.title.ilike(f"%{q}%")))
        if low_stock_only:
            query = query.filter(StockItem.on_hand <= StockItem.reorder_point)
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
        item = StockItem(**data.dict())
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    @staticmethod
    def update(db: Session, item_id: int, data) -> StockItem:
        item = StockItemService.get(db, item_id)
        for key, value in data.dict(exclude_unset=True).items():
            setattr(item, key, value)
        db.commit()
        db.refresh(item)
        return item


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
        if not db.query(Customer).filter(Customer.id == data.customer_id).first():
            raise HTTPException(status_code=400, detail="Customer not found")
        if not data.lines:
            raise HTTPException(status_code=400, detail="Order must have at least one line")

        order = CustomerOrder(
            code=generate_code(db, CustomerOrder, "CO"),
            customer_id=data.customer_id,
            delivery_date=data.delivery_date,
            notes=data.notes,
            status="draft",
            created_by=created_by,
        )
        db.add(order)
        db.flush()

        for line in data.lines:
            if not db.query(StockItem).filter(StockItem.id == line.item_id).first():
                raise HTTPException(status_code=400, detail=f"Stock item {line.item_id} not found")
            db.add(CustomerOrderLine(
                order_id=order.id,
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
        order = CustomerOrderService.get(db, order_id)
        if order.status != "draft":
            raise HTTPException(status_code=400, detail=f"Order is already {order.status}")
        order.status = "confirmed"
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def ship(db: Session, order_id: int, data, created_by: str) -> CustomerOrder:
        order = CustomerOrderService.get(db, order_id)
        if order.status in ("shipped", "invoiced", "cancelled"):
            raise HTTPException(status_code=400, detail=f"Order is already {order.status}")

        for ship_line in data.lines:
            line = db.query(CustomerOrderLine).filter(
                CustomerOrderLine.id == ship_line.line_id,
                CustomerOrderLine.order_id == order.id,
            ).first()
            if not line:
                raise HTTPException(status_code=400, detail=f"Order line {ship_line.line_id} not found on this order")

            remaining_to_ship = ship_line.quantity
            if remaining_to_ship <= 0:
                continue
            if line.shipped_quantity + remaining_to_ship > line.quantity + 1e-9:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot ship {ship_line.quantity} on line {line.id}: only "
                           f"{line.quantity - line.shipped_quantity} remains unshipped"
                )

            item = db.query(StockItem).filter(StockItem.id == line.item_id).first()
            available_lots = db.query(Lot).filter(
                Lot.item_id == line.item_id,
                Lot.status == "available",
                Lot.quantity > 0,
            ).order_by(Lot.received_date).all()

            total_available = sum(lot.quantity for lot in available_lots)
            if total_available + 1e-9 < remaining_to_ship:
                raise HTTPException(
                    status_code=400,
                    detail=f"Not enough available stock for item {item.code}: "
                           f"need {remaining_to_ship}, only {total_available} available across lots"
                )

            for lot in available_lots:
                if remaining_to_ship <= 0:
                    break
                take = min(lot.quantity, remaining_to_ship)
                lot.quantity -= take
                remaining_to_ship -= take
                db.add(InventoryTransaction(
                    item_id=item.id,
                    lot_id=lot.id,
                    quantity_delta=-take,
                    type="shipment",
                    reference=order.code,
                    created_by=created_by,
                ))

            item.on_hand -= ship_line.quantity
            line.shipped_quantity += ship_line.quantity

        db.flush()
        fully_shipped = all(l.shipped_quantity >= l.quantity - 1e-9 for l in order.lines)
        order.status = "shipped" if fully_shipped else "confirmed"
        db.commit()
        db.refresh(order)
        return order


# ---- Purchase Orders ----
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
            if not db.query(StockItem).filter(StockItem.id == line.item_id).first():
                raise HTTPException(status_code=400, detail=f"Stock item {line.item_id} not found")
            db.add(PurchaseOrderLine(
                po_id=po.id,
                item_id=line.item_id,
                quantity=line.quantity,
                unit_cost=line.unit_cost,
            ))

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
    def receive(db: Session, po_id: int, data, created_by: str) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        if po.status in ("received", "cancelled"):
            raise HTTPException(status_code=400, detail=f"Order is already {po.status}")

        if data.freight_cost is not None:
            po.freight_cost = data.freight_cost
        if data.tariff_cost is not None:
            po.tariff_cost = data.tariff_cost

        for recv_line in data.lines:
            line = db.query(PurchaseOrderLine).filter(
                PurchaseOrderLine.id == recv_line.line_id,
                PurchaseOrderLine.po_id == po.id,
            ).first()
            if not line:
                raise HTTPException(status_code=400, detail=f"PO line {recv_line.line_id} not found on this order")
            if recv_line.quantity <= 0:
                continue
            if line.received_quantity + recv_line.quantity > line.quantity + 1e-9:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot receive {recv_line.quantity} on line {line.id}: only "
                           f"{line.quantity - line.received_quantity} remains unreceived"
                )

            item = db.query(StockItem).filter(StockItem.id == line.item_id).first()
            lot_code = recv_line.lot_code or f"{po.code}-{item.code}"

            lot = Lot(
                item_id=item.id,
                lot_code=lot_code,
                quantity=recv_line.quantity,
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
                created_by=created_by,
            ))

            item.on_hand += recv_line.quantity
            line.received_quantity += recv_line.quantity

        db.flush()
        fully_received = all(l.received_quantity >= l.quantity - 1e-9 for l in po.lines)
        po.status = "received" if fully_received else "partially_received"
        db.commit()
        db.refresh(po)
        return po
