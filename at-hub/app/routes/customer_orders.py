from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import (
    CustomerOrderCreate, CustomerOrderResponse, ShipOrderRequest, ShipmentResponse,
    CustomerOrderUpdate, CustomerOrderLineAdd, CustomerOrderLineUpdate,
)
from app.services.crud import CustomerOrderService
from app.dependencies import get_current_active_user
from app.models import User

router = APIRouter(prefix="/api/customer-orders", tags=["customer-orders"], dependencies=[Depends(get_current_active_user)])


@router.get("/", response_model=list[CustomerOrderResponse])
def list_orders(status: str | None = Query(None), db: Session = Depends(get_db)):
    return CustomerOrderService.list(db, status=status)


@router.post("/", response_model=CustomerOrderResponse)
def create_order(data: CustomerOrderCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return CustomerOrderService.create(db, data, created_by=current_user.username)


@router.get("/{order_id}", response_model=CustomerOrderResponse)
def get_order(order_id: int, db: Session = Depends(get_db)):
    return CustomerOrderService.get(db, order_id)


@router.put("/{order_id}", response_model=CustomerOrderResponse)
def update_order(order_id: int, data: CustomerOrderUpdate, db: Session = Depends(get_db)):
    return CustomerOrderService.update(db, order_id, data)


@router.post("/{order_id}/lines", response_model=CustomerOrderResponse)
def add_line(order_id: int, data: CustomerOrderLineAdd, db: Session = Depends(get_db)):
    return CustomerOrderService.add_line(db, order_id, data)


@router.put("/{order_id}/lines/{line_id}", response_model=CustomerOrderResponse)
def update_line(order_id: int, line_id: int, data: CustomerOrderLineUpdate, db: Session = Depends(get_db)):
    return CustomerOrderService.update_line(db, order_id, line_id, data)


@router.delete("/{order_id}/lines/{line_id}", response_model=CustomerOrderResponse)
def remove_line(order_id: int, line_id: int, db: Session = Depends(get_db)):
    return CustomerOrderService.remove_line(db, order_id, line_id)


@router.post("/{order_id}/confirm", response_model=CustomerOrderResponse)
def confirm_order(order_id: int, db: Session = Depends(get_db)):
    return CustomerOrderService.confirm(db, order_id)


@router.post("/{order_id}/cancel", response_model=CustomerOrderResponse)
def cancel_order(order_id: int, db: Session = Depends(get_db)):
    return CustomerOrderService.cancel(db, order_id)


@router.post("/{order_id}/ship", response_model=ShipmentResponse)
def ship_order(order_id: int, data: ShipOrderRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return CustomerOrderService.ship(db, order_id, data, created_by=current_user.username)
