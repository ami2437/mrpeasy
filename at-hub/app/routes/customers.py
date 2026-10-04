from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import CustomerCreate, CustomerUpdate, PartyResponse
from app.services.crud import customer_service
from app.dependencies import get_current_active_user, require_perm, require_any

router = APIRouter(prefix="/api/customers", tags=["customers"], dependencies=[Depends(require_any("customers.view", "orders.view", "shipments.view", "invoices", "quotes", "pod.upload"))])


@router.get("/", response_model=list[PartyResponse])
def list_customers(db: Session = Depends(get_db)):
    return customer_service.list(db)


@router.post("/", response_model=PartyResponse, dependencies=[Depends(require_perm("customers.edit"))])
def create_customer(data: CustomerCreate, db: Session = Depends(get_db)):
    return customer_service.create(db, data)


@router.get("/{party_id}", response_model=PartyResponse)
def get_customer(party_id: int, db: Session = Depends(get_db)):
    return customer_service.get(db, party_id)


@router.put("/{party_id}", response_model=PartyResponse, dependencies=[Depends(require_perm("customers.edit"))])
def update_customer(party_id: int, data: CustomerUpdate, db: Session = Depends(get_db)):
    return customer_service.update(db, party_id, data)
