from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List


# ---- Auth ----
class LoginRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    full_name: Optional[str] = None
    role: str
    is_active: bool

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse


# ---- Stock Items ----
class StockItemCreate(BaseModel):
    code: str
    title: str
    unit: Optional[str] = None
    cost_price: Optional[float] = 0
    selling_price: Optional[float] = 0
    reorder_point: Optional[float] = 0


class StockItemUpdate(BaseModel):
    title: Optional[str] = None
    unit: Optional[str] = None
    cost_price: Optional[float] = None
    selling_price: Optional[float] = None
    reorder_point: Optional[float] = None
    is_active: Optional[bool] = None
    on_hand: Optional[float] = None  # manual correction; logged as an 'adjustment' transaction, not silently overwritten


class StockItemResponse(BaseModel):
    id: int
    code: str
    title: str
    unit: Optional[str] = None
    cost_price: Optional[float] = None
    selling_price: Optional[float] = None
    on_hand: float
    reorder_point: Optional[float] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ---- Lots ----
class LotResponse(BaseModel):
    id: int
    item_id: int
    lot_code: str
    quantity: float
    received_date: datetime
    expiry_date: Optional[datetime] = None
    status: str
    source: Optional[str] = None
    source_reference: Optional[str] = None

    class Config:
        from_attributes = True


class LotStatusUpdate(BaseModel):
    status: str  # available | on_hold | rejected


# ---- Customers / Vendors ----
class PartyCreate(BaseModel):
    name: str
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None


class PartyUpdate(BaseModel):
    name: Optional[str] = None
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    is_active: Optional[bool] = None


class PartyResponse(BaseModel):
    id: int
    name: str
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    is_active: bool

    class Config:
        from_attributes = True


# ---- Customer Orders ----
class CustomerOrderLineCreate(BaseModel):
    item_id: int
    quantity: float
    unit_price: float = 0
    delivery_date: Optional[datetime] = None


class CustomerOrderCreate(BaseModel):
    customer_id: int
    delivery_date: Optional[datetime] = None
    notes: Optional[str] = None
    lines: List[CustomerOrderLineCreate]


class CustomerOrderLineResponse(BaseModel):
    id: int
    item_id: int
    quantity: float
    unit_price: float
    delivery_date: Optional[datetime] = None
    shipped_quantity: float

    class Config:
        from_attributes = True


class CustomerOrderResponse(BaseModel):
    id: int
    code: str
    customer_id: int
    order_date: datetime
    delivery_date: Optional[datetime] = None
    status: str
    notes: Optional[str] = None
    lines: List[CustomerOrderLineResponse] = []

    class Config:
        from_attributes = True


class ShipLineRequest(BaseModel):
    line_id: int
    quantity: float


class ShipOrderRequest(BaseModel):
    lines: List[ShipLineRequest]
    carrier: Optional[str] = None
    tracking_number: Optional[str] = None
    notes: Optional[str] = None


# ---- Shipments / Packing / Labels ----
class ShipmentLineResponse(BaseModel):
    id: int
    order_line_id: int
    item_id: int
    lot_id: Optional[int] = None
    quantity: float
    unit_price: float

    class Config:
        from_attributes = True


class ShipmentBoxInput(BaseModel):
    item_id: int
    box_number: int
    quantity_in_box: float
    lot_code: Optional[str] = None


class ShipmentBoxResponse(ShipmentBoxInput):
    id: int

    class Config:
        from_attributes = True


class SetBoxesRequest(BaseModel):
    boxes: List[ShipmentBoxInput]


class ShipmentResponse(BaseModel):
    id: int
    code: str
    order_id: int
    ship_date: datetime
    carrier: Optional[str] = None
    tracking_number: Optional[str] = None
    status: str
    notes: Optional[str] = None
    lines: List[ShipmentLineResponse] = []
    boxes: List[ShipmentBoxResponse] = []

    class Config:
        from_attributes = True


# ---- Invoices ----
class CreateInvoiceRequest(BaseModel):
    due_date: Optional[datetime] = None
    free_text: Optional[str] = None
    shipping_charge: Optional[float] = 0


class InvoiceLineInput(BaseModel):
    item_id: Optional[int] = None
    description: str
    quantity: float
    unit_price: float


class InvoiceLineResponse(InvoiceLineInput):
    id: int

    class Config:
        from_attributes = True


class InvoiceUpdateRequest(BaseModel):
    due_date: Optional[datetime] = None
    free_text: Optional[str] = None
    lines: Optional[List[InvoiceLineInput]] = None


class InvoiceStatusUpdate(BaseModel):
    status: str  # sent | paid | void


class InvoiceResponse(BaseModel):
    id: int
    code: str
    customer_id: int
    order_id: Optional[int] = None
    shipment_id: Optional[int] = None
    invoice_date: datetime
    due_date: Optional[datetime] = None
    status: str
    free_text: Optional[str] = None
    lines: List[InvoiceLineResponse] = []

    class Config:
        from_attributes = True


# ---- Purchase Orders ----
class PurchaseOrderLineCreate(BaseModel):
    item_id: int
    quantity: float
    unit_cost: float = 0


class PurchaseOrderCreate(BaseModel):
    vendor_id: int
    expected_date: Optional[datetime] = None
    notes: Optional[str] = None
    lines: List[PurchaseOrderLineCreate]


class PurchaseOrderLineResponse(BaseModel):
    id: int
    item_id: int
    quantity: float
    unit_cost: float
    received_quantity: float

    class Config:
        from_attributes = True


class PurchaseOrderResponse(BaseModel):
    id: int
    code: str
    vendor_id: int
    order_date: datetime
    expected_date: Optional[datetime] = None
    status: str
    freight_cost: Optional[float] = None
    tariff_cost: Optional[float] = None
    notes: Optional[str] = None
    lines: List[PurchaseOrderLineResponse] = []

    class Config:
        from_attributes = True


class ReceiveLineRequest(BaseModel):
    line_id: int
    quantity: float
    lot_code: Optional[str] = None
    expiry_date: Optional[datetime] = None


class ReceiveOrderRequest(BaseModel):
    lines: List[ReceiveLineRequest]
    freight_cost: Optional[float] = None
    tariff_cost: Optional[float] = None
