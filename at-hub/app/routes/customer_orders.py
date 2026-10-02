from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import (
    CustomerOrderCreate, CustomerOrderResponse, CreateShipmentRequest, ShipmentResponse,
    CustomerOrderUpdate, CustomerOrderLineAdd, CustomerOrderLineUpdate, OrderProfitResponse,
)
from app.services.crud import CustomerOrderService, OrderProfitService
from app.dependencies import get_current_active_user, require_role

manager = [Depends(require_role("manager"))]  # creating/editing/pricing orders
from app.models import User

router = APIRouter(prefix="/api/customer-orders", tags=["customer-orders"], dependencies=[Depends(get_current_active_user)])


@router.get("/", response_model=list[CustomerOrderResponse])
def list_orders(status: str | None = Query(None), db: Session = Depends(get_db)):
    return CustomerOrderService.list(db, status=status)


@router.post("/", response_model=CustomerOrderResponse, dependencies=manager)
def create_order(data: CustomerOrderCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return CustomerOrderService.create(db, data, created_by=current_user.username)


@router.get("/{order_id}", response_model=CustomerOrderResponse)
def get_order(order_id: int, db: Session = Depends(get_db)):
    return CustomerOrderService.get(db, order_id)


@router.get("/{order_id}/profit", response_model=OrderProfitResponse, dependencies=manager)
def order_profit(order_id: int, db: Session = Depends(get_db)):
    """Revenue vs. lot cost (incl. landed costs) for shipped, booked, and not-yet-booked quantity."""
    return OrderProfitService.calculate(db, order_id)


@router.put("/{order_id}", response_model=CustomerOrderResponse, dependencies=manager)
def update_order(order_id: int, data: CustomerOrderUpdate, db: Session = Depends(get_db)):
    return CustomerOrderService.update(db, order_id, data)


@router.post("/{order_id}/lines", response_model=CustomerOrderResponse, dependencies=manager)
def add_line(order_id: int, data: CustomerOrderLineAdd, db: Session = Depends(get_db)):
    return CustomerOrderService.add_line(db, order_id, data)


@router.put("/{order_id}/lines/{line_id}", response_model=CustomerOrderResponse, dependencies=manager)
def update_line(order_id: int, line_id: int, data: CustomerOrderLineUpdate, db: Session = Depends(get_db)):
    return CustomerOrderService.update_line(db, order_id, line_id, data)


@router.delete("/{order_id}/lines/{line_id}", response_model=CustomerOrderResponse, dependencies=manager)
def remove_line(order_id: int, line_id: int, db: Session = Depends(get_db)):
    return CustomerOrderService.remove_line(db, order_id, line_id)


@router.post("/{order_id}/confirm", response_model=CustomerOrderResponse, dependencies=manager)
def confirm_order(order_id: int, db: Session = Depends(get_db)):
    return CustomerOrderService.confirm(db, order_id)


@router.post("/{order_id}/cancel", response_model=CustomerOrderResponse, dependencies=manager)
def cancel_order(order_id: int, db: Session = Depends(get_db)):
    return CustomerOrderService.cancel(db, order_id)


@router.post("/{order_id}/shipments", response_model=ShipmentResponse)
def create_shipment(order_id: int, data: CreateShipmentRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Create a shipment and book stock into it. Stock leaves on-hand only once the shipment is fully picked."""
    return CustomerOrderService.create_shipment(db, order_id, data, created_by=current_user.username)
