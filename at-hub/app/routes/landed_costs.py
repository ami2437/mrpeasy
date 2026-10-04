from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import LandedCostInput, LandedCostPreviewRequest, LandedCostResponse, LandedCostAllocationResponse
from app.services.crud import LandedCostService
from app.dependencies import get_current_active_user, require_perm, require_any
from app.models import User

router = APIRouter(prefix="/api/landed-costs", tags=["landed-costs"], dependencies=[Depends(require_perm("landed_costs"))])  # no dollar work for employees


@router.get("/", response_model=list[LandedCostResponse])
def list_landed_costs(db: Session = Depends(get_db)):
    return LandedCostService.list(db)


@router.post("/preview", response_model=list[LandedCostAllocationResponse])
def preview_allocation(data: LandedCostPreviewRequest, db: Session = Depends(get_db)):
    """How the amount would split across the selected POs' lines, without saving anything."""
    return LandedCostService.plan(db, data.amount, data.po_ids)


@router.post("/", response_model=LandedCostResponse)
def create_landed_cost(data: LandedCostInput, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return LandedCostService.create(db, data, created_by=current_user.username)


@router.get("/{lc_id}", response_model=LandedCostResponse)
def get_landed_cost(lc_id: int, db: Session = Depends(get_db)):
    return LandedCostService.get(db, lc_id)


@router.put("/{lc_id}", response_model=LandedCostResponse)
def update_landed_cost(lc_id: int, data: LandedCostInput, db: Session = Depends(get_db)):
    return LandedCostService.update(db, lc_id, data)


@router.delete("/{lc_id}")
def delete_landed_cost(lc_id: int, db: Session = Depends(get_db)):
    LandedCostService.delete(db, lc_id)
    return {"deleted": True}
