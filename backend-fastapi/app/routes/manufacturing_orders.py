from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import ManufacturingOrderResponse
from app.services.crud import ManufacturingOrderService

router = APIRouter(prefix="/manufacturing-orders", tags=["manufacturing-orders"])


@router.get("/", response_model=list[ManufacturingOrderResponse])
def list_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    """Get all manufacturing orders"""
    orders = ManufacturingOrderService.get_orders(db, skip=skip, limit=limit)
    return orders


@router.get("/active", response_model=list[ManufacturingOrderResponse])
def get_active_orders(
    limit: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    """Get active manufacturing orders"""
    orders = ManufacturingOrderService.get_active_orders(db, limit=limit)
    return orders


@router.get("/{order_id}", response_model=ManufacturingOrderResponse)
def get_order(order_id: int, db: Session = Depends(get_db)):
    """Get specific manufacturing order"""
    order = ManufacturingOrderService.get_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order
