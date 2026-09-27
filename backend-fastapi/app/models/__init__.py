from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, JSON
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()


class User(Base):
    """User Model for Authentication"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    role = Column(String, default="employee")  # employee, admin, super_admin
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Role(Base):
    """Role Model for RBAC"""
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)  # employee, admin, super_admin
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CustomerOrder(Base):
    """Customer Order Model"""
    __tablename__ = "customer_orders"

    id = Column(Integer, primary_key=True, index=True)
    mrp_cust_ord_id = Column(Integer, unique=True, nullable=False)
    code = Column(String, unique=True, nullable=False)
    reference = Column(String, nullable=True)
    customer_id = Column(Integer, nullable=False)
    customer_name = Column(String, nullable=False)
    status = Column(Integer, nullable=False)
    status_txt = Column(String, nullable=False)
    total_price = Column(Float, nullable=True)
    total_price_cur = Column(Float, nullable=True)
    currency = Column(String, nullable=True)
    created = Column(DateTime, default=datetime.utcnow)
    delivery_date = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    synced_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StockItem(Base):
    """Stock Item Model"""
    __tablename__ = "stock_items"

    id = Column(Integer, primary_key=True, index=True)
    mrp_article_id = Column(Integer, unique=True, nullable=False)
    product_id = Column(Integer, nullable=True)
    code = Column(String, unique=True, nullable=False)
    title = Column(String, nullable=False)
    unit_id = Column(Integer, nullable=True)
    unit = Column(String, nullable=True)
    group_id = Column(Integer, nullable=True)
    group_title = Column(String, nullable=True)
    is_raw = Column(Boolean, default=False)
    selling_price = Column(Float, nullable=True)
    avg_cost = Column(Float, nullable=True)
    in_stock = Column(Float, default=0)
    available = Column(Float, default=0)
    booked = Column(Float, default=0)
    expected_total = Column(Float, default=0)
    synced_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ManufacturingOrder(Base):
    """Manufacturing Order Model"""
    __tablename__ = "manufacturing_orders"

    id = Column(Integer, primary_key=True, index=True)
    mrp_man_ord_id = Column(Integer, unique=True, nullable=False)
    code = Column(String, unique=True, nullable=False)
    article_id = Column(Integer, nullable=False)
    item_code = Column(String, nullable=False)
    item_title = Column(String, nullable=False)
    quantity = Column(Float, nullable=False)
    status = Column(Integer, nullable=False)
    status_txt = Column(String, nullable=False)
    due_date = Column(DateTime, nullable=True)
    start_date = Column(DateTime, nullable=True)
    finish_date = Column(DateTime, nullable=True)
    item_cost = Column(Float, nullable=True)
    total_cost = Column(Float, nullable=True)
    created = Column(DateTime, default=datetime.utcnow)
    synced_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Vendor(Base):
    """Vendor Model"""
    __tablename__ = "vendors"

    id = Column(Integer, primary_key=True, index=True)
    mrp_vendor_id = Column(Integer, unique=True, nullable=False)
    code = Column(String, unique=True, nullable=False)
    title = Column(String, nullable=False)
    currency = Column(String, nullable=True)
    tax_rate = Column(Float, default=0)
    payment_period = Column(Integer, nullable=True)
    lead_time = Column(Integer, nullable=True)
    contact_data = Column(Text, nullable=True)  # JSON stored as text
    synced_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Inventory(Base):
    """Inventory Snapshot Model"""
    __tablename__ = "inventory"

    id = Column(Integer, primary_key=True, index=True)
    article_id = Column(Integer, nullable=False)
    item_code = Column(String, nullable=False)
    item_title = Column(String, nullable=False)
    quantity_on_hand = Column(Float, default=0)
    quantity_available = Column(Float, default=0)
    quantity_booked = Column(Float, default=0)
    quantity_expected = Column(Float, default=0)
    unit_cost = Column(Float, nullable=True)
    total_cost = Column(Float, nullable=True)
    snapshot_date = Column(DateTime, default=datetime.utcnow)


class SyncLog(Base):
    """Sync Log for tracking MRPeasy API syncs"""
    __tablename__ = "sync_logs"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String, nullable=False)  # customer_orders, stock_items, etc.
    last_sync = Column(DateTime, nullable=True)
    sync_count = Column(Integer, default=0)
    status = Column(String, default="pending")  # pending, success, failed
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ShipmentBox(Base):
    """Shipment Box Configuration - Stores finalized box data per shipment"""
    __tablename__ = "shipment_boxes"

    id = Column(Integer, primary_key=True, index=True)
    shipment_code = Column(String, nullable=False, index=True)
    customer_order_code = Column(String, nullable=False)
    po_number = Column(String, nullable=True)  # PO/Reference number
    customer_name = Column(String, nullable=True)
    shipping_address = Column(Text, nullable=True)
    job_number = Column(String, nullable=True)
    delivery_date = Column(String, nullable=True)
    item_code = Column(String, nullable=False)
    item_title = Column(String, nullable=False)
    order_line = Column(String, nullable=False, default="1")
    pack_size = Column(Integer, nullable=False)
    box_number = Column(Integer, nullable=False)  # Box 1, Box 2, etc.
    quantity_in_box = Column(Integer, nullable=False)  # Qty in this specific box
    total_quantity = Column(Integer, nullable=False)  # Total qty for this item across all boxes
    lot_codes = Column(Text, nullable=True)  # JSON array of lot codes
    pallet_number = Column(String, nullable=True)  # For grouping multiple shipments on same pallet
    generated_from = Column(String, nullable=True)  # individual or grouped label mode
    finalized_at = Column(DateTime, default=datetime.utcnow, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PendingShipmentFinalization(Base):
    """Employee-submitted finalize request awaiting admin/super_admin approval."""
    __tablename__ = "pending_shipment_finalizations"

    id = Column(Integer, primary_key=True, index=True)
    shipment_code = Column(String, nullable=False, index=True)
    pallet_number = Column(String, nullable=True)
    product_configs = Column(Text, nullable=False)  # JSON: same payload finalize accepts
    status = Column(String, nullable=False, default="pending", index=True)  # pending, approved, rejected
    submitted_by = Column(String, nullable=True)
    submitted_at = Column(DateTime, default=datetime.utcnow)
    reviewed_by = Column(String, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    rejection_reason = Column(String, nullable=True)


class PendingInvoiceSubmission(Base):
    """A staged 'send this shipment's invoice to MRP' request awaiting confirmation."""
    __tablename__ = "pending_invoice_submissions"

    id = Column(Integer, primary_key=True, index=True)
    cust_ord_id = Column(Integer, nullable=False)
    order_code = Column(String, nullable=True)
    customer_id = Column(Integer, nullable=True)
    pricelist_id = Column(Integer, nullable=True)
    invoice_type = Column(Integer, nullable=False, default=30)
    invoice_status = Column(Integer, nullable=False, default=10)  # stored per-submission, not hardcoded
    free_text = Column(Text, nullable=True)
    due_date = Column(String, nullable=True)
    payload_json = Column(Text, nullable=False)  # the exact frozen InvoiceInput body
    status = Column(String, nullable=False, default="pending", index=True)  # pending, approved, rejected, failed
    submitted_by = Column(String, nullable=True)
    submitted_at = Column(DateTime, default=datetime.utcnow)
    reviewed_by = Column(String, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    rejection_reason = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    mrp_invoice_id = Column(Integer, nullable=True)
    mrp_invoice_code = Column(String, nullable=True)


class PendingInvoiceSubmissionLine(Base):
    """One shipment/order-line's quantity included in a PendingInvoiceSubmission."""
    __tablename__ = "pending_invoice_submission_lines"

    id = Column(Integer, primary_key=True, index=True)
    submission_id = Column(Integer, nullable=False, index=True)
    shipment_code = Column(String, nullable=True, index=True)
    co_line_id = Column(Integer, nullable=True)
    article_id = Column(Integer, nullable=True)
    item_code = Column(String, nullable=True)
    quantity = Column(Float, nullable=False)
    unit_price = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class PurchaseOrderPayment(Base):
    """A payment recorded locally against an MRPeasy purchase order.

    MRPeasy's own PurchaseOrder has a `bills` array, but it's just three
    free-text-ish fields (invoice_number/invoice_date/due_date), not real
    payment tracking, and purchase orders aren't writable via the API
    anyway -- this table is the actual source of truth for payments.
    """
    __tablename__ = "purchase_order_payments"

    id = Column(Integer, primary_key=True, index=True)
    pur_ord_id = Column(Integer, nullable=False, index=True)  # MRPeasy's numeric PO id
    po_code = Column(String, nullable=True)  # display convenience, e.g. "PO00036"
    amount = Column(Float, nullable=False)
    currency = Column(String, nullable=True)
    paid_date = Column(String, nullable=True)  # 'YYYY-MM-DD'
    method = Column(String, nullable=True)  # e.g. wire, check, card
    reference = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    recorded_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PurchaseOrderNote(Base):
    """A general or delivery note recorded locally against a purchase order.

    Both are just timestamped text entries -- note_type distinguishes them,
    and only delivery notes populate the extra structured fields below.
    """
    __tablename__ = "purchase_order_notes"

    id = Column(Integer, primary_key=True, index=True)
    pur_ord_id = Column(Integer, nullable=False, index=True)
    po_code = Column(String, nullable=True)
    note_type = Column(String, nullable=False, default="general")  # 'general' | 'delivery'
    note_text = Column(Text, nullable=True)
    received_quantity = Column(Float, nullable=True)  # delivery notes only
    carrier = Column(String, nullable=True)  # delivery notes only
    tracking_number = Column(String, nullable=True)  # delivery notes only
    delivery_date = Column(String, nullable=True)  # delivery notes only, 'YYYY-MM-DD'
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PalletWeight(Base):
    """Weight entered per pallet number within a finalized shipment."""
    __tablename__ = "pallet_weights"

    id = Column(Integer, primary_key=True, index=True)
    shipment_code = Column(String, nullable=False, index=True)
    pallet_number = Column(String, nullable=False)
    weight = Column(Float, nullable=True)
    dimensions = Column(String, nullable=True)  # normalized "L x W x H" (inches)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class PackSize(Base):
    """Persistent default pack size mapped to a normalized item code."""
    __tablename__ = "pack_sizes"

    id = Column(Integer, primary_key=True, index=True)
    item_code = Column(String, unique=True, nullable=False, index=True)
    pack_size = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Label(Base):
    """Label Record - Stores individual label data"""
    __tablename__ = "labels"

    id = Column(Integer, primary_key=True, index=True)
    label_id = Column(String, unique=True, nullable=False, index=True)  # Format: shipment-PO-item-date
    shipment_code = Column(String, nullable=False, index=True)
    customer_order_code = Column(String, nullable=False)
    po_number = Column(String, nullable=False)
    item_code = Column(String, nullable=False)
    item_title = Column(String, nullable=False)
    order_line = Column(String, nullable=False, default="1")
    box_number = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)
    pack_size = Column(Integer, nullable=False)
    lot_codes = Column(Text, nullable=True)  # JSON array
    label_mode = Column(String, nullable=True)  # individual or grouped
    generated_at = Column(DateTime, default=datetime.utcnow, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
