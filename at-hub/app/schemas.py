from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional, List

PRICE_DECIMALS = 5


def _round_price(v):
    """Prices/costs are kept to 5 decimal places (e.g. 0.01275 per washer)."""
    return round(v, PRICE_DECIMALS) if v is not None else v


class InputModel(BaseModel):
    """Shared input rules: prices/costs are rounded to PRICE_DECIMALS, and quantities
    must be whole numbers (stock is counted in whole units everywhere)."""
    @field_validator("unit_price", "unit_cost", "cost_price", "selling_price", "adjustment_unit_cost",
                     mode="after", check_fields=False)
    @classmethod
    def _round(cls, v):
        return _round_price(v)

    @field_validator("quantity", "quantity_in_box", "on_hand", "reorder_point", mode="after", check_fields=False)
    @classmethod
    def _whole_qty(cls, v):
        if v is None:
            return v
        if abs(v - round(v)) > 1e-9:
            raise ValueError("Quantity must be a whole number")
        return float(round(v))


# ---- Auth ----
class LoginRequest(BaseModel):
    username: str
    password: str


ROLES = ("super_admin", "admin", "manager", "employee")


class UserResponse(BaseModel):
    id: int
    username: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: str
    is_active: bool
    must_change_password: bool = False
    permissions: List[str] = []  # what the role allows (app/services/permissions.py)
    role_name: Optional[str] = None
    last_login: Optional[datetime] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    username: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: str = "employee"
    password: str  # temporary -- the user must change it at first login


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class PasswordReset(BaseModel):
    password: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class ProductGroupCreate(BaseModel):
    name: str


class ProductGroupResponse(BaseModel):
    id: int
    name: str
    item_count: int = 0

    class Config:
        from_attributes = True


class PriceHistoryEntry(BaseModel):
    kind: str  # sale | purchase
    date: Optional[datetime] = None
    doc_id: int
    doc_code: str
    party: str
    status: str
    quantity: float
    unit_price: float


class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse


# ---- Stock Items ----
class StockItemCreate(InputModel):
    code: str
    keep_code: bool = False  # the user undid the autocorrect (-NUTS -> -NUT): save the code as typed
    title: str
    unit: Optional[str] = None
    category: Optional[str] = None
    barcode: Optional[str] = None
    cost_price: Optional[float] = 0
    selling_price: Optional[float] = 0
    reorder_point: Optional[float] = 0
    default_pack_size: Optional[int] = None
    created_via: Optional[str] = None  # "ai-scan" when made from a scanned PO
    is_generic: bool = False  # bulk stock other items draw from (e.g. 5/8-11 2H nuts bought by the 100,000)


class StockItemUpdate(InputModel):
    title: Optional[str] = None
    unit: Optional[str] = None
    category: Optional[str] = None
    barcode: Optional[str] = None
    cost_price: Optional[float] = None
    selling_price: Optional[float] = None
    reorder_point: Optional[float] = None
    is_active: Optional[bool] = None
    default_pack_size: Optional[int] = None
    on_hand: Optional[float] = None  # manual correction; logged as an 'adjustment' transaction, not silently overwritten
    adjustment_unit_cost: Optional[float] = None  # required when on_hand goes up: what the added stock was acquired at
    adjustment_lot_code: Optional[str] = None  # optional; a LOT-##### number is generated otherwise
    adjustment_note: Optional[str] = None
    parent_item_id: Optional[int] = None  # generic item to draw stock from; 0 clears it
    is_generic: Optional[bool] = None


class StockItemResponse(BaseModel):
    id: int
    code: str
    title: str
    unit: Optional[str] = None
    category: Optional[str] = None
    barcode: Optional[str] = None
    cost_price: Optional[float] = None
    selling_price: Optional[float] = None
    on_hand: float
    booked: float
    available: float
    reorder_point: Optional[float] = None
    default_pack_size: Optional[int] = None
    is_active: bool
    created_via: Optional[str] = None
    verified_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    parent_item_id: Optional[int] = None
    is_generic: bool = False

    class Config:
        from_attributes = True


# ---- Lots ----
class LotResponse(BaseModel):
    jobs: List[str] = []  # job #s of the orders this lot shipped on (search)
    id: int
    item_id: int
    lot_code: str
    quantity: float
    initial_quantity: Optional[float] = None
    base_unit_cost: Optional[float] = None
    landed_cost_per_unit: float = 0
    unit_cost: Optional[float] = None
    po_line_id: Optional[int] = None
    received_date: datetime
    expiry_date: Optional[datetime] = None
    status: str
    source: Optional[str] = None
    source_reference: Optional[str] = None

    parent_lot_id: Optional[int] = None
    class Config:
        from_attributes = True


class InventoryTransactionResponse(BaseModel):
    id: int
    item_id: int
    lot_id: Optional[int] = None
    quantity_delta: float
    type: str
    reference: Optional[str] = None
    note: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class LotStatusUpdate(BaseModel):
    status: str  # available | on_hold | rejected


class LotCostUpdate(BaseModel):
    unit_cost: float  # acquisition cost per unit; landed costs on the lot's PO line are added on top

    @field_validator("unit_cost")
    @classmethod
    def _check(cls, v):
        if v < 0:
            raise ValueError("Unit cost cannot be negative")
        return _round_price(v)


# ---- Customers / Vendors ----
class PartyCreate(BaseModel):
    name: str
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    shipping_address: Optional[str] = None


class PartyUpdate(BaseModel):
    name: Optional[str] = None
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    shipping_address: Optional[str] = None
    is_active: Optional[bool] = None


class CustomerCreate(PartyCreate):
    details: Optional[dict] = None  # the contact card -- see ContactCardMixin


class CustomerUpdate(PartyUpdate):
    details: Optional[dict] = None


VendorCreate, VendorUpdate = CustomerCreate, CustomerUpdate  # same contact card


class PartyResponse(BaseModel):
    id: int
    row_version: int = 1  # optimistic locking: send it back as X-Row-Version when changing the record
    row_updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None
    code: Optional[str] = None  # vendors: V-0001
    name: str
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    shipping_address: Optional[str] = None
    is_active: bool
    details: Optional[dict] = None  # the contact card
    invoice_email: Optional[str] = None  # customers: where invoices go (an "invoices / AP" email, else the main one)
    mtr_email: Optional[str] = None
    po_email: Optional[str] = None  # vendors: where our POs go (a "purchasing" / "orders" email, else the main one)

    class Config:
        from_attributes = True


# ---- Customer Orders ----
class CustomerOrderLineCreate(InputModel):
    item_id: int
    quantity: float
    unit_price: float = 0
    delivery_date: Optional[datetime] = None
    source_code: Optional[str] = None  # the customer's item # / description as scanned -- learned, not stored on the line
    source_description: Optional[str] = None
    notes: Optional[str] = None
    print_notes: Optional[bool] = True


class CustomerOrderCreate(BaseModel):
    allow_duplicate: bool = False  # create even though this customer PO # is already on another order
    customer_id: int
    delivery_date: Optional[datetime] = None
    po_number: Optional[str] = None
    customer_po_date: Optional[datetime] = None
    job_number: Optional[str] = None
    ship_to_address: Optional[str] = None
    notes: Optional[str] = None
    lines: List[CustomerOrderLineCreate]


class LineShipmentAllocation(BaseModel):
    shipment_id: int
    code: str
    status: str
    quantity: float
    picked_quantity: float
    boxes: int


class LineBookingSource(BaseModel):
    lot_id: Optional[int] = None
    lot_code: Optional[str] = None
    source: str
    reference: Optional[str] = None
    from_item_id: Optional[int] = None
    from_item_code: Optional[str] = None
    from_lot_code: Optional[str] = None
    quantity: float


class CustomerOrderLineResponse(BaseModel):
    id: int
    line_no: Optional[int] = None
    item_id: int
    quantity: float
    unit_price: float
    delivery_date: Optional[datetime] = None
    shipped_quantity: float
    booked_quantity: float = 0
    line_status: str = "not_booked"  # not_booked | partially_booked | booked | partially_shipped | shipped
    shipments: List[LineShipmentAllocation] = []
    booking_sources: List[LineBookingSource] = []
    notes: Optional[str] = None
    print_notes: Optional[bool] = True

    class Config:
        from_attributes = True


class CustomerOrderResponse(BaseModel):
    id: int
    row_version: int = 1  # optimistic locking: send it back as X-Row-Version when changing the record
    row_updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None
    code: str
    customer_id: int
    order_date: datetime
    created_at: Optional[datetime] = None  # when the order was entered (MRPeasy's "created" for imported ones)
    created_by: Optional[str] = None
    delivery_date: Optional[datetime] = None
    status: str
    po_number: Optional[str] = None
    customer_po_date: Optional[datetime] = None
    job_number: Optional[str] = None
    ship_to_address: Optional[str] = None
    notes: Optional[str] = None
    duplicate_po_ok: Optional[str] = None  # set once a manager has OK'd sharing the customer PO # with an earlier order
    lines: List[CustomerOrderLineResponse] = []

    class Config:
        from_attributes = True


class CustomerOrderUpdate(BaseModel):
    """Header fields -- always editable regardless of shipping progress (except once cancelled)."""
    customer_id: Optional[int] = None
    delivery_date: Optional[datetime] = None
    po_number: Optional[str] = None
    customer_po_date: Optional[datetime] = None
    job_number: Optional[str] = None
    ship_to_address: Optional[str] = None
    notes: Optional[str] = None


class CustomerOrderLineAdd(InputModel):
    item_id: int
    quantity: float
    unit_price: float = 0
    delivery_date: Optional[datetime] = None
    notes: Optional[str] = None
    print_notes: Optional[bool] = True


class CustomerOrderLineUpdate(InputModel):
    item_id: Optional[int] = None  # replace the item (only while nothing on the line is shipped or booked)
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    delivery_date: Optional[datetime] = None
    notes: Optional[str] = None
    print_notes: Optional[bool] = None


class LineOrderRequest(BaseModel):
    line_ids: List[int]  # every line of the order, in the new display order


class BookLineRequest(BaseModel):
    line_id: int
    quantity: float
    draw_from_item_id: Optional[int] = None  # generic item to transfer any shortfall from, in the same step

    @field_validator("quantity")
    @classmethod
    def _whole(cls, v):
        if abs(v - round(v)) > 1e-9:
            raise ValueError("Booked quantity must be a whole number")
        return float(round(v))


class CreateShipmentRequest(BaseModel):
    """Books the given order-line quantities into a new shipment."""
    lines: List[BookLineRequest]
    carrier: Optional[str] = None
    tracking_number: Optional[str] = None
    shipping_cost: Optional[float] = None  # what we pay the carrier -- distinct from the customer-facing shipping charge on an invoice
    notes: Optional[str] = None


class UnbookRequest(BaseModel):
    """Release booked (not yet picked) quantity from an open shipment back to stock.
    Give shipment_line_id to unbook from one specific lot, or order_line_id to unbook
    across that order line's lots in this shipment (newest lot first)."""
    quantity: float
    shipment_line_id: Optional[int] = None
    order_line_id: Optional[int] = None

    @field_validator("quantity")
    @classmethod
    def _whole(cls, v):
        if v <= 0 or abs(v - round(v)) > 1e-9:
            raise ValueError("Unbook quantity must be a whole number greater than 0")
        return float(round(v))


class ShipmentUpdate(BaseModel):
    carrier: Optional[str] = None
    tracking_number: Optional[str] = None
    shipping_cost: Optional[float] = None
    notes: Optional[str] = None


class PickLineRequest(InputModel):
    shipment_line_id: int
    quantity: float  # picked now, added to what's already picked


class PickRequest(BaseModel):
    lines: List[PickLineRequest] = []
    pick_all: bool = False  # pick everything still outstanding


# ---- Shipments / Packing / Labels ----
class ShipmentLineResponse(BaseModel):
    id: int
    order_line_id: int
    line_no: Optional[int] = None
    item_id: int
    lot_id: Optional[int] = None
    quantity: float
    picked_quantity: float = 0

    class Config:
        from_attributes = True


class ShipmentBoxInput(InputModel):
    item_id: int
    order_line_id: Optional[int] = None  # which order line the box belongs to; required when an item is on several lines
    box_number: int
    quantity_in_box: float
    lot_code: Optional[str] = None
    pallet_number: Optional[str] = None


class ShipmentBoxResponse(ShipmentBoxInput):
    id: int

    class Config:
        from_attributes = True


class SetBoxesRequest(BaseModel):
    boxes: List[ShipmentBoxInput]


class PalletWeightInput(BaseModel):
    pallet_number: str
    weight: Optional[float] = None
    dimensions: Optional[str] = None


class PalletWeightResponse(PalletWeightInput):
    id: int
    shipment_id: int

    class Config:
        from_attributes = True


class SetPalletWeightsRequest(BaseModel):
    pallets: List[PalletWeightInput]


class BulkPackSizeEntry(BaseModel):
    code: str
    pack_size: int


class BulkPackSizeRequest(BaseModel):
    entries: List[BulkPackSizeEntry]
    source: Optional[str] = None  # "batch packing" when pasted on the batch screen
    reference: Optional[str] = None  # e.g. the shipment codes it was pasted for


class BulkPackSizeResult(BaseModel):
    applied: List[str]
    not_found: List[str]
    unchanged: List[str] = []


class PackSizeHistoryEntry(BaseModel):
    id: int
    item_id: int
    item_code: str
    pack_size: Optional[int] = None
    previous_pack_size: Optional[int] = None
    source: Optional[str] = None
    reference: Optional[str] = None
    changed_by: Optional[str] = None
    changed_at: Optional[datetime] = None


class ShipmentResponse(BaseModel):
    id: int
    row_version: int = 1  # optimistic locking: send it back as X-Row-Version when changing the record
    row_updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None
    code: str
    order_id: int
    ship_date: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    delivered_by: Optional[str] = None
    packed_at: Optional[datetime] = None
    packed_by: Optional[str] = None
    created_at: Optional[datetime] = None
    carrier: Optional[str] = None
    tracking_number: Optional[str] = None
    shipping_cost: Optional[float] = None
    status: str
    notes: Optional[str] = None
    lines: List[ShipmentLineResponse] = []
    boxes: List[ShipmentBoxResponse] = []
    pallets: List[PalletWeightResponse] = []
    pods: List["PodFile"] = []  # proof-of-delivery attachments (filled in by the route)
    invoice_id: Optional[int] = None  # the live invoice billing this shipment (filled in by the route)
    invoice_code: Optional[str] = None
    invoice_status: Optional[str] = None
    invoice_combined: bool = False
    invoice_shipment_codes: List[str] = []  # every shipment on that invoice (more than one when combined)
    invoice_combined_from: List[str] = []

    class Config:
        from_attributes = True


class PodFile(BaseModel):
    id: int
    filename: str
    content_type: Optional[str] = None
    created_at: Optional[datetime] = None


class PodEmailRequest(BaseModel):
    to: str
    cc: Optional[str] = None
    subject: str
    body: str
    attachment_ids: Optional[List[int]] = None  # default: every POD on the shipment


class ShipmentEmailResponse(BaseModel):
    id: int
    shipment_id: int
    to_address: str
    cc_address: Optional[str] = None
    subject: str
    files: Optional[str] = None
    sent_by: Optional[str] = None
    sent_at: datetime

    class Config:
        from_attributes = True


# ---- Invoices ----
class CreateInvoiceRequest(BaseModel):
    due_date: Optional[datetime] = None
    free_text: Optional[str] = None
    shipping_charge: Optional[float] = 0


class InvoiceLineInput(InputModel):
    item_id: Optional[int] = None
    order_line_id: Optional[int] = None
    shipment_id: Optional[int] = None  # kept through edits so combined invoices still trace each line
    description: str
    quantity: float
    unit_price: float
    notes: Optional[str] = None
    print_notes: Optional[bool] = True


class InvoiceLineResponse(InvoiceLineInput):
    id: int


    amount: float = 0  # quantity x price, rounded to the cent

    class Config:
        from_attributes = True


class InvoiceUpdateRequest(BaseModel):
    due_date: Optional[datetime] = None
    free_text: Optional[str] = None
    lines: Optional[List[InvoiceLineInput]] = None
    accept_qty_differences: bool = False  # billing more / less than the shipments delivered was seen and accepted
    qty_note: Optional[str] = None        # why (kept with the order's billing record)


class CreateCombinedInvoiceRequest(BaseModel):
    shipment_ids: List[int]
    due_date: Optional[datetime] = None
    free_text: Optional[str] = None
    shipping_charge: Optional[float] = 0


class MergeInvoicesRequest(BaseModel):
    invoice_ids: List[int]  # draft invoices to fold into this one


class InvoicePrintOptions(BaseModel):
    print_zero_lines: bool


class InvoiceStatusUpdate(BaseModel):
    status: str  # sent | paid | void
    reason: Optional[str] = None  # why it's voided (kept on the invoice)


class InvoicePaymentInput(BaseModel):
    amount: float
    paid_date: Optional[datetime] = None
    method: Optional[str] = None
    reference: Optional[str] = None
    note: Optional[str] = None


class InvoicePaymentResponse(InvoicePaymentInput):
    id: int
    invoice_id: int
    funding_import_id: Optional[int] = None
    created_by: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class InvoiceResponse(BaseModel):
    id: int
    row_version: int = 1  # optimistic locking: send it back as X-Row-Version when changing the record
    row_updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None
    code: str
    customer_id: int
    order_id: Optional[int] = None
    shipment_id: Optional[int] = None
    invoice_date: datetime
    due_date: Optional[datetime] = None
    status: str
    void_reason: Optional[str] = None
    voided_at: Optional[datetime] = None
    voided_by: Optional[str] = None
    free_text: Optional[str] = None
    disbursement_date: Optional[datetime] = None
    funding_amount: Optional[float] = None
    funding_discount: Optional[float] = None
    funding_import_id: Optional[int] = None
    shipment_ids: List[int] = []
    shipment_codes: List[str] = []
    is_combined: bool = False
    combined_from: List[str] = []  # codes of the invoices folded into this one
    print_zero_lines: bool = False
    lines: List[InvoiceLineResponse] = []
    payments: List[InvoicePaymentResponse] = []
    emails: List["InvoiceEmailResponse"] = []
    total: float = 0
    amount_paid: float = 0
    balance: float = 0

    class Config:
        from_attributes = True


class InvoiceEmailRequest(BaseModel):
    to: str  # one or more addresses, comma/semicolon separated
    cc: Optional[str] = None
    subject: str
    body: str
    attach_pdf: bool = True


class InvoiceEmailResponse(BaseModel):
    id: int
    to_address: str
    cc_address: Optional[str] = None
    subject: str
    sent_by: Optional[str] = None
    sent_at: datetime

    class Config:
        from_attributes = True


class EmailConfigResponse(BaseModel):
    configured: bool
    from_address: Optional[str] = None
    host: Optional[str] = None


# ---- Purchase Orders ----
class PurchaseOrderLineCreate(InputModel):
    item_id: Optional[int] = None  # may be left out when vendor_item_code matches a known cross-reference
    quantity: float
    unit_cost: float = 0
    vendor_item_code: Optional[str] = None
    vendor_description: Optional[str] = None
    notes: Optional[str] = None
    print_notes: Optional[bool] = True


class PurchaseOrderCreate(BaseModel):
    vendor_id: int
    expected_date: Optional[datetime] = None
    vendor_so_number: Optional[str] = None  # the vendor's sales order / confirmation #
    notes: Optional[str] = None
    lines: List[PurchaseOrderLineCreate]


class PurchaseOrderLineResponse(BaseModel):
    id: int
    item_id: int
    quantity: float
    unit_cost: float
    received_quantity: float
    landed_cost_per_unit: float = 0
    vendor_item_code: Optional[str] = None
    vendor_description: Optional[str] = None
    notes: Optional[str] = None
    print_notes: Optional[bool] = True
    planned_lot_code: Optional[str] = None

    class Config:
        from_attributes = True


class PurchaseOrderPaymentInput(BaseModel):
    amount: float
    currency: Optional[str] = None
    paid_date: Optional[datetime] = None
    method: Optional[str] = None
    reference: Optional[str] = None
    note: Optional[str] = None
    vendor_bill_id: Optional[int] = None


class VendorBillInput(BaseModel):
    bill_number: str
    bill_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    amount: float  # the invoice total, including any shipping on it
    note: Optional[str] = None
    attachment_id: Optional[int] = None
    shipping_amount: Optional[float] = None  # freight/shipping on this invoice -> added to the PO as a charge
    shipping_type: Optional[str] = None


class PurchaseOrderChargeInput(BaseModel):
    charge_type: str = "shipping"
    amount: float
    description: Optional[str] = None
    vendor_bill_id: Optional[int] = None


class PurchaseOrderChargeResponse(PurchaseOrderChargeInput):
    id: int
    po_id: int
    bill_number: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class VendorPaymentInput(BaseModel):
    vendor_id: int
    amount: float
    paid_date: Optional[datetime] = None
    method: Optional[str] = None
    reference: Optional[str] = None
    note: Optional[str] = None


class VendorPaymentApplication(BaseModel):
    id: int
    po_id: int
    amount: float
    vendor_bill_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class VendorPaymentResponse(VendorPaymentInput):
    id: int
    code: str
    applied: float = 0
    unapplied: float = 0
    applications: List[VendorPaymentApplication] = []
    created_by: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ApplyVendorPaymentRequest(BaseModel):
    po_id: int
    amount: float
    vendor_bill_id: Optional[int] = None


class VendorBillResponse(VendorBillInput):
    id: int
    po_id: int
    amount_paid: float = 0
    balance: float = 0
    created_by: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class MarkDeliveredRequest(BaseModel):
    delivered_at: Optional[datetime] = None  # default: now


class UnshipRequest(BaseModel):
    """Undoing a shipment voids its invoice. A sent invoice needs the steps done first."""
    reason: Optional[str] = None  # why the invoice is cancelled (required when it was sent)
    customer_notified: bool = False  # the customer has been / will be told the invoice is cancelled
    combined_ok: bool = False  # the invoice also bills other shipments: they become un-invoiced too


class PurchaseOrderPaymentResponse(PurchaseOrderPaymentInput):
    id: int
    po_id: int
    vendor_payment_id: Optional[int] = None
    created_by: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class PurchaseOrderResponse(BaseModel):
    jobs: List[str] = []  # job #s its received material shipped to (search)
    id: int
    row_version: int = 1  # optimistic locking: send it back as X-Row-Version when changing the record
    row_updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None
    code: str
    vendor_id: int
    order_date: datetime
    created_at: Optional[datetime] = None  # when the PO was entered (MRPeasy's "created" for imported ones)
    created_by: Optional[str] = None
    vendor_so_number: Optional[str] = None
    expected_date: Optional[datetime] = None
    status: str
    freight_cost: Optional[float] = None
    tariff_cost: Optional[float] = None
    landed_cost_total: float = 0
    lines_total: float = 0
    charges_total: float = 0
    order_total: float = 0
    amount_paid: float = 0
    notes: Optional[str] = None
    lines: List[PurchaseOrderLineResponse] = []
    charges: List[PurchaseOrderChargeResponse] = []
    payments: List[PurchaseOrderPaymentResponse] = []
    emails: List["PurchaseOrderEmailLog"] = []
    bills: List[VendorBillResponse] = []

    class Config:
        from_attributes = True


class PurchaseOrderUpdate(BaseModel):
    vendor_id: Optional[int] = None
    expected_date: Optional[datetime] = None
    vendor_so_number: Optional[str] = None
    notes: Optional[str] = None


class PurchaseOrderLineAdd(InputModel):
    item_id: Optional[int] = None
    quantity: float
    unit_cost: float = 0
    vendor_item_code: Optional[str] = None
    vendor_description: Optional[str] = None
    notes: Optional[str] = None
    print_notes: Optional[bool] = True


class PurchaseOrderLineUpdate(InputModel):
    item_id: Optional[int] = None  # replace the item (only while nothing on the line is received)
    quantity: Optional[float] = None
    unit_cost: Optional[float] = None
    vendor_item_code: Optional[str] = None
    vendor_description: Optional[str] = None
    notes: Optional[str] = None
    print_notes: Optional[bool] = None


class PurchaseOrderEmailLog(BaseModel):
    to_address: str
    cc_address: Optional[str] = None
    subject: str
    sent_by: Optional[str] = None
    sent_at: datetime

    class Config:
        from_attributes = True


class VendorItemInput(BaseModel):
    item_id: int
    vendor_item_code: str
    vendor_description: Optional[str] = None


class VendorItemResponse(BaseModel):
    id: int
    vendor_id: int
    item_id: int
    vendor_item_code: str
    vendor_description: Optional[str] = None
    last_unit_cost: Optional[float] = None
    last_ordered_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PurchaseOrderEmailRequest(BaseModel):
    to: str
    cc: Optional[str] = None
    subject: str
    body: str
    attach_pdf: bool = True


class AttachmentResponse(BaseModel):
    id: int
    entity_type: str
    entity_id: int
    category: str
    filename: str
    content_type: Optional[str] = None
    size: int
    note: Optional[str] = None
    uploaded_by: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ReceiveLineRequest(InputModel):
    line_id: int
    quantity: float
    lot_code: Optional[str] = None
    expiry_date: Optional[datetime] = None


class ReceiveOrderRequest(BaseModel):
    lines: List[ReceiveLineRequest]
    # Deprecated: use Landed Costs. If sent, each becomes a landed cost applied to this PO by quantity.
    freight_cost: Optional[float] = None
    tariff_cost: Optional[float] = None


# ---- Landed costs ----
class LandedCostInput(BaseModel):
    description: str
    cost_type: str = "freight"
    amount: float
    paid_to: Optional[str] = None
    reference: Optional[str] = None
    cost_date: Optional[datetime] = None
    notes: Optional[str] = None
    po_ids: List[int]  # purchase orders to spread the amount over, by line quantity


class LandedCostPreviewRequest(BaseModel):
    amount: float
    po_ids: List[int]


class LandedCostAllocationResponse(BaseModel):
    po_id: int
    po_line_id: int
    item_id: int
    quantity: float
    amount: float
    per_unit: float

    class Config:
        from_attributes = True


class LandedCostResponse(BaseModel):
    id: int
    code: str
    description: str
    cost_type: str
    amount: float
    paid_to: Optional[str] = None
    reference: Optional[str] = None
    cost_date: Optional[datetime] = None
    notes: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    allocations: List[LandedCostAllocationResponse] = []

    class Config:
        from_attributes = True


# ---- Order profit ----
class ProfitComponent(BaseModel):
    kind: str  # shipped | booked | unbooked
    quantity: float
    unit_cost: Optional[float] = None
    cost: float
    revenue: float
    shipment_id: Optional[int] = None
    shipment_code: Optional[str] = None
    lot_id: Optional[int] = None
    lot_code: Optional[str] = None
    lot_source: Optional[str] = None  # PO code or "adjustment"
    estimated: bool = False


class ProfitLine(BaseModel):
    line_id: int
    line_no: Optional[int] = None
    item_id: int
    item_code: str
    item_title: str
    quantity: float
    unit_price: float
    revenue: float
    cost: float
    profit: float
    margin_pct: Optional[float] = None
    components: List[ProfitComponent]


class ProfitBucket(BaseModel):
    quantity: float = 0
    revenue: float = 0
    cost: float = 0
    profit: float = 0


class MissingLotCost(BaseModel):
    lot_id: int
    lot_code: str
    item_code: str
    quantity: float
    source: Optional[str] = None


class OrderProfitResponse(BaseModel):
    order_id: int
    order_code: str
    lines: List[ProfitLine]
    shipped: ProfitBucket
    booked: ProfitBucket
    unbooked: ProfitBucket
    revenue: float
    cogs: float
    gross_profit: float
    other_charges: float  # non-item invoice lines (e.g. shipping charged to the customer)
    shipping_cost: float  # what we paid carriers on this order's shipments
    net_profit: float
    margin_pct: Optional[float] = None
    missing_costs: List[MissingLotCost]
    warnings: List[str]


# ---- Test data ----
class TestDataResult(BaseModel):
    order_id: int
    order_code: str
    order_created: bool
    items: List[str]
    topped_up: List[str]


# ---- Company profile ----
class CompanyProfileUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    tax_id: Optional[str] = None
    invoice_notes: Optional[str] = None
    generic_stock_enabled: Optional[bool] = None


class CompanyProfileResponse(CompanyProfileUpdate):
    name: str
    has_logo: bool = False

    class Config:
        from_attributes = True


InvoiceResponse.model_rebuild()
PurchaseOrderResponse.model_rebuild()


class InvoiceFundingUpdate(BaseModel):
    """Set the funding fields by hand (any of them may be cleared with null)."""
    disbursement_date: Optional[datetime] = None
    funding_amount: Optional[float] = None
    funding_discount: Optional[float] = None


class FundingImportResponse(BaseModel):
    id: int
    file_name: Optional[str] = None
    record_payments: bool = True
    updated_count: int = 0
    skipped_count: int = 0
    rolled_back: bool = False
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    summary: Optional[dict] = None
ShipmentResponse.model_rebuild()
