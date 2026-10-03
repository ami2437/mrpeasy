from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import (
    InvoiceResponse, CreateInvoiceRequest, InvoiceUpdateRequest, InvoiceStatusUpdate, InvoicePaymentInput,
    InvoiceEmailRequest, EmailConfigResponse, InvoiceFundingUpdate,
    CreateCombinedInvoiceRequest, MergeInvoicesRequest, InvoicePrintOptions,
)
from app.services.crud import InvoiceService, InvoicePaymentService
from app.services import email as email_service
from app.services.pdf import invoice_pdf
from app.config.settings import settings
from app.dependencies import get_current_active_user, require_role
from app.models import User

# Invoicing is manager work: employees ship, they don't bill or see dollar amounts.
router = APIRouter(prefix="/api/invoices", tags=["invoices"], dependencies=[Depends(require_role("manager"))])


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
def get_invoice_pdf(invoice_id: int, notes: bool = True, db: Session = Depends(get_db)):
    """?notes=false leaves every line note off this print."""
    invoice = InvoiceService.get(db, invoice_id)
    return Response(invoice_pdf(db, invoice, show_notes=notes), media_type="application/pdf",
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


@router.post("/from-shipments", response_model=InvoiceResponse)
def create_combined_invoice(data: CreateCombinedInvoiceRequest, db: Session = Depends(get_db),
                            current_user: User = Depends(get_current_active_user)):
    """One invoice for several shipments of the same order."""
    return InvoiceService.create_from_shipments(db, data.shipment_ids, data, created_by=current_user.username)


@router.post("/{invoice_id}/merge", response_model=InvoiceResponse)
def merge_invoices(invoice_id: int, data: MergeInvoicesRequest, db: Session = Depends(get_db)):
    """Combine other draft invoices of the same order into this one."""
    return InvoiceService.merge(db, invoice_id, data.invoice_ids)


@router.post("/{invoice_id}/split", response_model=list[InvoiceResponse])
def split_invoice(invoice_id: int, db: Session = Depends(get_db)):
    """Undo a combined invoice."""
    return InvoiceService.split(db, invoice_id)


@router.put("/{invoice_id}/print-options", response_model=InvoiceResponse)
def set_print_options(invoice_id: int, data: InvoicePrintOptions, db: Session = Depends(get_db)):
    invoice = InvoiceService.get(db, invoice_id)
    invoice.print_zero_lines = data.print_zero_lines
    db.commit()
    db.refresh(invoice)
    return invoice


@router.put("/{invoice_id}", response_model=InvoiceResponse)
def update_invoice(invoice_id: int, data: InvoiceUpdateRequest, db: Session = Depends(get_db)):
    return InvoiceService.update(db, invoice_id, data)


@router.put("/{invoice_id}/status", response_model=InvoiceResponse)
def set_invoice_status(invoice_id: int, data: InvoiceStatusUpdate, db: Session = Depends(get_db)):
    return InvoiceService.set_status(db, invoice_id, data.status)


@router.post("/{invoice_id}/payments", response_model=InvoiceResponse)
def record_invoice_payment(invoice_id: int, data: InvoicePaymentInput, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return InvoicePaymentService.record(db, invoice_id, data, created_by=current_user.username)


@router.put("/{invoice_id}/funding", response_model=InvoiceResponse, dependencies=[Depends(require_role("manager"))])
def set_invoice_funding(invoice_id: int, data: InvoiceFundingUpdate, db: Session = Depends(get_db)):
    """Edit disbursement date / funding amount / discount by hand. Payments aren't touched."""
    invoice = InvoiceService.get(db, invoice_id)
    for key, value in data.model_dump().items():
        setattr(invoice, key, value)
    db.commit()
    db.refresh(invoice)
    return invoice
