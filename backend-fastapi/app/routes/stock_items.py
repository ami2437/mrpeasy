from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import StockItemResponse
from app.services.crud import StockItemService

router = APIRouter(prefix="/stock-items", tags=["stock-items"])


@router.get("/", response_model=list[StockItemResponse])
def list_items(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    """Get all stock items"""
    items = StockItemService.get_items(db, skip=skip, limit=limit)
    return items


@router.get("/search", response_model=list[StockItemResponse])
def search_items(
    q: str = Query(..., min_length=1),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    """Search stock items by code or title"""
    items = StockItemService.search_items(db, q, skip=skip, limit=limit)
    return items


@router.get("/low-stock", response_model=list[StockItemResponse])
def get_low_stock_items(
    limit: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    """Get low stock items"""
    items = StockItemService.get_low_stock(db, limit=limit)
    return items


@router.get("/{item_id}", response_model=StockItemResponse)
def get_item(item_id: int, db: Session = Depends(get_db)):
    """Get specific stock item"""
    item = StockItemService.get_item(db, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item
