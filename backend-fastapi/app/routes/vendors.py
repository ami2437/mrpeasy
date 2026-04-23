from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import VendorResponse
from app.services.crud import VendorService

router = APIRouter(prefix="/vendors", tags=["vendors"])


@router.get("/", response_model=list[VendorResponse])
def list_vendors(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    """Get all vendors"""
    vendors = VendorService.get_vendors(db, skip=skip, limit=limit)
    return vendors
