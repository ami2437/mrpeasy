from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, ForeignKey
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    role = Column(String, nullable=False, default="admin")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class StockItem(Base):
    __tablename__ = "stock_items"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False, index=True)
    title = Column(String, nullable=False)
    unit = Column(String, nullable=True)
    cost_price = Column(Float, nullable=True, default=0)
    selling_price = Column(Float, nullable=True, default=0)
    on_hand = Column(Float, nullable=False, default=0)
    booked = Column(Float, nullable=False, default=0)  # soft-reserved by confirmed, unshipped order lines
    reorder_point = Column(Float, nullable=True, default=0)
    default_pack_size = Column(Integer, nullable=True)  # units per box default; editable per-shipment at packing time
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def available(self) -> float:
        """on_hand not already promised to a confirmed order -- what's actually free to book next."""
        return self.on_hand - self.booked


class Lot(Base):
    __tablename__ = "lots"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=False, index=True)
    lot_code = Column(String, nullable=False, index=True)
    quantity = Column(Float, nullable=False, default=0)  # remaining quantity in this lot
    received_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    expiry_date = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="available")  # available | on_hold | rejected
    source = Column(String, nullable=True)  # purchase | adjustment
    source_reference = Column(String, nullable=True)  # e.g. PO code
    created_at = Column(DateTime, default=datetime.utcnow)


class InventoryTransaction(Base):
    __tablename__ = "inventory_transactions"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=False, index=True)
    lot_id = Column(Integer, ForeignKey("lots.id"), nullable=True, index=True)
    quantity_delta = Column(Float, nullable=False)  # positive = in, negative = out
    type = Column(String, nullable=False)  # receipt | shipment | adjustment
    reference = Column(String, nullable=True)  # e.g. PO-0001 / CO-0001
    note = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    contact_name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    address = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Vendor(Base):
    __tablename__ = "vendors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    contact_name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    address = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CustomerOrder(Base):
    __tablename__ = "customer_orders"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False, index=True)  # CO-0001
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    order_date = Column(DateTime, default=datetime.utcnow)
    delivery_date = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="draft")  # draft | confirmed | shipped | invoiced | cancelled
    po_number = Column(String, nullable=True)  # customer's PO reference -- printed on shipment labels
    job_number = Column(String, nullable=True)  # optional job reference -- printed on shipment labels
    notes = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    lines = relationship("CustomerOrderLine", backref="order", cascade="all, delete-orphan")


class CustomerOrderLine(Base):
    __tablename__ = "customer_order_lines"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("customer_orders.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=False)
    quantity = Column(Float, nullable=False)
    unit_price = Column(Float, nullable=False, default=0)
    delivery_date = Column(DateTime, nullable=True)
    shipped_quantity = Column(Float, nullable=False, default=0)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False, index=True)  # PO-0001
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False)
    order_date = Column(DateTime, default=datetime.utcnow)
    expected_date = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="draft")  # draft | ordered | partially_received | received | cancelled
    freight_cost = Column(Float, nullable=True, default=0)
    tariff_cost = Column(Float, nullable=True, default=0)
    notes = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    lines = relationship("PurchaseOrderLine", backref="po", cascade="all, delete-orphan")


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"

    id = Column(Integer, primary_key=True, index=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=False)
    quantity = Column(Float, nullable=False)
    unit_cost = Column(Float, nullable=False, default=0)
    received_quantity = Column(Float, nullable=False, default=0)


class Shipment(Base):
    """One shipping event against a customer order. Created by the 'Ship' action
    on a CustomerOrder -- may cover all or part of the order's lines."""
    __tablename__ = "shipments"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False, index=True)  # SH-0001
    order_id = Column(Integer, ForeignKey("customer_orders.id"), nullable=False, index=True)
    ship_date = Column(DateTime, default=datetime.utcnow)
    carrier = Column(String, nullable=True)
    tracking_number = Column(String, nullable=True)
    status = Column(String, nullable=False, default="shipped")  # shipped | invoiced
    notes = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    lines = relationship("ShipmentLine", backref="shipment", cascade="all, delete-orphan")
    boxes = relationship("ShipmentBox", backref="shipment", cascade="all, delete-orphan")


class ShipmentLine(Base):
    """What was actually shipped, tied back to the order line and the lot it came from."""
    __tablename__ = "shipment_lines"

    id = Column(Integer, primary_key=True, index=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id"), nullable=False, index=True)
    order_line_id = Column(Integer, ForeignKey("customer_order_lines.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=False)
    lot_id = Column(Integer, ForeignKey("lots.id"), nullable=True)
    quantity = Column(Float, nullable=False)
    unit_price = Column(Float, nullable=False, default=0)


class ShipmentBox(Base):
    """Packing-list / label record: how a shipment's items are split into physical
    boxes for printing box labels. Independent of ShipmentLine so one item's
    shipped quantity can be split across several boxes."""
    __tablename__ = "shipment_boxes"

    id = Column(Integer, primary_key=True, index=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=False)
    box_number = Column(Integer, nullable=False)
    quantity_in_box = Column(Float, nullable=False)
    lot_code = Column(String, nullable=True)
    pallet_number = Column(String, nullable=True)  # grouping for the packing list / freight -- not printed on the label itself
    created_at = Column(DateTime, default=datetime.utcnow)


class Invoice(Base):
    """Native invoice generated from a shipment. No MRP involvement -- this app
    owns invoicing end to end."""
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False, index=True)  # INV-0001
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    order_id = Column(Integer, ForeignKey("customer_orders.id"), nullable=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id"), nullable=True)
    invoice_date = Column(DateTime, default=datetime.utcnow)
    due_date = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="draft")  # draft | sent | paid | void
    free_text = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    lines = relationship("InvoiceLine", backref="invoice", cascade="all, delete-orphan")


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=True)  # null for e.g. a Shipping charge line
    description = Column(String, nullable=False)
    quantity = Column(Float, nullable=False, default=1)
    unit_price = Column(Float, nullable=False, default=0)
