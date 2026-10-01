from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import (
    InvoiceResponse, CreateInvoiceRequest, InvoiceUpdateRequest, InvoiceStatusUpdate,
)
from app.services.crud import InvoiceService
from app.dependencies import get_current_active_user
from app.models import User

router = APIRouter(prefix="/api/invoices", tags=["invoices"], dependencies=[Depends(get_current_active_user)])


@router.get("/", response_model=list[InvoiceResponse])
def list_invoices(db: Session = Depends(get_db)):
    return InvoiceService.list(db)


@router.get("/{invoice_id}", response_model=InvoiceResponse)
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    return InvoiceService.get(db, invoice_id)


@router.post("/from-shipment/{shipment_id}", response_model=InvoiceResponse)
def create_invoice_from_shipment(
    shipment_id: int,
    data: CreateInvoiceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return InvoiceService.create_from_shipment(db, shipment_id, data, created_by=current_user.username)


@router.put("/{invoice_id}", response_model=InvoiceResponse)
def update_invoice(invoice_id: int, data: InvoiceUpdateRequest, db: Session = Depends(get_db)):
    return InvoiceService.update(db, invoice_id, data)


@router.put("/{invoice_id}/status", response_model=InvoiceResponse)
def set_invoice_status(invoice_id: int, data: InvoiceStatusUpdate, db: Session = Depends(get_db)):
    return InvoiceService.set_status(db, invoice_id, data.status)
