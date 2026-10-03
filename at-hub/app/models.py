from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
from typing import Optional

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    role = Column(String, nullable=False, default="employee")  # super_admin | admin | manager | employee
    email = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    must_change_password = Column(Boolean, default=False)  # set on creation / reset; user picks their own at next login
    last_login = Column(DateTime, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ProductGroup(Base):
    """Predefined item groups (BOLT, WASHER...), like MRPeasy's product groups.
    StockItem.category holds the group name."""
    __tablename__ = "product_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class StockItem(Base):
    __tablename__ = "stock_items"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False, index=True)
    title = Column(String, nullable=False)
    unit = Column(String, nullable=True)
    category = Column(String, nullable=True, index=True)  # product group name (see ProductGroup)
    created_via = Column(String, nullable=True)  # "ai-scan": made from a scanned customer PO -- worth a second look
    verified_by = Column(String, nullable=True)  # "who, when" a person checked an ai-scan item; until then it can't be picked
    barcode = Column(String, nullable=True, index=True)
    cost_price = Column(Float, nullable=True, default=0)
    selling_price = Column(Float, nullable=True, default=0)
    on_hand = Column(Float, nullable=False, default=0)
    booked = Column(Float, nullable=False, default=0)  # reserved by open (new/ready) shipments, released when they ship or are cancelled
    reorder_point = Column(Float, nullable=True, default=0)
    default_pack_size = Column(Integer, nullable=True)  # units per box default; editable per-shipment at packing time
    is_active = Column(Boolean, default=True)
    mrp_id = Column(Integer, nullable=True, index=True)  # id in MRPeasy, for records imported from it
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def available(self) -> float:
        """on_hand not already promised to a confirmed order -- what's actually free to book next."""
        return self.on_hand - self.booked


class PackSizeHistory(Base):
    """Every change to an item's default pack size. The item keeps the newest as its
    default; this keeps the earlier ones so they can still be looked up or reused."""
    __tablename__ = "pack_size_history"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=False, index=True)
    pack_size = Column(Integer, nullable=True)
    previous_pack_size = Column(Integer, nullable=True)
    source = Column(String, nullable=True)  # item edit | bulk paste | batch packing
    reference = Column(String, nullable=True)  # e.g. shipment codes it was pasted for
    changed_by = Column(String, nullable=True)
    changed_at = Column(DateTime, default=datetime.utcnow)


class Lot(Base):
    __tablename__ = "lots"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=False, index=True)
    lot_code = Column(String, nullable=False, index=True)
    quantity = Column(Float, nullable=False, default=0)  # remaining quantity in this lot
    initial_quantity = Column(Float, nullable=True)  # quantity the lot was created with
    base_unit_cost = Column(Float, nullable=True)  # acquisition cost per unit: PO line unit_cost, or what the user entered on an adjustment
    unit_cost = Column(Float, nullable=True)  # landed cost per unit: base_unit_cost + landed costs allocated to its PO line
    po_line_id = Column(Integer, ForeignKey("purchase_order_lines.id"), nullable=True, index=True)  # the receipt this lot came from
    received_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    expiry_date = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="available")  # available | on_hold | rejected
    source = Column(String, nullable=True)  # purchase | adjustment
    source_reference = Column(String, nullable=True)  # e.g. PO code
    mrp_id = Column(Integer, nullable=True, index=True)  # id in MRPeasy, for records imported from it
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def landed_cost_per_unit(self) -> float:
        if self.unit_cost is None or self.base_unit_cost is None:
            return 0
        return self.unit_cost - self.base_unit_cost


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


class ContactCardMixin:
    """iOS-style contact card shared by customers and vendors: any number of labelled phones, emails,
    addresses, websites and people, plus tags and notes. The single fields (phone, email, contact_name,
    address, shipping_address) stay as the card's "main" values, since documents and emails read them."""
    details_json = Column(Text, nullable=True)  # JSON: tags, labelled phones/emails/addresses/websites, people, notes

    DETAIL_LISTS = ("phones", "emails", "addresses", "websites", "people")

    @property
    def details(self) -> dict:
        """The contact card. Records saved before it existed get one built from their single fields."""
        import json
        if self.details_json:
            d = json.loads(self.details_json)
        else:
            d = {"phones": [{"label": "work", "value": self.phone}] if self.phone else [],
                 "emails": [{"label": "work", "value": self.email}] if self.email else [],
                 "addresses": ([{"label": "billing", "value": self.address}] if self.address else [])
                              + ([{"label": "shipping", "value": self.shipping_address}] if self.shipping_address else []),
                 "people": [{"name": self.contact_name, "role": "", "phone": "", "email": ""}] if self.contact_name else []}
        for key in self.DETAIL_LISTS:
            d.setdefault(key, [])
        d.setdefault("tags", [])
        d.setdefault("notes", "")
        return d

    @details.setter
    def details(self, d: dict) -> None:
        """Save the card and keep the single fields the rest of the app reads (PDFs, ship-to, emails) in step:
        the first phone / email / person, the billing address and the shipping address."""
        import json
        d = dict(d or {})
        clean = lambda rows: [r for r in (rows or []) if any(str(v or "").strip() for k, v in r.items() if k != "label" and k != "role")]
        for key in self.DETAIL_LISTS:
            d[key] = clean(d.get(key))
        tags = {}
        for t in d.get("tags") or []:
            if t and t.strip():
                tags.setdefault(t.strip().lower(), t.strip())  # "Net 30" and "net 30" are one tag
        d["tags"] = sorted(tags.values(), key=str.lower)
        self.details_json = json.dumps(d)
        by_label = lambda rows, word: next((r["value"] for r in rows if word in (r.get("label") or "").lower()), None)
        self.phone = d["phones"][0]["value"] if d["phones"] else None
        self.email = d["emails"][0]["value"] if d["emails"] else None
        self.contact_name = d["people"][0].get("name") if d["people"] else None
        self.address = by_label(d["addresses"], "bill") or (d["addresses"][0]["value"] if d["addresses"] else None)
        self.shipping_address = by_label(d["addresses"], "ship") or by_label(d["addresses"], "pickup")

    def email_for(self, purpose: str) -> Optional[str]:
        """The email labelled for this purpose ("invoice", "mtr"), else the main one."""
        words = {"invoice": ("invoice", "ap", "accounts payable", "billing"), "mtr": ("mtr", "quality", "qa", "cert"),
                 "po": ("purchasing", "orders", "order", "po"), "remit": ("remit", "accounts receivable", "ar", "payments")}[purpose]
        for r in self.details["emails"]:
            label = (r.get("label") or "").lower()
            if any(w == label or w in label.replace("/", " ").split() or (len(w) > 3 and w in label) for w in words):
                return r["value"]
        return self.email

    @property
    def po_email(self) -> Optional[str]:
        return self.email_for("po")

    @property
    def invoice_email(self) -> Optional[str]:
        return self.email_for("invoice")

    @property
    def mtr_email(self) -> Optional[str]:
        return self.email_for("mtr")


class Customer(ContactCardMixin, Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    contact_name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    address = Column(Text, nullable=True)  # billing address
    shipping_address = Column(Text, nullable=True)  # default ship-to; blank = same as billing
    mrp_id = Column(Integer, nullable=True, index=True)  # id in MRPeasy, for records imported from it
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Vendor(ContactCardMixin, Base):
    __tablename__ = "vendors"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, nullable=True, index=True)  # V-0001, assigned on create; keys the vendor part # mapping
    name = Column(String, nullable=False)
    contact_name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    address = Column(Text, nullable=True)
    shipping_address = Column(Text, nullable=True)  # ship-from / pickup address; blank = same as main address
    mrp_id = Column(Integer, nullable=True, index=True)  # id in MRPeasy, for records imported from it
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
    customer_po_date = Column(DateTime, nullable=True)  # when the customer issued their PO (MRPeasy custom_218)
    job_number = Column(String, nullable=True)  # optional job reference -- printed on shipment labels
    ship_to_address = Column(Text, nullable=True)  # this order's delivery address; defaults from the customer
    mrp_id = Column(Integer, nullable=True, index=True)  # id in MRPeasy, for records imported from it
    custom_fields = Column(Text, nullable=True)  # JSON: MRPeasy custom fields kept as imported ({"label": value})
    notes = Column(Text, nullable=True)
    duplicate_po_ok = Column(String, nullable=True)  # "who, when" a manager OK'd sharing this customer PO # with an earlier order
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    lines = relationship("CustomerOrderLine", backref="order", cascade="all, delete-orphan",
                         order_by="[CustomerOrderLine.position, CustomerOrderLine.id]")


class CustomerOrderLine(Base):
    __tablename__ = "customer_order_lines"

    id = Column(Integer, primary_key=True, index=True)
    position = Column(Integer, nullable=True)  # display order on the order (drag to reorder); the line # never changes
    order_id = Column(Integer, ForeignKey("customer_orders.id"), nullable=False, index=True)
    line_no = Column(Integer, nullable=True)  # 1, 2, 3... within the order; never reused, so "#2" always means the same line
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=False)
    quantity = Column(Float, nullable=False)
    unit_price = Column(Float, nullable=False, default=0)
    delivery_date = Column(DateTime, nullable=True)
    shipped_quantity = Column(Float, nullable=False, default=0)
    mrp_id = Column(Integer, nullable=True, index=True)  # id in MRPeasy, for records imported from it
    notes = Column(Text, nullable=True)  # free-text note for this line; carried to packing lists / invoices
    print_notes = Column(Boolean, nullable=True, default=True)  # False = internal note, kept off printouts

    shipment_lines = relationship("ShipmentLine", backref="order_line")

    def _active_shipment_lines(self):
        return [sl for sl in self.shipment_lines if sl.shipment.status != "cancelled"]

    @property
    def booked_quantity(self) -> float:
        """Booked into shipments that haven't shipped yet."""
        return sum(sl.quantity for sl in self._active_shipment_lines() if sl.shipment.status in ("new", "ready"))

    @property
    def allocated_quantity(self) -> float:
        """Shipped + booked -- what's already spoken for by a shipment."""
        return self.shipped_quantity + self.booked_quantity

    @property
    def line_status(self) -> str:
        if self.shipped_quantity >= self.quantity - 1e-9:
            return "shipped"
        if self.shipped_quantity > 0:
            return "partially_shipped"
        booked = self.booked_quantity
        if booked >= self.quantity - 1e-9:
            return "booked"
        if booked > 0:
            return "partially_booked"
        return "not_booked"

    @property
    def shipments(self) -> list:
        """Per-shipment breakdown of this line: which shipments its quantity sits in, and how it's boxed."""
        by_shipment = {}
        for sl in self._active_shipment_lines():
            sh = sl.shipment
            entry = by_shipment.setdefault(sh.id, {
                "shipment_id": sh.id, "code": sh.code, "status": sh.status,
                "quantity": 0, "picked_quantity": 0,
                "boxes": sum(1 for b in sh.boxes if b.order_line_id == self.id),
            })
            entry["quantity"] += sl.quantity
            entry["picked_quantity"] += sl.picked_quantity or 0
        return list(by_shipment.values())

    @property
    def booking_sources(self) -> list:
        """Where this line's booked/shipped stock came from: one entry per lot,
        e.g. {lot_code: LOT-00012, source: purchase, reference: PO-0003, quantity: 100}."""
        by_lot = {}
        for sl in self._active_shipment_lines():
            lot = sl.lot
            key = lot.id if lot else None
            entry = by_lot.setdefault(key, {
                "lot_id": key,
                "lot_code": lot.lot_code if lot else None,
                "source": (lot.source if lot else None) or "manual",
                "reference": lot.source_reference if lot else None,
                "quantity": 0,
            })
            entry["quantity"] += sl.quantity
        return list(by_lot.values())


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
    mrp_id = Column(Integer, nullable=True, index=True)  # id in MRPeasy, for records imported from it
    custom_fields = Column(Text, nullable=True)  # JSON: MRPeasy custom fields kept as imported ({"label": value})
    vendor_so_number = Column(String, nullable=True, index=True)  # the vendor's sales order / confirmation # (MRPeasy "order_number")
    notes = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    lines = relationship("PurchaseOrderLine", backref="po", cascade="all, delete-orphan",
                         order_by="[PurchaseOrderLine.position, PurchaseOrderLine.id]")
    payments = relationship("PurchaseOrderPayment", backref="po", cascade="all, delete-orphan")
    emails = relationship("PurchaseOrderEmail", cascade="all, delete-orphan", order_by="PurchaseOrderEmail.sent_at.desc()")
    bills = relationship("VendorBill", cascade="all, delete-orphan", order_by="VendorBill.bill_date")
    charges = relationship("PurchaseOrderCharge", cascade="all, delete-orphan", order_by="PurchaseOrderCharge.id")

    @property
    def landed_cost_total(self) -> float:
        return sum(a.amount for l in self.lines for a in l.allocations)

    @property
    def lines_total(self) -> float:
        from app.services.money import total
        return total(self.lines, "unit_cost")

    @property
    def charges_total(self) -> float:
        from app.services.money import cents
        return cents(sum(c.amount for c in self.charges))

    @property
    def order_total(self) -> float:
        """What we owe the vendor: lines + freight/shipping/handling charges (+ pre-landed-cost legacy fees)."""
        from app.services.money import cents
        return cents(self.lines_total + self.charges_total + (self.freight_cost or 0) + (self.tariff_cost or 0))

    @property
    def amount_paid(self) -> float:
        return round(sum(p.amount for p in self.payments), 2)


class PurchaseOrderPayment(Base):
    """A payment made to the vendor against this purchase order."""
    __tablename__ = "purchase_order_payments"

    id = Column(Integer, primary_key=True, index=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    currency = Column(String, nullable=True)
    paid_date = Column(DateTime, nullable=True)
    method = Column(String, nullable=True)  # wire, check, card, ach, ...
    reference = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    vendor_bill_id = Column(Integer, ForeignKey("vendor_bills.id"), nullable=True, index=True)  # which vendor invoice this pays
    vendor_payment_id = Column(Integer, ForeignKey("vendor_payments.id"), nullable=True, index=True)  # applied from a payment made before/without a PO
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class VendorBill(Base):
    """A vendor's invoice against a purchase order. A PO can be billed in several invoices;
    payments are applied to a specific one so each invoice shows what's paid and what remains."""
    __tablename__ = "vendor_bills"

    id = Column(Integer, primary_key=True, index=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False, index=True)
    bill_number = Column(String, nullable=False)  # the vendor's invoice #
    bill_date = Column(DateTime, nullable=True)
    due_date = Column(DateTime, nullable=True)
    amount = Column(Float, nullable=False, default=0)
    note = Column(Text, nullable=True)
    attachment_id = Column(Integer, ForeignKey("attachments.id"), nullable=True)  # the uploaded invoice PDF
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    payments = relationship("PurchaseOrderPayment", backref="bill")

    @property
    def amount_paid(self) -> float:
        return round(sum(p.amount for p in self.payments), 2)

    @property
    def balance(self) -> float:
        return round(self.amount - self.amount_paid, 2)


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"

    id = Column(Integer, primary_key=True, index=True)
    position = Column(Integer, nullable=True)  # display order on the order (drag to reorder); the line # never changes
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=False)
    quantity = Column(Float, nullable=False)
    unit_cost = Column(Float, nullable=False, default=0)
    received_quantity = Column(Float, nullable=False, default=0)
    vendor_item_code = Column(String, nullable=True)  # the vendor's part # -- what the vendor-facing PO shows
    vendor_description = Column(String, nullable=True)
    mrp_id = Column(Integer, nullable=True, index=True)  # id in MRPeasy, for records imported from it
    planned_lot_code = Column(String, nullable=True)  # lot # already assigned before receipt (MRPeasy does this); used when it arrives
    notes = Column(Text, nullable=True)  # free-text note for this line; carried to packing lists / invoices
    print_notes = Column(Boolean, nullable=True, default=True)  # False = internal note, kept off printouts

    allocations = relationship("LandedCostAllocation", backref="po_line")

    @property
    def landed_cost_per_unit(self) -> float:
        return sum(a.per_unit for a in self.allocations)


class VendorItem(Base):
    """Cross-reference: what a vendor calls one of our items (our ITEM100 = their B45).
    Learned from purchase orders and editable on the vendor page; typing the vendor's #
    on a PO picks our item."""
    __tablename__ = "vendor_items"
    __table_args__ = (UniqueConstraint("vendor_id", "vendor_item_code", name="uq_vendor_item_code"),)

    id = Column(Integer, primary_key=True, index=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=False, index=True)
    vendor_item_code = Column(String, nullable=False)
    vendor_description = Column(String, nullable=True)
    last_unit_cost = Column(Float, nullable=True)
    last_ordered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PurchaseOrderEmail(Base):
    """Log of every time a purchase order was emailed to the vendor."""
    __tablename__ = "purchase_order_emails"

    id = Column(Integer, primary_key=True, index=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False, index=True)
    to_address = Column(String, nullable=False)
    cc_address = Column(String, nullable=True)
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=True)
    sent_by = Column(String, nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow)


class ShipmentEmail(Base):
    """Log of proof-of-delivery emails sent to the customer for a shipment."""
    __tablename__ = "shipment_emails"

    id = Column(Integer, primary_key=True, index=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id"), nullable=False, index=True)
    to_address = Column(String, nullable=False)
    cc_address = Column(String, nullable=True)
    subject = Column(String, nullable=False)
    files = Column(Text, nullable=True)  # file names that were attached
    sent_by = Column(String, nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow)


class Attachment(Base):
    """A file attached to a record: customer PO PDFs on orders, vendor invoices and
    material test reports on purchase orders, proof-of-delivery photos on shipments.
    The file itself lives on disk under settings.upload_dir; this row is its metadata."""
    __tablename__ = "attachments"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String, nullable=False, index=True)  # customer_order | purchase_order | shipment
    entity_id = Column(Integer, nullable=False, index=True)
    category = Column(String, nullable=False)  # customer_po | vendor_invoice | mtr | pod | other
    filename = Column(String, nullable=False)  # original name, for display/download
    stored_name = Column(String, nullable=False)  # path relative to upload_dir
    content_type = Column(String, nullable=True)
    size = Column(Integer, nullable=False, default=0)
    note = Column(Text, nullable=True)  # e.g. heat # on an MTR, who signed for a delivery
    uploaded_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class LandedCost(Base):
    """A freight / tariff / customs / brokerage bill applied on top of one or more
    purchase orders. Its amount is spread over the selected POs' lines by quantity,
    and every lot received from those lines carries its share in unit_cost."""
    __tablename__ = "landed_costs"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False, index=True)  # LC-0001
    description = Column(String, nullable=False)
    cost_type = Column(String, nullable=False, default="freight")  # freight | tariff | customs | brokerage | insurance | other
    amount = Column(Float, nullable=False, default=0)
    paid_to = Column(String, nullable=True)  # carrier / broker name
    reference = Column(String, nullable=True)  # their bill / invoice #
    cost_date = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    allocations = relationship("LandedCostAllocation", backref="landed_cost", cascade="all, delete-orphan")


class LandedCostAllocation(Base):
    """The share of one landed cost carried by one PO line, by that line's quantity."""
    __tablename__ = "landed_cost_allocations"

    id = Column(Integer, primary_key=True, index=True)
    landed_cost_id = Column(Integer, ForeignKey("landed_costs.id"), nullable=False, index=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False, index=True)
    po_line_id = Column(Integer, ForeignKey("purchase_order_lines.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=False)
    quantity = Column(Float, nullable=False)  # the line quantity the split was based on
    amount = Column(Float, nullable=False)

    @property
    def per_unit(self) -> float:
        return self.amount / self.quantity if self.quantity else 0


class Shipment(Base):
    """One shipping event against a customer order, covering all or part of its lines.

    Lifecycle: new (items booked from specific lots) -> ready (bookings confirmed,
    picking can start) -> shipped (every line fully picked; stock leaves on_hand)
    -> invoiced. new/ready shipments can be cancelled, releasing their bookings."""
    __tablename__ = "shipments"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False, index=True)  # SH-0001
    order_id = Column(Integer, ForeignKey("customer_orders.id"), nullable=False, index=True)
    ship_date = Column(DateTime, nullable=True)  # set when the shipment actually ships
    delivered_at = Column(DateTime, nullable=True)  # when the customer received it (POD upload or marked by a manager)
    delivered_by = Column(String, nullable=True)  # who recorded the delivery
    carrier = Column(String, nullable=True)
    tracking_number = Column(String, nullable=True)
    shipping_cost = Column(Float, nullable=True)  # what WE pay the carrier -- separate from what we invoice the customer
    mrp_id = Column(Integer, nullable=True, index=True)  # id in MRPeasy, for records imported from it
    custom_fields = Column(Text, nullable=True)  # JSON: MRPeasy custom fields kept as imported ({"label": value})
    status = Column(String, nullable=False, default="new")  # new | ready | shipped | delivered | invoiced | cancelled
    notes = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    lines = relationship("ShipmentLine", backref="shipment", cascade="all, delete-orphan")
    boxes = relationship("ShipmentBox", backref="shipment", cascade="all, delete-orphan")
    pallets = relationship("PalletWeight", backref="shipment", cascade="all, delete-orphan")


class ShipmentLine(Base):
    """Quantity booked into this shipment, tied back to the order line and the lot it's booked from."""
    __tablename__ = "shipment_lines"

    id = Column(Integer, primary_key=True, index=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id"), nullable=False, index=True)
    order_line_id = Column(Integer, ForeignKey("customer_order_lines.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=False)
    lot_id = Column(Integer, ForeignKey("lots.id"), nullable=True)
    quantity = Column(Float, nullable=False)  # booked quantity
    picked_quantity = Column(Float, nullable=False, default=0)
    unit_price = Column(Float, nullable=False, default=0)

    lot = relationship("Lot")

    @property
    def line_no(self):
        return self.order_line.line_no if self.order_line else None


class ShipmentBox(Base):
    """Packing-list / label record: how a shipment's items are split into physical
    boxes for printing box labels. Independent of ShipmentLine so one item's
    shipped quantity can be split across several boxes."""
    __tablename__ = "shipment_boxes"

    id = Column(Integer, primary_key=True, index=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id"), nullable=False, index=True)
    order_line_id = Column(Integer, ForeignKey("customer_order_lines.id"), nullable=True, index=True)  # boxes are packed per order line
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=False)
    box_number = Column(Integer, nullable=False)
    quantity_in_box = Column(Float, nullable=False)
    lot_code = Column(String, nullable=True)
    pallet_number = Column(String, nullable=True)  # grouping for the packing list / freight -- not printed on the label itself
    created_at = Column(DateTime, default=datetime.utcnow)


class PalletWeight(Base):
    """Weight/dimensions entered per pallet number within one shipment's packing list."""
    __tablename__ = "pallet_weights"

    id = Column(Integer, primary_key=True, index=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id"), nullable=False, index=True)
    pallet_number = Column(String, nullable=False)
    weight = Column(Float, nullable=True)
    dimensions = Column(String, nullable=True)  # free text, e.g. "48x40x36 in"
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class InvoiceShipment(Base):
    """Which shipments an invoice bills. Usually one; several when shipments of the same
    order are combined into one invoice. (Invoice.shipment_id keeps the first one.)"""
    __tablename__ = "invoice_shipments"

    invoice_id = Column(Integer, ForeignKey("invoices.id"), primary_key=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id"), primary_key=True, index=True)


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
    # Factoring / funding report fields (the portal's custom_570/571/572), filled by the
    # bulk funding upload or by hand. funding_amount + funding_discount should equal the total.
    disbursement_date = Column(DateTime, nullable=True)
    funding_amount = Column(Float, nullable=True)
    funding_discount = Column(Float, nullable=True)
    funding_import_id = Column(Integer, ForeignKey("funding_imports.id"), nullable=True, index=True)
    print_zero_lines = Column(Boolean, default=False)  # $0 lines are left off the PDF unless this is ticked
    # JSON record of how this invoice was combined, so it can be shown and undone:
    # {"merged": [{"code", "shipment_ids", "line_ids", "due_date", "free_text"}], "by", "at"}
    combined_info = Column(Text, nullable=True)
    mrp_id = Column(Integer, nullable=True, index=True)  # id in MRPeasy, for records imported from it
    custom_fields = Column(Text, nullable=True)  # JSON: MRPeasy custom fields kept as imported ({"label": value})
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    shipments = relationship("Shipment", secondary="invoice_shipments", order_by="Shipment.id")
    lines = relationship("InvoiceLine", backref="invoice", cascade="all, delete-orphan")
    payments = relationship("InvoicePayment", backref="invoice", cascade="all, delete-orphan")
    emails = relationship("InvoiceEmail", backref="invoice", cascade="all, delete-orphan", order_by="InvoiceEmail.sent_at")

    @property
    def total(self) -> float:
        """Sum of the lines, each rounded to the cent (services/money.py)."""
        from app.services.money import total
        return total(self.lines)

    @property
    def amount_paid(self) -> float:
        from app.services.money import cents
        return cents(sum(p.amount for p in self.payments))

    @property
    def shipment_ids(self) -> list:
        return [s.id for s in self.shipments]

    @property
    def shipment_codes(self) -> list:
        return [s.code for s in self.shipments]

    @property
    def is_combined(self) -> bool:
        return len(self.shipments) > 1

    @property
    def combined_from(self) -> list:
        import json
        info = json.loads(self.combined_info) if self.combined_info else {}
        return [m["code"] for m in info.get("merged", [])]

    @property
    def balance(self) -> float:
        from app.services.money import cents
        return cents(self.total - self.amount_paid)


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=True)  # null for e.g. a Shipping charge line
    order_line_id = Column(Integer, ForeignKey("customer_order_lines.id"), nullable=True)  # the order line this bills
    shipment_id = Column(Integer, ForeignKey("shipments.id"), nullable=True)  # which shipment it shipped on (combined invoices)
    description = Column(String, nullable=False)
    quantity = Column(Float, nullable=False, default=1)
    unit_price = Column(Float, nullable=False, default=0)
    notes = Column(Text, nullable=True)  # free-text note for this line; carried to packing lists / invoices
    print_notes = Column(Boolean, nullable=True, default=True)  # False = internal note, kept off printouts

    @property
    def amount(self) -> float:
        from app.services.money import line_amount
        return line_amount(self.quantity, self.unit_price)


class InvoiceEmail(Base):
    """Log of every time an invoice was emailed out."""
    __tablename__ = "invoice_emails"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    to_address = Column(String, nullable=False)
    cc_address = Column(String, nullable=True)
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=True)
    sent_by = Column(String, nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow)


class FundingImport(Base):
    """One bulk funding upload, kept so it can be reviewed and rolled back."""
    __tablename__ = "funding_imports"

    id = Column(Integer, primary_key=True, index=True)
    file_name = Column(String, nullable=True)
    record_payments = Column(Boolean, default=True)
    updated_count = Column(Integer, default=0)
    skipped_count = Column(Integer, default=0)
    summary = Column(Text, nullable=True)  # JSON: skipped matches, discrepancies, invalid rows
    rolled_back = Column(Boolean, default=False)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class InvoicePayment(Base):
    """A payment received from the customer against this invoice."""
    __tablename__ = "invoice_payments"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    paid_date = Column(DateTime, nullable=True)
    method = Column(String, nullable=True)  # wire, check, card, ach, ...
    reference = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    funding_import_id = Column(Integer, ForeignKey("funding_imports.id"), nullable=True, index=True)  # created by a bulk funding upload
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CompanyProfile(Base):
    """Our own company details, printed on invoices. Single row (id=1)."""
    __tablename__ = "company_profile"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, default="American Traders LLC - ATind Supplies")
    address = Column(Text, nullable=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True, default="sales@atindsupplies.com")
    website = Column(String, nullable=True)
    tax_id = Column(String, nullable=True)
    invoice_notes = Column(Text, nullable=True)  # payment instructions / terms printed at the bottom of every invoice
    logo_data = Column(Text, nullable=True)  # data: URL (base64 PNG/JPEG) -- printed on invoices, packing lists, labels

    @property
    def has_logo(self) -> bool:
        return bool(self.logo_data)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MtrLink(Base):
    """Which purchase-order lines a material test report (an 'mtr' attachment on a PO) covers.
    One MTR can cover several lines, but not necessarily all of them. Through the lots received
    from those lines, every shipped unit traces back to its MTR."""
    __tablename__ = "mtr_links"
    __table_args__ = (UniqueConstraint("attachment_id", "po_line_id", name="uq_mtr_link"),)

    id = Column(Integer, primary_key=True, index=True)
    attachment_id = Column(Integer, ForeignKey("attachments.id"), nullable=False, index=True)
    po_line_id = Column(Integer, ForeignKey("purchase_order_lines.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=False, index=True)
    heat_number = Column(String, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class MtrEmail(Base):
    """Log of MTRs emailed to a customer, usually for one of their orders."""
    __tablename__ = "mtr_emails"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("customer_orders.id"), nullable=True, index=True)
    to_address = Column(String, nullable=False)
    cc_address = Column(String, nullable=True)
    subject = Column(String, nullable=False)
    files = Column(Text, nullable=True)  # file names that were attached
    sent_by = Column(String, nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow)


class PurchaseOrderCharge(Base):
    """Freight / shipping / handling the vendor bills on top of the lines -- usually on each
    invoice as it ships. Counted in the PO total so payments reconcile to it."""
    __tablename__ = "purchase_order_charges"

    id = Column(Integer, primary_key=True, index=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False, index=True)
    charge_type = Column(String, nullable=False, default="shipping")  # shipping | freight | handling | other
    amount = Column(Float, nullable=False, default=0)
    description = Column(String, nullable=True)
    vendor_bill_id = Column(Integer, ForeignKey("vendor_bills.id"), nullable=True, index=True)  # the invoice it was billed on
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    bill = relationship("VendorBill")

    @property
    def bill_number(self):
        return self.bill.bill_number if self.bill else None


class VendorPayment(Base):
    """Money sent to a vendor, possibly before any PO exists. Applied to POs in parts
    (each application is a PurchaseOrderPayment pointing back here); the rest stays
    'unapplied' and is offered when a PO for that vendor is opened."""
    __tablename__ = "vendor_payments"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False, index=True)  # VP-0001
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    paid_date = Column(DateTime, nullable=True)
    method = Column(String, nullable=True)
    reference = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    applications = relationship("PurchaseOrderPayment", backref="vendor_payment")

    @property
    def applied(self) -> float:
        return round(sum(a.amount for a in self.applications), 2)

    @property
    def unapplied(self) -> float:
        return round(self.amount - self.applied, 2)


class NumberSeries(Base):
    """How new document numbers are formed, so AT-HUB continues the numbering customers and
    vendors already know (e.g. MRPeasy's C89123 -> C89124). Without a row, a document type
    falls back to AT-HUB's own CO-0001 style."""
    __tablename__ = "number_series"

    key = Column(String, primary_key=True)  # CO | PO | SH | INV | LOT | V
    prefix = Column(String, nullable=False)  # e.g. "C", "PO", "Inv-"
    width = Column(Integer, nullable=False)  # digits, zero-padded


class ActivityLog(Base):
    """Who changed what on an order / PO, and when: one row per successful change request
    (edit, add / change / remove / reorder a line, confirm, cancel...). Written by a middleware in main.py."""
    __tablename__ = "activity_log"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String, nullable=False, index=True)  # customer_order | purchase_order
    entity_id = Column(Integer, nullable=False, index=True)
    method = Column(String, nullable=False)  # PUT / POST / DELETE
    action = Column(String, nullable=False)  # the path after the record id: "", "lines", "lines/1411", "line-order", "cancel"...
    detail = Column(Text, nullable=True)  # the request's JSON body (what was sent), trimmed
    by = Column(String, nullable=True)
    at = Column(DateTime, default=datetime.utcnow, index=True)


class DeletedRecord(Base):
    """Recycle bin: one entry per delete action, holding every row it removed (see services/recycle_bin.py)."""
    __tablename__ = "recycle_bin"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String, nullable=False)  # Customer order, PO line, File...
    label = Column(String, nullable=False)  # C89126, 15423, Birmingham 2605425.pdf...
    rows = Column(Text, nullable=False)  # JSON [{table, cols}] -- everything that went in that one action
    deleted_by = Column(String, nullable=True)
    deleted_at = Column(DateTime, default=datetime.utcnow, index=True)
    restored_at = Column(DateTime, nullable=True)
    restored_by = Column(String, nullable=True)


class ItemAlias(Base):
    """What a vendor or customer called one of our items on their document ("SS 316 HEX BOLT 3/4 X 10",
    "CRB063") -> the item the user picked. Learned every time an order/PO is saved, so the next scan of
    the same wording is matched without asking; picking a different item later re-points it."""
    __tablename__ = "item_aliases"
    __table_args__ = (UniqueConstraint("party_type", "party_id", "kind", "key", name="uq_item_alias"),)

    id = Column(Integer, primary_key=True, index=True)
    party_type = Column(String, nullable=False)  # vendor | customer
    party_id = Column(Integer, nullable=False, index=True)
    kind = Column(String, nullable=False)  # code | desc
    key = Column(String, nullable=False)  # normalised: lowercase letters + digits only
    text = Column(String, nullable=False)  # as it was printed
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=False)
    hits = Column(Integer, nullable=False, default=1)  # times saved with this item
    last_used_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


class Task(Base):
    """A to-do for an admin. Manual ones are typed in; suggested ones (key set) are raised by
    TaskService.refresh() from the data -- e.g. "record the payments on PO325370" -- and close
    themselves once the data shows the work is done."""
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, nullable=True, unique=True)  # suggested tasks: what raised it (never duplicated)
    category = Column(String, nullable=False, default="General")
    title = Column(String, nullable=False)
    detail = Column(Text, nullable=True)
    link = Column(String, nullable=True)  # page to open, e.g. purchase-orders.html?id=7
    status = Column(String, nullable=False, default="open")  # open | done | dismissed
    note = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    done_by = Column(String, nullable=True)
    done_at = Column(DateTime, nullable=True)
