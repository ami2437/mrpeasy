from sqlalchemy.orm import Session
from app.models import CustomerOrder, StockItem, ManufacturingOrder, Vendor
from app.schemas import (
    CustomerOrderCreate, CustomerOrderUpdate, CustomerOrderResponse,
    StockItemResponse, ManufacturingOrderResponse, VendorResponse
)
from typing import List, Optional


class CustomerOrderService:
    @staticmethod
    def get_orders(db: Session, skip: int = 0, limit: int = 100) -> List[CustomerOrder]:
        return db.query(CustomerOrder).offset(skip).limit(limit).all()

    @staticmethod
    def get_order(db: Session, order_id: int) -> Optional[CustomerOrder]:
        return db.query(CustomerOrder).filter(CustomerOrder.id == order_id).first()

    @staticmethod
    def create_order(db: Session, order: CustomerOrderCreate) -> CustomerOrder:
        db_order = CustomerOrder(**order.dict())
        db.add(db_order)
        db.commit()
        db.refresh(db_order)
        return db_order

    @staticmethod
    def update_order(db: Session, order_id: int, order: CustomerOrderUpdate) -> Optional[CustomerOrder]:
        db_order = db.query(CustomerOrder).filter(CustomerOrder.id == order_id).first()
        if db_order:
            update_data = order.dict(exclude_unset=True)
            for key, value in update_data.items():
                setattr(db_order, key, value)
            db.commit()
            db.refresh(db_order)
        return db_order

    @staticmethod
    def delete_order(db: Session, order_id: int) -> bool:
        db_order = db.query(CustomerOrder).filter(CustomerOrder.id == order_id).first()
        if db_order:
            db.delete(db_order)
            db.commit()
            return True
        return False


class StockItemService:
    @staticmethod
    def get_items(db: Session, skip: int = 0, limit: int = 100) -> List[StockItem]:
        return db.query(StockItem).offset(skip).limit(limit).all()

    @staticmethod
    def get_item(db: Session, item_id: int) -> Optional[StockItem]:
        return db.query(StockItem).filter(StockItem.id == item_id).first()

    @staticmethod
    def search_items(db: Session, query: str, skip: int = 0, limit: int = 100) -> List[StockItem]:
        return db.query(StockItem).filter(
            (StockItem.code.ilike(f"%{query}%")) |
            (StockItem.title.ilike(f"%{query}%"))
        ).offset(skip).limit(limit).all()

    @staticmethod
    def get_low_stock(db: Session, limit: int = 50) -> List[StockItem]:
        return db.query(StockItem).filter(
            StockItem.available < StockItem.expected_total
        ).limit(limit).all()


class ManufacturingOrderService:
    @staticmethod
    def get_orders(db: Session, skip: int = 0, limit: int = 100) -> List[ManufacturingOrder]:
        return db.query(ManufacturingOrder).offset(skip).limit(limit).all()

    @staticmethod
    def get_order(db: Session, order_id: int) -> Optional[ManufacturingOrder]:
        return db.query(ManufacturingOrder).filter(ManufacturingOrder.id == order_id).first()

    @staticmethod
    def get_active_orders(db: Session, limit: int = 50) -> List[ManufacturingOrder]:
        return db.query(ManufacturingOrder).filter(
            ManufacturingOrder.status.in_([10, 15, 20, 30, 35])
        ).limit(limit).all()


class VendorService:
    @staticmethod
    def get_vendors(db: Session, skip: int = 0, limit: int = 100) -> List[Vendor]:
        return db.query(Vendor).offset(skip).limit(limit).all()

    @staticmethod
    def get_vendor(db: Session, vendor_id: int) -> Optional[Vendor]:
        return db.query(Vendor).filter(Vendor.id == vendor_id).first()
