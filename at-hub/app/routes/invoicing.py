from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import (
    InvoiceResponse, CreateInvoiceRequest, InvoiceUpdateRequest, InvoiceStatusUpdate, InvoicePaymentInput,
    InvoiceEmailRequest, EmailConfigResponse,
)
from app.services.crud import InvoiceService, InvoicePaymentService
from app.services import email as email_service
from app.services.pdf import invoice_pdf
from app.config.settings import settings
from app.dependencies import get_current_active_user
from app.models import User

router = APIRouter(prefix="/api/invoices", tags=["invoices"], dependencies=[Depends(get_current_active_user)])


@router.get("/", response_model=list[InvoiceResponse])
def list_invoices(db: Session = Depends(get_db)):
    return InvoiceService.list(db)


@router.get("/email/config", response_model=EmailConfigResponse)
def email_config():
    """Whether SMTP is set up, so the UI can explain instead of failing on Send."""
    return {
        "configured": email_service.is_configured(),
        "from_address": email_service.from_address() or None,
        "host": settings.smtp_host or None,
    }


@router.get("/{invoice_id}", response_model=InvoiceResponse)
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    return InvoiceService.get(db, invoice_id)


@router.get("/{invoice_id}/pdf")
def get_invoice_pdf(invoice_id: int, db: Session = Depends(get_db)):
    invoice = InvoiceService.get(db, invoice_id)
    return Response(invoice_pdf(db, invoice), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{invoice.code}.pdf"'})


@router.post("/{invoice_id}/email", response_model=InvoiceResponse)
def email_invoice(invoice_id: int, data: InvoiceEmailRequest, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_active_user)):
    """Email the invoice (PDF attached) with the user's own message. A draft becomes sent."""
    invoice = InvoiceService.get(db, invoice_id)
    email_service.send_invoice(db, invoice, data.to, data.cc, data.subject, data.body, data.attach_pdf,
                               sent_by=current_user.username)
    db.refresh(invoice)
    return invoice


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


@router.post("/{invoice_id}/payments", response_model=InvoiceResponse)
def record_invoice_payment(invoice_id: int, data: InvoicePaymentInput, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return InvoicePaymentService.record(db, invoice_id, data, created_by=current_user.username)
