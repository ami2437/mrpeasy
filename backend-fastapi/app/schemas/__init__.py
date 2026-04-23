from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List


# Auth Schemas
class UserBase(BaseModel):
    username: str
    email: str
    full_name: Optional[str] = None
    role: str = "viewer"  # owner, admin, editor, viewer


class UserCreate(UserBase):
    password: str


class UserUpdate(BaseModel):
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: Optional[str] = None


class UserResponse(UserBase):
    id: int
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse


class TokenData(BaseModel):
    username: Optional[str] = None


class RoleResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None

    class Config:
        from_attributes = True


# Customer Order Schemas
class CustomerOrderBase(BaseModel):
    code: str
    customer_id: int
    customer_name: str
    status: int
    total_price_cur: Optional[float] = None
    currency: Optional[str] = None
    notes: Optional[str] = None


class CustomerOrderCreate(CustomerOrderBase):
    pass


class CustomerOrderUpdate(BaseModel):
    status: Optional[int] = None
    notes: Optional[str] = None
    total_price_cur: Optional[float] = None


class CustomerOrderResponse(CustomerOrderBase):
    id: int
    mrp_cust_ord_id: int
    reference: Optional[str] = None
    status_txt: str
    created: datetime
    delivery_date: Optional[datetime] = None
    synced_at: datetime

    class Config:
        from_attributes = True


class StockItemBase(BaseModel):
    code: str
    title: str
    is_raw: bool = False
    selling_price: Optional[float] = None


class StockItemCreate(StockItemBase):
    pass


class StockItemUpdate(BaseModel):
    title: Optional[str] = None
    selling_price: Optional[float] = None


class StockItemResponse(StockItemBase):
    id: int
    mrp_article_id: int
    product_id: Optional[int] = None
    unit: Optional[str] = None
    group_title: Optional[str] = None
    avg_cost: Optional[float] = None
    in_stock: float
    available: float
    booked: float
    synced_at: datetime

    class Config:
        from_attributes = True


class ManufacturingOrderBase(BaseModel):
    code: str
    article_id: int
    item_code: str
    item_title: str
    quantity: float
    status: int


class ManufacturingOrderCreate(ManufacturingOrderBase):
    pass


class ManufacturingOrderResponse(ManufacturingOrderBase):
    id: int
    mrp_man_ord_id: int
    status_txt: str
    due_date: Optional[datetime] = None
    total_cost: Optional[float] = None
    created: datetime
    synced_at: datetime

    class Config:
        from_attributes = True


class VendorBase(BaseModel):
    code: str
    title: str
    currency: Optional[str] = None


class VendorResponse(VendorBase):
    id: int
    mrp_vendor_id: int
    tax_rate: float
    payment_period: Optional[int] = None
    synced_at: datetime

    class Config:
        from_attributes = True


class InventoryResponse(BaseModel):
    id: int
    article_id: int
    item_code: str
    item_title: str
    quantity_on_hand: float
    quantity_available: float
    quantity_booked: float
    quantity_expected: float
    unit_cost: Optional[float] = None
    total_cost: Optional[float] = None
    snapshot_date: datetime

    class Config:
        from_attributes = True
