from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
from app.services import clock
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
    timezone = Column(String, nullable=True)  # IANA zone, e.g. America/New_York; None = the company's (app/services/clock.py)
    totp_secret = Column(String, nullable=True)  # authenticator-app key (app/services/totp.py); set up on My Account
    totp_enabled = Column(Boolean, nullable=False, default=False)
    print_prefs = Column(Text, nullable=True)    # JSON: Print Options pop-up choices per document type (services/print_options.py)
    recent_json = Column(Text, nullable=True)    # JSON: records this person opened last (Recently Viewed)
    filters_json = Column(Text, nullable=True)   # JSON: saved filters per screen {page: [{name, state}]}

    @property
    def effective_timezone(self) -> str:
        from app.config.settings import settings
        return self.timezone or settings.business_timezone
    last_login = Column(DateTime, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ProductGroup(Base):
    """Predefined item groups (BOLT, WASHER...), like MRPeasy's product groups.
    StockItem.category holds the group name."""
    __tablename__ = "product_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    non_stock = Column(Boolean, nullable=True, default=False)  # services / do-not-sell: POs made only of these are grouped apart
    created_at = Column(DateTime, default=datetime.utcnow)


class StockItem(Base):
    __tablename__ = "stock_items"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False, index=True)
    title = Column(String, nullable=False)
    unit = Column(String, nullable=True)
    category = Column(String, nullable=True, index=True)  # product group name (see ProductGroup)
    parent_item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=True, index=True)  # generic item it draws stock from (15420-NUT <- bulk 5/8 2H nut)
    is_generic = Column(Boolean, nullable=False, default=False)  # bulk stock (58-NUT) that specific items of the same size/spec draw from
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


class PackSizePreset(Base):
    """A named set of pack sizes ("Hudson Tulsa - pallets") to apply to many shipments at once.
    Optionally tied to a customer, so their orders offer it first."""
    __tablename__ = "pack_size_presets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    sizes = Column(Text, nullable=False, default="{}")  # JSON {item code: pack size}
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
    received_date = Column(DateTime, nullable=False, default=datetime.utcnow)  # a moment (UTC)
    expiry_date = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="available")  # available | on_hold | rejected
    source = Column(String, nullable=True)  # purchase | adjustment
    source_reference = Column(String, nullable=True)  # e.g. PO code
    mrp_id = Column(Integer, nullable=True, index=True)  # id in MRPeasy, for records imported from it
    parent_lot_id = Column(Integer, ForeignKey("lots.id"), nullable=True, index=True)  # transferred from this generic item's lot
    parent_lot = relationship("Lot", remote_side="Lot.id", foreign_keys=[parent_lot_id])
    item = relationship("StockItem", foreign_keys=[item_id], viewonly=True)
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
    email_from_id = Column(Integer, nullable=True)  # our address their emails go out from (EmailSender), else the default per kind

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
        """The email labelled for this purpose ("invoice", "mtr", "quote"), else the main one."""
        words = {"quote": ("quote", "quotes", "rfq", "purchasing", "buyer", "orders", "order", "po"),"invoice": ("invoice", "ap", "accounts payable", "billing"), "mtr": ("mtr", "quality", "qa", "cert"),
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

    @property
    def expedited(self) -> bool:
        """This customer's orders ship expedited unless an order says otherwise (contact card)."""
        return bool((self.details or {}).get("expedited"))

    @property
    def payment_terms(self) -> str:
        """Net 15 / 30 / 45 / 60 or Due on Receipt, from the contact card (app/services/terms.py; none = Net 30)."""
        from app.services.terms import terms_of
        return terms_of(self)

    id = Column(Integer, primary_key=True, index=True)
    row_version = Column(Integer, nullable=False, default=1)  # bumped on every change to it or its lines (optimistic locking)
    row_updated_at = Column(DateTime, nullable=True)  # when / by whom it last changed
    updated_by = Column(String, nullable=True)
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
    row_version = Column(Integer, nullable=False, default=1)  # bumped on every change to it or its lines (optimistic locking)
    row_updated_at = Column(DateTime, nullable=True)  # when / by whom it last changed
    updated_by = Column(String, nullable=True)
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
    row_version = Column(Integer, nullable=False, default=1)  # bumped on every change to it or its lines (optimistic locking)
    row_updated_at = Column(DateTime, nullable=True)  # when / by whom it last changed
    updated_by = Column(String, nullable=True)
    code = Column(String, unique=True, nullable=False, index=True)  # CO-0001
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    order_date = Column(DateTime, default=datetime.utcnow)
    delivery_date = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="draft")  # validation (captured, not checked yet) | draft | confirmed | shipped | invoiced | cancelled
    validated_by = Column(String, nullable=True)  # who checked a quick-captured order (and when)
    validated_at = Column(DateTime, nullable=True)
    po_number = Column(String, nullable=True)  # customer's PO reference -- printed on shipment labels
    customer_po_date = Column(DateTime, nullable=True)  # when the customer issued their PO (MRPeasy custom_218)
    job_number = Column(String, nullable=True)  # optional job reference -- printed on shipment labels
    ship_to_address = Column(Text, nullable=True)  # this order's delivery address; defaults from the customer
    mrp_id = Column(Integer, nullable=True, index=True)  # id in MRPeasy, for records imported from it
    custom_fields = Column(Text, nullable=True)  # JSON: MRPeasy custom fields kept as imported ({"label": value})
    notes = Column(Text, nullable=True)
    duplicate_po_ok = Column(String, nullable=True)  # "who, when" a manager OK'd sharing this customer PO # with an earlier order
    lookalike_ok = Column(Text, nullable=True)  # JSON: look-alike orders someone checked and OK'd as separate (services/lookalike.py)
    report_check = Column(Text, nullable=True)  # JSON: last customer open-lines report check of this order (services/open_lines.py)
    expedited = Column(Boolean, nullable=True)  # expedited shipping: charge extra on its invoices (default from the customer's card)
    ai_source = Column(String, nullable=True)  # created from an AI read of this file (File Matcher): shown as "AI READ"
    ai_pending = Column(Text, nullable=True)   # JSON: lines the read couldn't match to an item yet (app/services/ai_pending.py)

    @property
    def ai_pending_lines(self) -> list:
        from app.services.ai_pending import pending
        return pending(self)
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
                "combined": None,
            })
            entry["quantity"] += sl.quantity
            entry["picked_quantity"] += sl.picked_quantity or 0
            for c in sh.combos:  # sent assembled with another line on that shipment (bolt + nut)
                if self.id in (c.lead_line_id, c.member_line_id) and not entry["combined"]:
                    lead = c.lead_line_id == self.id
                    other = c.member_line if lead else c.lead_line
                    entry["combined"] = {"role": "lead" if lead else "member", "with_line_no": other.line_no if other else None,
                                         "with_line_id": other.id if other else None, "units": c.quantity,
                                         "quantity": c.quantity if lead else c.member_quantity, "ratio": c.ratio, "note": c.note}
        return list(by_shipment.values())

    @property
    def booking_sources(self) -> list:
        """Where this line's booked/shipped stock came from: one entry per lot,
        e.g. {lot_code: LOT-00012, source: purchase, reference: PO-0003, quantity: 100}."""
        by_lot = {}
        for sl in self._active_shipment_lines():
            lot = sl.lot
            key = lot.id if lot else None
            parent = lot.parent_lot if lot is not None and lot.parent_lot_id else None
            entry = by_lot.setdefault(key, {
                "lot_id": key,
                "lot_code": lot.lot_code if lot else None,
                "source": (lot.source if lot else None) or "manual",
                "reference": lot.source_reference if lot else None,
                # drawn from generic stock: the generic item + lot it came from (acts like the supplier)
                "from_item_id": parent.item_id if parent else None,
                "from_item_code": parent.item.code if parent and parent.item else None,
                "from_lot_code": parent.lot_code if parent else None,
                "quantity": 0,
            })
            entry["quantity"] += sl.quantity
        return list(by_lot.values())


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True, index=True)
    row_version = Column(Integer, nullable=False, default=1)  # bumped on every change to it or its lines (optimistic locking)
    row_updated_at = Column(DateTime, nullable=True)  # when / by whom it last changed
    updated_by = Column(String, nullable=True)
    code = Column(String, unique=True, nullable=False, index=True)  # PO-0001
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False)
    order_date = Column(DateTime, default=clock.today)  # a calendar date
    expected_date = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="draft")  # validation (captured, not checked yet) | draft | ordered | shipped (vendor shipment in transit, nothing received yet) | partially_received | received | cancelled
    validated_by = Column(String, nullable=True)  # who checked a quick-captured PO (and when)
    validated_at = Column(DateTime, nullable=True)
    ai_source = Column(String, nullable=True)  # created from an AI read of this file (File Matcher)
    ai_pending = Column(Text, nullable=True)   # JSON: lines the read couldn't match to an item yet

    @property
    def ai_pending_lines(self) -> list:
        from app.services.ai_pending import pending
        return pending(self)
    freight_cost = Column(Float, nullable=True, default=0)
    tariff_cost = Column(Float, nullable=True, default=0)
    mrp_id = Column(Integer, nullable=True, index=True)  # id in MRPeasy, for records imported from it
    custom_fields = Column(Text, nullable=True)  # JSON: MRPeasy custom fields kept as imported ({"label": value})
    vendor_so_number = Column(String, nullable=True, index=True)  # the vendor's sales order / confirmation # (MRPeasy "order_number")
    duplicate_so_ok = Column(String, nullable=True)  # "who, when" someone OK'd this vendor SO # also being on another PO of the vendor
    lookalike_ok = Column(Text, nullable=True)  # JSON: look-alike POs someone checked and OK'd as separate (services/lookalike.py)
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
    vendor_shipments = relationship("VendorShipment", cascade="all, delete-orphan", order_by="[VendorShipment.shipped_date, VendorShipment.id]")

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


class EmailSender(Base):
    """An address AT-HUB sends from (robert.jones@, accounting@...). Logs in with its own mailbox password, or uses the
    server's shared login (a sending service with the domain verified). The password is kept encrypted
    (services/mail_secret.py) and never sent back to a screen. status: ok | failing | unknown -- checked daily."""
    __tablename__ = "email_senders"

    id = Column(Integer, primary_key=True, index=True)
    address = Column(String, nullable=False, unique=True)
    display_name = Column(String, nullable=True)
    reply_to = Column(String, nullable=True)
    signature = Column(Text, nullable=True)
    bcc_me = Column(Boolean, nullable=False, default=False)
    login = Column(String, nullable=False, default="server")  # server (shared login, .env SMTP_*) | own (this mailbox)
    smtp_host = Column(String, nullable=True)
    smtp_port = Column(Integer, nullable=True)
    smtp_security = Column(String, nullable=True)  # starttls | ssl | none
    smtp_username = Column(String, nullable=True)
    password_enc = Column(Text, nullable=True)
    imap_host = Column(String, nullable=True)
    imap_port = Column(Integer, nullable=True)
    save_sent = Column(Boolean, nullable=False, default=True)  # a copy in this mailbox's Sent folder (own login)
    sent_folder = Column(String, nullable=True)
    read_inbox = Column(Boolean, nullable=False, default=False)  # replies onto records, attachments to the AI Desk
    inbox_folder = Column(String, nullable=True, default="INBOX")
    attachments_to_desk = Column(Boolean, nullable=False, default=True)
    imap_uidvalidity = Column(String, nullable=True)
    imap_last_uid = Column(Integer, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    status = Column(String, nullable=False, default="unknown")
    last_error = Column(Text, nullable=True)
    failing_since = Column(DateTime, nullable=True)
    last_ok_at = Column(DateTime, nullable=True)
    last_check_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class EmailLog(Base):
    """Every email AT-HUB sent (or tried to): the Emails page, and the record's email history. A failed one keeps
    its message (raw_path) so it can be sent again once the address works."""
    __tablename__ = "email_log"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String, nullable=True, index=True)  # invoice | statement | purchase_order | quote | pod | mtr | test
    record_type = Column(String, nullable=True, index=True)
    record_id = Column(Integer, nullable=True, index=True)
    record_code = Column(String, nullable=True)
    party = Column(String, nullable=True)
    sender_id = Column(Integer, ForeignKey("email_senders.id"), nullable=True)
    from_address = Column(String, nullable=True)
    to_address = Column(Text, nullable=True)
    cc_address = Column(Text, nullable=True)
    subject = Column(Text, nullable=True)
    attachments = Column(Text, nullable=True)
    message_id = Column(String, nullable=True, index=True)
    status = Column(String, nullable=False, default="sent")  # sent | failed | resent
    error = Column(Text, nullable=True)
    raw_path = Column(String, nullable=True)  # failed ones: the message as it was, to send again
    sent_by = Column(String, nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow, index=True)


class EmailReply(Base):
    """A reply that came into a mailbox AT-HUB reads, matched to the record it's about (by the thread, or the
    record's number in the subject)."""
    __tablename__ = "email_replies"

    id = Column(Integer, primary_key=True, index=True)
    sender_id = Column(Integer, ForeignKey("email_senders.id"), nullable=True)
    email_log_id = Column(Integer, ForeignKey("email_log.id"), nullable=True)
    record_type = Column(String, nullable=True, index=True)
    record_id = Column(Integer, nullable=True, index=True)
    record_code = Column(String, nullable=True)
    from_address = Column(String, nullable=True)
    subject = Column(Text, nullable=True)
    snippet = Column(Text, nullable=True)
    message_id = Column(String, nullable=True, index=True)
    attachments = Column(Text, nullable=True)  # names; the files went to the AI Desk
    received_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CustomerReport(Base):
    """A customer's own "open lines" report (their open POs with us, from their system) checked against our orders
    (services/open_lines.py). The file and the result of each check are kept, so the next upload can say what changed."""
    __tablename__ = "customer_reports"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    filename = Column(String, nullable=False)
    stored_name = Column(String, nullable=False)  # under uploads/reports/
    mapping = Column(Text, nullable=True)  # JSON: our field -> their column header
    result = Column(Text, nullable=True)  # JSON: summary + every row with its verdict
    uploaded_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CustomerReportProfile(Base):
    """Per customer: which of their columns is which, and what to leave out (their kit headers, our $0 nut lines...)."""
    __tablename__ = "customer_report_profiles"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, unique=True)
    mapping = Column(Text, nullable=True)  # JSON
    rules = Column(Text, nullable=True)  # JSON
    updated_by = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DeskFile(Base):
    """A file dropped on the AI Desk: kept on the server until someone acts on it or removes it, so nothing dropped
    is ever lost (leave the page, come back later). result = what AT-HUB worked out (JSON: who it's from, what it
    is, the read, what it offers to do) -- read once, used for every action (routes/ai_desk.py, services/desk.py)."""
    __tablename__ = "desk_files"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, nullable=False)
    stored_name = Column(String, nullable=False)  # under uploads/desk/
    content_type = Column(String, nullable=True)
    size = Column(Integer, nullable=True)
    status = Column(String, nullable=False, default="new")  # new | reading | read | error | done
    kind = Column(String, nullable=True)  # customer_po | rfq | vendor_order | vendor_invoice | mtr | pod | other
    result = Column(Text, nullable=True)  # JSON
    error = Column(Text, nullable=True)
    record_type = Column(String, nullable=True)  # what it became / went on: customer_order | purchase_order | shipment | quote
    record_id = Column(Integer, nullable=True)
    record_code = Column(String, nullable=True)
    done_label = Column(String, nullable=True)  # "Created PO325380 (Validation Needed)"
    done_by = Column(String, nullable=True)
    done_at = Column(DateTime, nullable=True)
    uploaded_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class VendorShipment(Base):
    """The vendor shipped (goods left their warehouse): carrier, tracking, BOL / PRO #, ETA... -- the PO's "Shipped"
    stage. A PO can ship in several. In transit until its goods are received, then it completes itself
    (services/vendor_shipments.settle); no lines = the whole rest of the PO."""
    __tablename__ = "vendor_shipments"

    id = Column(Integer, primary_key=True, index=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False, index=True)
    status = Column(String, nullable=False, default="in_transit")  # in_transit | received
    shipped_date = Column(DateTime, nullable=True)  # a calendar date: left the vendor's dock (not "ship_date": that key is a moment in API responses)
    eta = Column(DateTime, nullable=True)  # a calendar date: expected at our dock
    carrier = Column(String, nullable=True)
    ship_mode = Column(String, nullable=True)  # Parcel | LTL | FTL | Ocean | Air | Vendor Truck | We Pick Up
    tracking_number = Column(String, nullable=True, index=True)
    pro_number = Column(String, nullable=True)  # LTL carrier's PRO #
    bol_number = Column(String, nullable=True)  # bill of lading #
    container_number = Column(String, nullable=True)  # ocean container / trailer #
    vendor_ref = Column(String, nullable=True)  # the vendor's packing slip / ASN / invoice #
    freight_terms = Column(String, nullable=True)  # Prepaid | Collect | Prepaid & Add | Third Party
    packages = Column(Integer, nullable=True)
    package_type = Column(String, nullable=True)  # Pallets | Boxes | Crates | Drums | Bundles
    weight = Column(Float, nullable=True)  # lb
    note = Column(Text, nullable=True)
    received_at = Column(DateTime, nullable=True)  # when it completed (all of its goods received)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    lines = relationship("VendorShipmentLine", cascade="all, delete-orphan", order_by="VendorShipmentLine.id")


class VendorShipmentLine(Base):
    """How much of a PO line is on a vendor shipment."""
    __tablename__ = "vendor_shipment_lines"

    id = Column(Integer, primary_key=True, index=True)
    shipment_id = Column(Integer, ForeignKey("vendor_shipments.id"), nullable=False, index=True)
    po_line_id = Column(Integer, ForeignKey("purchase_order_lines.id"), nullable=False, index=True)
    quantity = Column(Float, nullable=False, default=0)


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


class TypeOption(Base):
    """One entry of a list people can add to (app/services/type_lists.py): document types, S&H / charge types, landed
    cost types, payment methods. Records store the key; the label is what's shown and can be renamed."""
    __tablename__ = "type_options"

    id = Column(Integer, primary_key=True, index=True)
    list = Column(String, nullable=False, index=True)  # attachment | charge | landed_cost | payment_method
    key = Column(String, nullable=False)  # what records store: packing_list, vendor_packing_list...
    label = Column(String, nullable=False)  # "Vendor Packing List"
    scopes = Column(String, nullable=True)  # document types: customer_order,purchase_order,shipment
    money = Column(Boolean, nullable=False, default=False)  # document types with prices: managers only
    builtin = Column(Boolean, nullable=False, default=False)
    active = Column(Boolean, nullable=False, default=True)  # hidden ones stay on old records, just not offered
    position = Column(Integer, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


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

    Lifecycle: new (items booked from specific lots) -> ready (bookings confirmed, picking can
    start) -> picked + packing accepted -> shipped (Ship: stock leaves on_hand) -> delivered ->
    invoiced. new/ready shipments can be cancelled, releasing their bookings."""
    __tablename__ = "shipments"

    id = Column(Integer, primary_key=True, index=True)
    row_version = Column(Integer, nullable=False, default=1)  # bumped on every change to it or its lines (optimistic locking)
    row_updated_at = Column(DateTime, nullable=True)  # when / by whom it last changed
    updated_by = Column(String, nullable=True)
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
    packed_at = Column(DateTime, nullable=True)  # packing (boxes, pallets) reviewed and accepted -- needed before Ship
    packed_by = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    lines = relationship("ShipmentLine", backref="shipment", cascade="all, delete-orphan")
    boxes = relationship("ShipmentBox", backref="shipment", cascade="all, delete-orphan")
    pallets = relationship("PalletWeight", backref="shipment", cascade="all, delete-orphan")
    combos = relationship("ShipmentCombo", backref="shipment", cascade="all, delete-orphan", order_by="ShipmentCombo.id")

    def delivered_for_line(self, order_line_id):
        """When this order line arrived: its own date if it came on another day, else the shipment's."""
        own = next((l.delivered_at for l in self.lines if l.order_line_id == order_line_id and l.delivered_at), None)
        return own or self.delivered_at


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
    # This line's own delivery moment, only when it differs from the shipment's (None = arrived with the shipment).
    delivered_at = Column(DateTime, nullable=True)

    lot = relationship("Lot")

    @property
    def line_no(self):
        return self.order_line.line_no if self.order_line else None

    @property
    def effective_delivered_at(self):
        """When this line was delivered: its own date if one was set, else the shipment's."""
        return self.delivered_at or (self.shipment.delivered_at if self.shipment else None)


class ShipmentCombo(Base):
    """Bolts and nuts sent together as assembled units on ONE shipment: `quantity` of the lead (bolt) line, each with
    `ratio` of the member (nut) line, go out as `quantity` units. Stock, lots and each order line's shipped qty stay per
    item; only packing, labels and the packing list count them once (app/services/nut_combos.py). The next shipment of
    the same order starts uncombined."""
    __tablename__ = "shipment_combos"

    id = Column(Integer, primary_key=True, index=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id"), nullable=False, index=True)
    lead_line_id = Column(Integer, ForeignKey("customer_order_lines.id"), nullable=False)  # the bolt: boxes + labels are its
    member_line_id = Column(Integer, ForeignKey("customer_order_lines.id"), nullable=False)  # the nut riding on it
    quantity = Column(Float, nullable=False)  # assembled units = bolts combined
    ratio = Column(Float, nullable=False, default=1)  # nuts per bolt
    note = Column(String, nullable=True)  # printed on the packing list, e.g. "Bolts and nuts combined"
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    lead_line = relationship("CustomerOrderLine", foreign_keys=[lead_line_id])
    member_line = relationship("CustomerOrderLine", foreign_keys=[member_line_id])

    @property
    def member_quantity(self) -> float:
        return self.quantity * (self.ratio or 1)


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
    pack_size = Column(Integer, nullable=True)  # the size this line was packed at (a one-box line can't show it otherwise)
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
    row_version = Column(Integer, nullable=False, default=1)  # bumped on every change to it or its lines (optimistic locking)
    row_updated_at = Column(DateTime, nullable=True)  # when / by whom it last changed
    updated_by = Column(String, nullable=True)
    code = Column(String, unique=True, nullable=False, index=True)  # INV-0001
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    order_id = Column(Integer, ForeignKey("customer_orders.id"), nullable=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id"), nullable=True)
    invoice_date = Column(DateTime, default=clock.today)  # a calendar date: the company's today
    due_date = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="draft")  # draft | sent | paid | void
    void_reason = Column(Text, nullable=True)  # why it was voided (e.g. its shipment was undone)
    voided_at = Column(DateTime, nullable=True)
    voided_by = Column(String, nullable=True)
    free_text = Column(Text, nullable=True)
    # Factoring / funding report fields (the portal's custom_570/571/572), filled by the
    # bulk funding upload or by hand. funding_amount + funding_discount should equal the total.
    disbursement_date = Column(DateTime, nullable=True)
    funding_amount = Column(Float, nullable=True)
    funding_discount = Column(Float, nullable=True)
    funding_import_id = Column(Integer, ForeignKey("funding_imports.id"), nullable=True, index=True)
    print_zero_lines = Column(Boolean, default=False)  # $0 lines are left off the PDF unless this is ticked
    print_payments = Column(Boolean, nullable=True, default=True)  # PDF lists the customer's payments and the balance due (None = yes)
    # JSON record of how this invoice was combined, so it can be shown and undone:
    # {"merged": [{"code", "shipment_ids", "line_ids", "due_date", "free_text"}], "by", "at"}
    combined_info = Column(Text, nullable=True)
    # Lines split off another invoice of the same shipment(s) (Split Lines): that invoice. Both keep billing the shipment.
    split_from_id = Column(Integer, ForeignKey("invoices.id"), nullable=True, index=True)
    mrp_id = Column(Integer, nullable=True, index=True)  # id in MRPeasy, for records imported from it
    custom_fields = Column(Text, nullable=True)  # JSON: MRPeasy custom fields kept as imported ({"label": value})
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    shipments = relationship("Shipment", secondary="invoice_shipments", order_by="Shipment.id")
    lines = relationship("InvoiceLine", backref="invoice", cascade="all, delete-orphan")
    payments = relationship("InvoicePayment", backref="invoice", cascade="all, delete-orphan")
    emails = relationship("InvoiceEmail", backref="invoice", cascade="all, delete-orphan", order_by="InvoiceEmail.sent_at")
    split_from = relationship("Invoice", remote_side=[id], backref="split_parts")
    order = relationship("CustomerOrder", viewonly=True)

    @property
    def order_expedited(self) -> bool:
        """Its order ships expedited: the invoice should carry the extra charge."""
        return bool(self.order and self.order.expedited)

    @property
    def split_from_code(self):
        return self.split_from.code if self.split_from else None

    @property
    def split_into(self) -> list:
        """Invoices whose lines were split off this one (void ones left out)."""
        return [i.code for i in self.split_parts if i.status != "void"]

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

    @property
    def customer_payments(self) -> list:
        """Payments the customer made. Factoring payments (a funding upload, or method factoring / factoring
        discount) are left out: the factor paid those, and the customer still owes the invoice in full."""
        return [p for p in self.payments
                if not p.funding_import_id and (p.method or "").lower() not in ("factoring", "factoring discount")]

    def prints(self, key: str) -> bool:
        """Whether the customer's copy prints this (Print Options: due_date, payments, zero_lines, notes). The
        renderer sets the choices for the document being made (_print_opts); without them, the saved defaults."""
        opts = getattr(self, "_print_opts", None)
        if opts is None:
            from app.services import print_options
            opts = print_options.current("invoice")
        return bool(opts.get(key, True))

    @property
    def printed_payments(self) -> list:
        """The payments the customer's copy lists ("Previous Payments" in Print Options, on unless unticked)."""
        if not self.prints("payments") or self.status == "void":
            return []
        return sorted(self.customer_payments, key=lambda p: (p.paid_date or p.created_at or datetime.min, p.id))

    @property
    def amount_due_printed(self) -> float:
        """What the customer's copy (PDF and email) says is due: the total less the payments it lists."""
        from app.services.money import cents
        return cents(self.total - sum(p.amount for p in self.printed_payments))


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

    order_line = relationship("CustomerOrderLine", viewonly=True)

    @property
    def amount(self) -> float:
        from app.services.money import line_amount
        return line_amount(self.quantity, self.unit_price)

    @property
    def order_line_no(self):
        return self.order_line.line_no if self.order_line else None


def in_order_line_order(lines) -> list:
    """Invoice lines as the order lists them: by the order line's #; lines not from the order (Shipping...) last."""
    return sorted(lines, key=lambda l: (l.order_line_no is None, l.order_line_no or 0, l.shipment_id or 0, l.id))


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
    credit_memo_id = Column(Integer, nullable=True, index=True)  # a credit memo applied to the invoice (method "credit memo")
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
    generic_stock_enabled = Column(Boolean, nullable=False, default=True)  # off = no generic-stock offers or draws (quick rollback)
    # How the packing screens pre-fill pack sizes (app/services/pack_sizes.py RULES): smart | customer | last | default
    pack_size_rule = Column(String, nullable=True, default="smart")

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


class Role(Base):
    """A role = a named set of permissions (app/services/permissions.py). Users point at it by key.
    The four built-in roles start with what each could do before roles were editable; super_admin always has
    everything (it can't be locked out)."""
    __tablename__ = "roles"

    key = Column(String, primary_key=True)          # "manager", "driver", "accountant_2"
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    permissions = Column(Text, nullable=False, default="[]")  # JSON list of permission keys
    builtin = Column(Boolean, nullable=False, default=False)
    updated_by = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CreditMemo(Base):
    """Money given back to a customer: over-billing, returns, price corrections. Linked to the order (and usually the
    invoice it corrects). Lines tied to an order line count against what that line billed (app/services/billing.py).
    draft -> issued (the customer's) -> applied to open invoices as payments ("credit memo"), or void.
    remaining = total - applied: what the customer can still use."""
    __tablename__ = "credit_memos"

    id = Column(Integer, primary_key=True, index=True)
    row_version = Column(Integer, nullable=False, default=1)
    row_updated_at = Column(DateTime, nullable=True)
    updated_by = Column(String, nullable=True)
    code = Column(String, unique=True, nullable=False, index=True)  # CM-0001
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    order_id = Column(Integer, ForeignKey("customer_orders.id"), nullable=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True, index=True)  # the invoice it corrects
    memo_date = Column(DateTime, default=clock.today)  # a calendar date
    reason = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="draft")  # draft | issued | void
    void_reason = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    lines = relationship("CreditMemoLine", backref="memo", cascade="all, delete-orphan", order_by="CreditMemoLine.id")
    applications = relationship("InvoicePayment", backref="credit_memo", primaryjoin="CreditMemo.id == foreign(InvoicePayment.credit_memo_id)")

    @property
    def total(self) -> float:
        from app.services.money import total
        return total(self.lines)

    @property
    def applied(self) -> float:
        from app.services.money import cents
        return cents(sum(p.amount for p in self.applications))

    @property
    def remaining(self) -> float:
        from app.services.money import cents
        return 0.0 if self.status == "void" else cents(self.total - self.applied)

    @property
    def applied_to(self) -> list:
        return [{"payment_id": p.id, "invoice_id": p.invoice_id, "amount": p.amount, "paid_date": p.paid_date} for p in self.applications]


class ReminderLog(Base):
    """An overdue-payment reminder emailed to a customer (statement attached): who, which invoices, when, by whom."""
    __tablename__ = "reminder_logs"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    invoice_codes = Column(Text, nullable=True)  # the overdue invoices it was about, comma separated
    amount = Column(Float, nullable=True)        # overdue total at the time
    to_address = Column(String, nullable=True)
    sent_by = Column(String, nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow)


class CreditMemoLine(Base):
    __tablename__ = "credit_memo_lines"

    id = Column(Integer, primary_key=True, index=True)
    memo_id = Column(Integer, ForeignKey("credit_memos.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=True)
    order_line_id = Column(Integer, ForeignKey("customer_order_lines.id"), nullable=True)  # the order line it credits
    description = Column(String, nullable=False)
    quantity = Column(Float, nullable=False, default=1)
    unit_price = Column(Float, nullable=False, default=0)

    @property
    def amount(self) -> float:
        from app.services.money import line_amount
        return line_amount(self.quantity, self.unit_price)


class BillingVariance(Base):
    """An invoice line billed a different quantity than the shipment delivered, and someone accepted it.
    Kept against the customer order so it can be put right later (app/services/billing.py). Rows mirror the
    invoice as last saved; voiding the invoice removes them."""
    __tablename__ = "billing_variances"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("customer_orders.id"), nullable=False, index=True)
    order_line_id = Column(Integer, ForeignKey("customer_order_lines.id"), nullable=False)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    delivered_qty = Column(Float, nullable=False)
    billed_qty = Column(Float, nullable=False)
    reason = Column(Text, nullable=True)
    accepted_by = Column(String, nullable=True)
    accepted_at = Column(DateTime, default=datetime.utcnow)


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


class Quote(Base):
    """A quotation to a customer. Kept apart from orders so a quote never shows up in shipping,
    invoicing or reports; Convert to Order turns an accepted one into a customer order."""
    __tablename__ = "quotes"

    id = Column(Integer, primary_key=True, index=True)
    row_version = Column(Integer, nullable=False, default=1)  # bumped on every change to it or its lines (optimistic locking)
    row_updated_at = Column(DateTime, nullable=True)  # when / by whom it last changed
    updated_by = Column(String, nullable=True)
    code = Column(String, unique=True, index=True, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    status = Column(String, nullable=False, default="draft")  # draft | sent | accepted | declined | converted
    quote_date = Column(DateTime, default=datetime.utcnow)
    valid_until = Column(DateTime, nullable=True)
    customer_ref = Column(String, nullable=True)  # their RFQ # / email subject
    notes = Column(Text, nullable=True)  # printed on the quote
    order_id = Column(Integer, ForeignKey("customer_orders.id"), nullable=True)  # once converted
    status_before_convert = Column(String, nullable=True)  # restored if the order is cancelled / deleted
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    lines = relationship("QuoteLine", backref="quote", cascade="all, delete-orphan", order_by="QuoteLine.position")
    emails = relationship("QuoteEmail", cascade="all, delete-orphan", order_by="QuoteEmail.sent_at.desc()")


class QuoteEmail(Base):
    """Log of every time a quote was emailed to the customer."""
    __tablename__ = "quote_emails"

    id = Column(Integer, primary_key=True, index=True)
    quote_id = Column(Integer, ForeignKey("quotes.id"), nullable=False, index=True)
    to_address = Column(String, nullable=False)
    cc_address = Column(String, nullable=True)
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=True)
    sent_by = Column(String, nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow)


class QuoteLine(Base):
    __tablename__ = "quote_lines"

    id = Column(Integer, primary_key=True, index=True)
    quote_id = Column(Integer, ForeignKey("quotes.id"), nullable=False, index=True)
    position = Column(Integer, nullable=False, default=0)
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=True)  # may be blank: quoted before the item exists
    description = Column(String, nullable=True)  # printed; defaults to the item's title
    quantity = Column(Float, nullable=False, default=1)
    unit_price = Column(Float, nullable=False, default=0)
    notes = Column(Text, nullable=True)
    source_text = Column(String, nullable=True)  # the pasted line it came from (taught to learned matches)


class DocTemplate(Base):
    """A designed layout for a printed document or label (Template Designer). One per doc_type can be the
    default; a customer can have its own default for customer documents (invoice, packing list, quote,
    box label). No default = the built-in layout."""
    __tablename__ = "doc_templates"

    id = Column(Integer, primary_key=True, index=True)
    doc_type = Column(String, nullable=False, index=True)  # invoice | packing_list | purchase_order | quote | box_label | address_label
    name = Column(String, nullable=False)
    spec = Column(Text, nullable=False)  # JSON: page, bands (header / running / summary / footer) with blocks, table columns
    is_default = Column(Boolean, nullable=False, default=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)  # default for this customer only
    starter = Column(String, nullable=True)  # which ready-made design it began from
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_by = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AppSetting(Base):
    """Small app-wide settings by key (JSON value): go-live cut-off, which permissions roles have been given..."""
    __tablename__ = "app_settings"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)
    updated_by = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AttentionDismissal(Base):
    """A Needs Attention row someone dismissed (MRP Migrate > Go-Live Cleanup): hidden until restored."""
    __tablename__ = "attention_dismissals"
    __table_args__ = (UniqueConstraint("section_key", "record_id", name="uq_attention_dismissal"),)

    id = Column(Integer, primary_key=True, index=True)
    section_key = Column(String, nullable=False, index=True)
    record_id = Column(Integer, nullable=False)
    label = Column(String, nullable=True)  # what it was, for the restore list
    dismissed_by = Column(String, nullable=True)
    dismissed_at = Column(DateTime, default=datetime.utcnow)


class TodoList(Base):
    """A To-Do list (iOS Reminders style). Personal unless shared, then everyone sees and ticks it."""
    __tablename__ = "todo_lists"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    color = Column(String, nullable=True, default="#2f6fed")
    owner = Column(String, nullable=False, index=True)  # username
    shared = Column(Boolean, nullable=False, default=False)
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class TodoItem(Base):
    """One to-do. Due on a day (due_date, a calendar date) and optionally at a time (due_at, a moment in UTC)."""
    __tablename__ = "todo_items"

    id = Column(Integer, primary_key=True, index=True)
    list_id = Column(Integer, ForeignKey("todo_lists.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, nullable=False)
    notes = Column(Text, nullable=True)
    due_date = Column(DateTime, nullable=True)
    due_at = Column(DateTime, nullable=True)
    priority = Column(Integer, nullable=False, default=0)  # 0 none, 1 low, 2 medium, 3 high
    flagged = Column(Boolean, nullable=False, default=False)
    done = Column(Boolean, nullable=False, default=False)
    done_at = Column(DateTime, nullable=True)
    done_by = Column(String, nullable=True)
    position = Column(Integer, nullable=False, default=0)
    entity_type = Column(String, nullable=True)  # optional link: customer_order | purchase_order | shipment
    entity_id = Column(Integer, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StickyNote(Base):
    """A sticky note on a customer order, PO or shipment: the team's reminder, seen by everyone who opens the record.
    A reminder (remind_date, a calendar day; remind_at, optional time in UTC) puts it on the calendar, in To-Do's
    Today / Scheduled and on the reminder badge."""
    __tablename__ = "sticky_notes"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String, nullable=False, index=True)
    entity_id = Column(Integer, nullable=False, index=True)
    text = Column(Text, nullable=False)
    color = Column(String, nullable=False, default="yellow")  # yellow | pink | green | blue
    remind_date = Column(DateTime, nullable=True)
    remind_at = Column(DateTime, nullable=True)
    done = Column(Boolean, nullable=False, default=False)
    done_by = Column(String, nullable=True)
    done_at = Column(DateTime, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_by = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Simulation(Base):
    """A profit / loss simulation (Simulate page): customer demand on one side, sources on the other, extra costs, the
    item-by-item comparison. The working document is one JSON doc (app/routes/simulations.py)."""
    __tablename__ = "simulations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    doc = Column(Text, nullable=False, default="{}")
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_by = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---- Accounting (app/services/accounting.py): the company's own books, kept beside the operations data. Bank / card
# statements come in, every line goes into an account (learned from the descriptions), reports show where the money went.
# Shaped like QuickBooks (account types, external_id on everything) so the books can be synced to it later.

class AcctAccount(Base):
    """An account a bank line can go in: a customer that pays us (income), a vendor (COGS), an expense, a lender
    (loan), a partner's draws (owner), a transfer between our own accounts (wash), or a year-end AP / AR figure."""
    __tablename__ = "acct_accounts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    kind = Column(String, nullable=False, default="expense")  # income | cogs | expense | loan | owner | transfer | pending | other
    partner = Column(String, nullable=True)  # owner accounts: whose draws
    qb_type = Column(String, nullable=True)  # QuickBooks account type when synced (Income, Cost of Goods Sold, Expense ...)
    external_id = Column(String, nullable=True)  # id in QuickBooks (later)
    note = Column(Text, nullable=True)
    opening_balance = Column(Float, nullable=False, default=0.0)  # loans: what was owed before the first line here
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AcctSource(Base):
    """Where lines come from: a bank account, a credit card, or "Book Entries" (typed in, not on a statement)."""
    __tablename__ = "acct_sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    kind = Column(String, nullable=False, default="bank")  # bank | card | book
    last4 = Column(String, nullable=True)
    layout = Column(Text, nullable=True)  # JSON: which statement column is which (learned on the first import)
    opening_balance = Column(Float, nullable=False, default=0.0)
    opening_date = Column(DateTime, nullable=True)  # calendar date the opening balance is for
    external_id = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AcctImport(Base):
    """One statement file brought in -- kept so it can be undone in one go."""
    __tablename__ = "acct_imports"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("acct_sources.id"), nullable=True)
    filename = Column(String, nullable=True)
    kind = Column(String, nullable=False, default="statement")  # statement | workbook
    rows = Column(Integer, nullable=False, default=0)
    added = Column(Integer, nullable=False, default=0)
    skipped = Column(Integer, nullable=False, default=0)  # already in (same date, amount, description)
    matched = Column(Integer, nullable=False, default=0)  # typed in earlier, now found on the statement
    auto = Column(Integer, nullable=False, default=0)  # put in an account by AT-HUB
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AcctTxn(Base):
    """One line: money in (+) or out (-) on a calendar date, in an account (or split over several, AcctSplit)."""
    __tablename__ = "acct_txns"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("acct_sources.id"), nullable=False, index=True)
    date = Column(DateTime, nullable=False, index=True)  # calendar date
    amount = Column(Float, nullable=False)
    description = Column(Text, nullable=False, default="")
    payee = Column(String, nullable=True)  # who, read from the description (acct_mapper.payee_label) or typed
    account_id = Column(Integer, ForeignKey("acct_accounts.id"), nullable=True, index=True)  # None = not in an account yet (or split)
    how = Column(String, nullable=True)  # person | auto | rule | workbook -- how it got its account
    reviewed = Column(Boolean, nullable=False, default=True)  # False: AT-HUB picked it, nobody has looked yet
    note = Column(Text, nullable=True)
    origin = Column(String, nullable=False, default="import")  # import | manual | workbook
    expected = Column(Boolean, nullable=False, default=False)  # typed in ahead of the statement; matched when it shows up
    import_id = Column(Integer, ForeignKey("acct_imports.id"), nullable=True, index=True)
    dedupe = Column(String, nullable=True, index=True)  # date|amount|description|n -- the same line isn't brought in twice
    bank_description = Column(Text, nullable=True)  # a typed-in line's statement wording once matched
    external_id = Column(String, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_by = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    splits = relationship("AcctSplit", cascade="all, delete-orphan", order_by="AcctSplit.id")


class AcctSplit(Base):
    """Part of a line in an account (a Zelle that was half salary, half supplies)."""
    __tablename__ = "acct_splits"

    id = Column(Integer, primary_key=True, index=True)
    txn_id = Column(Integer, ForeignKey("acct_txns.id"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("acct_accounts.id"), nullable=False)
    amount = Column(Float, nullable=False)
    note = Column(String, nullable=True)


class AcctRule(Base):
    """A person's rule: a description containing these words (in / out, amount range) goes in this account. Rules
    come before what AT-HUB learned from earlier lines."""
    __tablename__ = "acct_rules"

    id = Column(Integer, primary_key=True, index=True)
    contains = Column(String, nullable=False)
    sign = Column(String, nullable=True)  # in | out | None
    min_amount = Column(Float, nullable=True)
    max_amount = Column(Float, nullable=True)
    account_id = Column(Integer, ForeignKey("acct_accounts.id"), nullable=False)
    hits = Column(Integer, nullable=False, default=0)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AcctOpenItem(Base):
    """Accounts payable / receivable. Two kinds of row: one typed in (source "manual"), or a person's change to a
    figure AT-HUB works out live from its invoices / purchase orders (source "invoice" / "po" + source_id) -- e.g.
    "we already paid this" before the payment is recorded in AT-HUB. AT-HUB's own figure is never changed here."""
    __tablename__ = "acct_open_items"

    id = Column(Integer, primary_key=True, index=True)
    side = Column(String, nullable=False)  # ap | ar
    source = Column(String, nullable=False, default="manual")  # manual | invoice | po
    source_id = Column(Integer, nullable=True, index=True)
    party = Column(String, nullable=True)
    ref = Column(String, nullable=True)
    amount = Column(Float, nullable=True)  # manual: what's owed; AT-HUB rows: a changed amount (None = AT-HUB's)
    item_date = Column(DateTime, nullable=True)  # calendar date
    due_date = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="open")  # open | paid | left_out
    paid_date = Column(DateTime, nullable=True)
    note = Column(Text, nullable=True)
    external_id = Column(String, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_by = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
