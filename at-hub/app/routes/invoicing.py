from fastapi import APIRouter, Depends, HTTPException, Response
from typing import List
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import (
    InvoiceResponse, CreateInvoiceRequest, InvoiceUpdateRequest, InvoiceStatusUpdate, InvoicePaymentInput,
    InvoiceEmailRequest, EmailConfigResponse, InvoiceFundingUpdate,
    CreateCombinedInvoiceRequest, MergeInvoicesRequest, InvoicePrintOptions, InvoiceLineInput,
)
from app.services.crud import InvoiceService, InvoicePaymentService
from app.services import email as email_service
from app.services import filenames
from app.services.pdf import invoice_pdf
from app.config.settings import settings
from app.dependencies import get_current_active_user, require_perm, require_any
from app.models import User

# Invoicing is manager work: employees ship, they don't bill or see dollar amounts.
router = APIRouter(prefix="/api/invoices", tags=["invoices"], dependencies=[Depends(require_perm("invoices"))])


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
                    headers={"Content-Disposition": filenames.disposition(filenames.invoice_name(db, invoice))})


class RenameIn(BaseModel):
    code: str


@router.put("/{invoice_id}/code", response_model=InvoiceResponse)
def rename_invoice(invoice_id: int, data: RenameIn, db: Session = Depends(get_db)):
    """Change the invoice # (unique; printed on it from now on)."""
    return InvoiceService.rename(db, invoice_id, data.code)


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
def update_invoice(invoice_id: int, data: InvoiceUpdateRequest, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_active_user)):
    return InvoiceService.update(db, invoice_id, data, by=current_user.username)


class QtyCheckIn(BaseModel):
    lines: List[InvoiceLineInput]


@router.post("/{invoice_id}/qty-check")
def qty_check(invoice_id: int, data: QtyCheckIn, db: Session = Depends(get_db)):
    """Before saving: the lines that would bill more or less than the invoice's shipments delivered."""
    from app.services import billing
    return billing.invoice_differences(db, InvoiceService.get(db, invoice_id), [l.model_dump() for l in data.lines])


@router.get("/{invoice_id}/billing-check")
def billing_check(invoice_id: int, db: Session = Depends(get_db)):
    """What the invoice's shipments delivered per order line (the screen warns as you type) and the saved
    invoice's over / under-billed lines with the reason they were accepted."""
    from app.services import billing
    inv = InvoiceService.get(db, invoice_id)
    return {"delivered": {str(k): v for k, v in billing.delivered_for(inv).items()}, "differences": billing.open_differences(db, inv)}


@router.put("/{invoice_id}/status", response_model=InvoiceResponse)
def set_invoice_status(invoice_id: int, data: InvoiceStatusUpdate, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_active_user)):
    return InvoiceService.set_status(db, invoice_id, data.status, reason=data.reason, by=current_user.username)


@router.post("/{invoice_id}/payments", response_model=InvoiceResponse)
def record_invoice_payment(invoice_id: int, data: InvoicePaymentInput, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return InvoicePaymentService.record(db, invoice_id, data, created_by=current_user.username)


@router.delete("/{invoice_id}", status_code=204)
def delete_invoice(invoice_id: int, db: Session = Depends(get_db)):
    """Delete a void invoice, or a draft that never went out (it goes to the Recycle Bin)."""
    from app.services.crud import delete_invoice as _delete
    _delete(db, invoice_id)
    return Response(status_code=204)


@router.delete("/{invoice_id}/payments/{payment_id}", response_model=InvoiceResponse, dependencies=[Depends(require_perm("invoices"))])
def remove_invoice_payment(invoice_id: int, payment_id: int, db: Session = Depends(get_db)):
    """Take a recorded payment off (wrong entry, refund, or before undoing the shipment)."""
    return InvoicePaymentService.remove(db, invoice_id, payment_id)


@router.put("/{invoice_id}/funding", response_model=InvoiceResponse, dependencies=[Depends(require_perm("invoices.funding"))])
def set_invoice_funding(invoice_id: int, data: InvoiceFundingUpdate, db: Session = Depends(get_db)):
    """Edit disbursement date / funding amount / discount by hand. Payments aren't touched."""
    if any(v is not None and v < 0 for v in (data.funding_amount, data.funding_discount)):
        raise HTTPException(status_code=400, detail="Funding amount and discount can't be negative")
    invoice = InvoiceService.get(db, invoice_id)
    for key, value in data.model_dump().items():
        setattr(invoice, key, value)
    db.commit()
    db.refresh(invoice)
    return invoice
