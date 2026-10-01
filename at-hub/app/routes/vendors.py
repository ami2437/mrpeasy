from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import PartyCreate, PartyUpdate, PartyResponse
from app.services.crud import vendor_service
from app.dependencies import get_current_active_user

router = APIRouter(prefix="/api/vendors", tags=["vendors"], dependencies=[Depends(get_current_active_user)])


@router.get("/", response_model=list[PartyResponse])
def list_vendors(db: Session = Depends(get_db)):
    return vendor_service.list(db)


@router.post("/", response_model=PartyResponse)
def create_vendor(data: PartyCreate, db: Session = Depends(get_db)):
    return vendor_service.create(db, data)


@router.get("/{party_id}", response_model=PartyResponse)
def get_vendor(party_id: int, db: Session = Depends(get_db)):
    return vendor_service.get(db, party_id)


@router.put("/{party_id}", response_model=PartyResponse)
def update_vendor(party_id: int, data: PartyUpdate, db: Session = Depends(get_db)):
    return vendor_service.update(db, party_id, data)
