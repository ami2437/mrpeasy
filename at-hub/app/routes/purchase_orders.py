from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import PurchaseOrderCreate, PurchaseOrderResponse, ReceiveOrderRequest
from app.services.crud import PurchaseOrderService
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


@router.post("/{po_id}/mark-ordered", response_model=PurchaseOrderResponse)
def mark_ordered(po_id: int, db: Session = Depends(get_db)):
    return PurchaseOrderService.mark_ordered(db, po_id)


@router.post("/{po_id}/receive", response_model=PurchaseOrderResponse)
def receive_order(po_id: int, data: ReceiveOrderRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return PurchaseOrderService.receive(db, po_id, data, created_by=current_user.username)
