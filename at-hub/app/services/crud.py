from datetime import datetime
from typing import List, Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import (
    StockItem, Lot, InventoryTransaction, Customer, Vendor,
    CustomerOrder, CustomerOrderLine, PurchaseOrder, PurchaseOrderLine,
    Shipment, ShipmentLine, ShipmentBox, Invoice, InvoiceLine,
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
    def update(db: Session, item_id: int, data, created_by: str = None) -> StockItem:
        item = StockItemService.get(db, item_id)
        updates = data.dict(exclude_unset=True)

        new_on_hand = updates.pop("on_hand", None)
        if new_on_hand is not None and abs(new_on_hand - item.on_hand) > 1e-9:
            delta = new_on_hand - item.on_hand
            db.add(InventoryTransaction(
                item_id=item.id,
                lot_id=None,
                quantity_delta=delta,
                type="adjustment",
                reference=None,
                note="Manual on-hand correction via Stock Items edit",
                created_by=created_by,
            ))
            item.on_hand = new_on_hand

        for key, value in updates.items():
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
            po_number=data.po_number,
            job_number=data.job_number,
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
        """Re-derive shipped/confirmed from line data. Never called for draft orders
        (editing lines on a draft shouldn't auto-confirm it) or cancelled ones."""
        if order.status == "cancelled":
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
        db.add(CustomerOrderLine(
            order_id=order.id,
            item_id=data.item_id,
            quantity=data.quantity,
            unit_price=data.unit_price,
            delivery_date=data.delivery_date or order.delivery_date,
        ))
        db.flush()
        if order.status != "draft":
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
            if updates["quantity"] < line.shipped_quantity - 1e-9:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot reduce quantity below {line.shipped_quantity}, which has already shipped"
                )
        for key, value in updates.items():
            setattr(line, key, value)
        db.flush()
        if order.status != "draft":
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
        if line.shipped_quantity > 0:
            raise HTTPException(status_code=400, detail="Cannot remove a line that has already shipped")
        if len(order.lines) <= 1:
            raise HTTPException(status_code=400, detail="Order must have at least one line")
        db.delete(line)
        db.flush()
        if order.status != "draft":
            CustomerOrderService._recompute_status(order)
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def cancel(db: Session, order_id: int) -> CustomerOrder:
        order = CustomerOrderService.get(db, order_id)
        if any(l.shipped_quantity > 0 for l in order.lines):
            raise HTTPException(status_code=400, detail="Cannot cancel an order that has already shipped")
        order.status = "cancelled"
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def ship(db: Session, order_id: int, data, created_by: str) -> Shipment:
        order = CustomerOrderService.get(db, order_id)
        if order.status in ("shipped", "invoiced", "cancelled"):
            raise HTTPException(status_code=400, detail=f"Order is already {order.status}")

        shipment = Shipment(
            code=generate_code(db, Shipment, "SH"),
            order_id=order.id,
            carrier=getattr(data, "carrier", None),
            tracking_number=getattr(data, "tracking_number", None),
            notes=getattr(data, "notes", None),
            created_by=created_by,
        )
        db.add(shipment)
        db.flush()

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
                    reference=shipment.code,
                    created_by=created_by,
                ))
                db.add(ShipmentLine(
                    shipment_id=shipment.id,
                    order_line_id=line.id,
                    item_id=item.id,
                    lot_id=lot.id,
                    quantity=take,
                    unit_price=line.unit_price,
                ))

            item.on_hand -= ship_line.quantity
            line.shipped_quantity += ship_line.quantity

        db.flush()
        CustomerOrderService._recompute_status(order)
        db.commit()
        db.refresh(shipment)
        return shipment


# ---- Shipments (packing list / labels) ----
class ShipmentService:
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
    def set_boxes(db: Session, shipment_id: int, data) -> Shipment:
        """Replace the packing-list/box breakdown for this shipment (used for label printing)."""
        shipment = ShipmentService.get(db, shipment_id)

        shipped_by_item = {}
        for line in shipment.lines:
            shipped_by_item[line.item_id] = shipped_by_item.get(line.item_id, 0) + line.quantity

        boxed_by_item = {}
        for box in data.boxes:
            boxed_by_item[box.item_id] = boxed_by_item.get(box.item_id, 0) + box.quantity_in_box

        for item_id, shipped_qty in shipped_by_item.items():
            boxed_qty = boxed_by_item.get(item_id, 0)
            if abs(boxed_qty - shipped_qty) > 1e-9:
                item = db.query(StockItem).filter(StockItem.id == item_id).first()
                raise HTTPException(
                    status_code=400,
                    detail=f"Boxed quantity for {item.code if item else item_id} ({boxed_qty}) "
                           f"must equal shipped quantity ({shipped_qty})"
                )

        db.query(ShipmentBox).filter(ShipmentBox.shipment_id == shipment.id).delete()
        for box in data.boxes:
            db.add(ShipmentBox(
                shipment_id=shipment.id,
                item_id=box.item_id,
                box_number=box.box_number,
                quantity_in_box=box.quantity_in_box,
                lot_code=box.lot_code,
                pallet_number=box.pallet_number,
            ))
        db.commit()
        db.refresh(shipment)
        return shipment


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
        if db.query(Invoice).filter(Invoice.shipment_id == shipment.id).first():
            raise HTTPException(status_code=400, detail=f"Shipment {shipment.code} has already been invoiced")

        order = db.query(CustomerOrder).filter(CustomerOrder.id == shipment.order_id).first()

        invoice = Invoice(
            code=generate_code(db, Invoice, "INV"),
            customer_id=order.customer_id,
            order_id=order.id,
            shipment_id=shipment.id,
            due_date=data.due_date,
            free_text=data.free_text or "Generated via AT-HUB",
            status="draft",
            created_by=created_by,
        )
        db.add(invoice)
        db.flush()

        # Aggregate shipment lines by item so multi-lot shipments collapse to one invoice line per item.
        totals_by_item = {}
        for line in shipment.lines:
            totals_by_item[line.item_id] = totals_by_item.get(line.item_id, {"quantity": 0, "unit_price": line.unit_price})
            totals_by_item[line.item_id]["quantity"] += line.quantity

        for item_id, agg in totals_by_item.items():
            item = db.query(StockItem).filter(StockItem.id == item_id).first()
            db.add(InvoiceLine(
                invoice_id=invoice.id,
                item_id=item_id,
                description=f"{item.code} — {item.title}" if item else f"Item {item_id}",
                quantity=agg["quantity"],
                unit_price=agg["unit_price"],
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
        invoice.status = status
        db.commit()
        db.refresh(invoice)
        return invoice


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
        if not db.query(StockItem).filter(StockItem.id == data.item_id).first():
            raise HTTPException(status_code=400, detail=f"Stock item {data.item_id} not found")
        db.add(PurchaseOrderLine(po_id=po.id, item_id=data.item_id, quantity=data.quantity, unit_cost=data.unit_cost))
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
        for key, value in updates.items():
            setattr(line, key, value)
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
        po.status = "cancelled"
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
