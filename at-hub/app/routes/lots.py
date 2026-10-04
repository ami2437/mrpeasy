from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import LotResponse, LotStatusUpdate, LotCostUpdate
from app.services.crud import LotService
from app.dependencies import get_current_active_user, require_perm, require_any

router = APIRouter(prefix="/api/lots", tags=["lots"], dependencies=[Depends(require_any("stock.view", "orders.view", "shipments.view", "purchasing"))])


@router.get("/", response_model=list[LotResponse])
def list_lots(item_id: int | None = Query(None), db: Session = Depends(get_db)):
    return LotService.list(db, item_id=item_id)


@router.get("/expiring", response_model=list[LotResponse])
def expiring_lots(within_days: int = Query(30), db: Session = Depends(get_db)):
    return LotService.expiring(db, within_days=within_days)


@router.get("/{lot_id}", response_model=LotResponse)
def get_lot(lot_id: int, db: Session = Depends(get_db)):
    return LotService.get(db, lot_id)


@router.put("/{lot_id}/status", response_model=LotResponse, dependencies=[Depends(require_perm("stock.edit"))])
def set_lot_status(lot_id: int, data: LotStatusUpdate, db: Session = Depends(get_db)):
    return LotService.set_status(db, lot_id, data.status)


@router.put("/{lot_id}/cost", response_model=LotResponse, dependencies=[Depends(require_perm("purchasing"))])
def set_lot_cost(lot_id: int, data: LotCostUpdate, db: Session = Depends(get_db)):
    """Record what a lot was acquired at (e.g. an adjustment lot created without a cost)."""
    return LotService.set_cost(db, lot_id, data.unit_cost)
