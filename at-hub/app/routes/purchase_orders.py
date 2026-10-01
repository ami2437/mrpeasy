from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import (
    PurchaseOrderCreate, PurchaseOrderResponse, ReceiveOrderRequest,
    PurchaseOrderUpdate, PurchaseOrderLineAdd, PurchaseOrderLineUpdate, PurchaseOrderPaymentInput,
)
from app.services.crud import PurchaseOrderService, PurchaseOrderPaymentService
from app.dependencies import get_current_active_user
from app.models import User

router = APIRouter(prefix="/api/purchase-orders", tags=["purchase-orders"], dependencies=[Depends(get_current_active_user)])


@router.get("/", response_model=list[PurchaseOrderResponse])
def list_orders(status: str | None = Query(None), db: Session = Depends(get_db)):
    return PurchaseOrderService.list(db, status=status)


@router.post("/", response_model=PurchaseOrderResponse)
def create_order(data: PurchaseOrderCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return PurchaseOrderService.create(db, data, created_by=current_user.username)


@router.get("/{po_id}", response_model=PurchaseOrderResponse)
def get_order(po_id: int, db: Session = Depends(get_db)):
    return PurchaseOrderService.get(db, po_id)


@router.put("/{po_id}", response_model=PurchaseOrderResponse)
def update_order(po_id: int, data: PurchaseOrderUpdate, db: Session = Depends(get_db)):
    return PurchaseOrderService.update(db, po_id, data)


@router.post("/{po_id}/lines", response_model=PurchaseOrderResponse)
def add_line(po_id: int, data: PurchaseOrderLineAdd, db: Session = Depends(get_db)):
    return PurchaseOrderService.add_line(db, po_id, data)


@router.put("/{po_id}/lines/{line_id}", response_model=PurchaseOrderResponse)
def update_line(po_id: int, line_id: int, data: PurchaseOrderLineUpdate, db: Session = Depends(get_db)):
    return PurchaseOrderService.update_line(db, po_id, line_id, data)


@router.delete("/{po_id}/lines/{line_id}", response_model=PurchaseOrderResponse)
def remove_line(po_id: int, line_id: int, db: Session = Depends(get_db)):
    return PurchaseOrderService.remove_line(db, po_id, line_id)


@router.post("/{po_id}/mark-ordered", response_model=PurchaseOrderResponse)
def mark_ordered(po_id: int, db: Session = Depends(get_db)):
    return PurchaseOrderService.mark_ordered(db, po_id)


@router.post("/{po_id}/cancel", response_model=PurchaseOrderResponse)
def cancel_order(po_id: int, db: Session = Depends(get_db)):
    return PurchaseOrderService.cancel(db, po_id)


@router.post("/{po_id}/receive", response_model=PurchaseOrderResponse)
def receive_order(po_id: int, data: ReceiveOrderRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return PurchaseOrderService.receive(db, po_id, data, created_by=current_user.username)


@router.post("/{po_id}/payments", response_model=PurchaseOrderResponse)
def record_payment(po_id: int, data: PurchaseOrderPaymentInput, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return PurchaseOrderPaymentService.record(db, po_id, data, created_by=current_user.username)
