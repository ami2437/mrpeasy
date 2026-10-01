from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import LotResponse, LotStatusUpdate
from app.services.crud import LotService
from app.dependencies import get_current_active_user

router = APIRouter(prefix="/api/lots", tags=["lots"], dependencies=[Depends(get_current_active_user)])


@router.get("/", response_model=list[LotResponse])
def list_lots(item_id: int | None = Query(None), db: Session = Depends(get_db)):
    return LotService.list(db, item_id=item_id)


@router.get("/{lot_id}", response_model=LotResponse)
def get_lot(lot_id: int, db: Session = Depends(get_db)):
    return LotService.get(db, lot_id)


@router.put("/{lot_id}/status", response_model=LotResponse)
def set_lot_status(lot_id: int, data: LotStatusUpdate, db: Session = Depends(get_db)):
    return LotService.set_status(db, lot_id, data.status)
