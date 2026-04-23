from sqlalchemy.orm import Session
from app.models import (
    CustomerOrder, StockItem, ManufacturingOrder,
    Vendor, Inventory, SyncLog
)
from app.services.mrpeasy_client import mrpeasy_client
from datetime import datetime
from typing import List, Optional


class SyncService:
    """Service for syncing data from MRPeasy API to local database"""

    @staticmethod
    def sync_customer_orders(db: Session) -> dict:
        """Sync customer orders from MRPeasy API"""
        try:
            # Fetch from MRPeasy
            orders = mrpeasy_client.get_customer_orders() or []
            if not isinstance(orders, list):
                orders = [orders]

            synced_count = 0
            for order_data in orders:
                existing = db.query(CustomerOrder).filter(
                    CustomerOrder.mrp_cust_ord_id == order_data.get("cust_ord_id")
                ).first()

                order_obj = {
                    "mrp_cust_ord_id": order_data.get("cust_ord_id"),
                    "code": order_data.get("code"),
                    "reference": order_data.get("reference"),
                    "customer_id": order_data.get("customer_id"),
                    "customer_name": order_data.get("customer_name"),
                    "status": order_data.get("status"),
                    "status_txt": order_data.get("status_txt"),
                    "total_price": order_data.get("total_price"),
                    "total_price_cur": order_data.get("total_price_cur"),
                    "currency": order_data.get("currency"),
                    "notes": order_data.get("notes"),
                }

                if existing:
                    for key, value in order_obj.items():
                        setattr(existing, key, value)
                    existing.synced_at = datetime.utcnow()
                else:
                    db.add(CustomerOrder(**order_obj))
                synced_count += 1

            db.commit()

            # Update sync log
            sync_log = db.query(SyncLog).filter(
                SyncLog.entity_type == "customer_orders"
            ).first()
            if sync_log:
                sync_log.last_sync = datetime.utcnow()
                sync_log.sync_count = synced_count
                sync_log.status = "success"
            else:
                db.add(SyncLog(
                    entity_type="customer_orders",
                    last_sync=datetime.utcnow(),
                    sync_count=synced_count,
                    status="success"
                ))
            db.commit()

            return {"success": True, "synced": synced_count}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def sync_stock_items(db: Session) -> dict:
        """Sync stock items from MRPeasy API"""
        try:
            items = mrpeasy_client.get_stock_items() or []
            if not isinstance(items, list):
                items = [items]

            synced_count = 0
            for item_data in items:
                existing = db.query(StockItem).filter(
                    StockItem.mrp_article_id == item_data.get("article_id")
                ).first()

                item_obj = {
                    "mrp_article_id": item_data.get("article_id"),
                    "product_id": item_data.get("product_id"),
                    "code": item_data.get("code"),
                    "title": item_data.get("title"),
                    "unit_id": item_data.get("unit_id"),
                    "unit": item_data.get("unit"),
                    "group_id": item_data.get("group_id"),
                    "group_title": item_data.get("group_title"),
                    "is_raw": item_data.get("is_raw", False),
                    "selling_price": item_data.get("selling_price"),
                    "avg_cost": item_data.get("avg_cost"),
                    "in_stock": item_data.get("in_stock", 0),
                    "available": item_data.get("available", 0),
                    "booked": item_data.get("booked", 0),
                    "expected_total": item_data.get("expected_total", 0),
                }

                if existing:
                    for key, value in item_obj.items():
                        setattr(existing, key, value)
                    existing.synced_at = datetime.utcnow()
                else:
                    db.add(StockItem(**item_obj))
                synced_count += 1

            db.commit()

            sync_log = db.query(SyncLog).filter(
                SyncLog.entity_type == "stock_items"
            ).first()
            if sync_log:
                sync_log.last_sync = datetime.utcnow()
                sync_log.sync_count = synced_count
                sync_log.status = "success"
            else:
                db.add(SyncLog(
                    entity_type="stock_items",
                    last_sync=datetime.utcnow(),
                    sync_count=synced_count,
                    status="success"
                ))
            db.commit()

            return {"success": True, "synced": synced_count}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def sync_manufacturing_orders(db: Session) -> dict:
        """Sync manufacturing orders from MRPeasy API"""
        try:
            orders = mrpeasy_client.get_manufacturing_orders() or []
            if not isinstance(orders, list):
                orders = [orders]

            synced_count = 0
            for order_data in orders:
                existing = db.query(ManufacturingOrder).filter(
                    ManufacturingOrder.mrp_man_ord_id == order_data.get("man_ord_id")
                ).first()

                order_obj = {
                    "mrp_man_ord_id": order_data.get("man_ord_id"),
                    "code": order_data.get("code"),
                    "article_id": order_data.get("article_id"),
                    "item_code": order_data.get("item_code"),
                    "item_title": order_data.get("item_title"),
                    "quantity": order_data.get("quantity"),
                    "status": order_data.get("status"),
                    "status_txt": order_data.get("status_txt", ""),
                    "due_date": order_data.get("due_date"),
                    "total_cost": order_data.get("total_cost"),
                }

                if existing:
                    for key, value in order_obj.items():
                        setattr(existing, key, value)
                    existing.synced_at = datetime.utcnow()
                else:
                    db.add(ManufacturingOrder(**order_obj))
                synced_count += 1

            db.commit()

            sync_log = db.query(SyncLog).filter(
                SyncLog.entity_type == "manufacturing_orders"
            ).first()
            if sync_log:
                sync_log.last_sync = datetime.utcnow()
                sync_log.sync_count = synced_count
                sync_log.status = "success"
            else:
                db.add(SyncLog(
                    entity_type="manufacturing_orders",
                    last_sync=datetime.utcnow(),
                    sync_count=synced_count,
                    status="success"
                ))
            db.commit()

            return {"success": True, "synced": synced_count}
        except Exception as e:
            return {"success": False, "error": str(e)}
