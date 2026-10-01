from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import (
    StockItemCreate, StockItemUpdate, StockItemResponse, InventoryTransactionResponse,
    BulkPackSizeRequest, BulkPackSizeResult,
)
from app.services.crud import StockItemService, InventoryTransactionService
from app.dependencies import get_current_active_user
from app.models import User

router = APIRouter(prefix="/api/stock-items", tags=["stock-items"], dependencies=[Depends(get_current_active_user)])


@router.get("/", response_model=list[StockItemResponse])
def list_items(q: str | None = Query(None), low_stock_only: bool = Query(False), db: Session = Depends(get_db)):
    return StockItemService.list(db, q=q, low_stock_only=low_stock_only)


@router.post("/", response_model=StockItemResponse)
def create_item(data: StockItemCreate, db: Session = Depends(get_db)):
    return StockItemService.create(db, data)


@router.get("/activity/recent", response_model=list[InventoryTransactionResponse])
def recent_activity(limit: int = Query(25), db: Session = Depends(get_db)):
    return InventoryTransactionService.recent(db, limit=limit)


@router.post("/pack-sizes/bulk", response_model=BulkPackSizeResult)
def bulk_pack_sizes(data: BulkPackSizeRequest, db: Session = Depends(get_db)):
    """Paste-a-list bulk update of default_pack_size by item code."""
    return StockItemService.bulk_set_pack_sizes(db, data.entries)


@router.get("/{item_id}", response_model=StockItemResponse)
def get_item(item_id: int, db: Session = Depends(get_db)):
    return StockItemService.get(db, item_id)


@router.put("/{item_id}", response_model=StockItemResponse)
def update_item(item_id: int, data: StockItemUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return StockItemService.update(db, item_id, data, created_by=current_user.username)
