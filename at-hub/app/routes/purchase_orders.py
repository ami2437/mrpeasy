from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import (
    PurchaseOrderCreate, PurchaseOrderResponse, ReceiveOrderRequest,
    PurchaseOrderUpdate, PurchaseOrderLineAdd, PurchaseOrderLineUpdate, PurchaseOrderPaymentInput,
    PurchaseOrderEmailRequest, VendorBillInput, PurchaseOrderChargeInput,
)
from app.dependencies import require_role
from app.services.pdf import purchase_order_pdf
from app.services import email as email_service
from app.services.crud import PurchaseOrderService, PurchaseOrderPaymentService, VendorBillService, PurchaseOrderChargeService
from app.dependencies import get_current_active_user
from app.models import User

router = APIRouter(prefix="/api/purchase-orders", tags=["purchase-orders"], dependencies=[Depends(require_role("manager"))])  # no dollar work for employees


@router.get("/", response_model=list[PurchaseOrderResponse])
def list_orders(status: str | None = Query(None), db: Session = Depends(get_db)):
    return PurchaseOrderService.list(db, status=status)


@router.post("/", response_model=PurchaseOrderResponse)
def create_order(data: PurchaseOrderCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return PurchaseOrderService.create(db, data, created_by=current_user.username)


@router.get("/{po_id}", response_model=PurchaseOrderResponse)
def get_order(po_id: int, db: Session = Depends(get_db)):
    return PurchaseOrderService.get(db, po_id)


@router.get("/{po_id}/pdf")
def po_pdf(po_id: int, vendor: bool = False, db: Session = Depends(get_db)):
    """?vendor=true: the copy for the vendor (their part #s only). Default: internal copy with our item #s."""
    po = PurchaseOrderService.get(db, po_id)
    suffix = "" if vendor else "-internal"
    return Response(purchase_order_pdf(db, po, for_vendor=vendor), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{po.code}{suffix}.pdf"'})


@router.post("/{po_id}/email", response_model=PurchaseOrderResponse)
def email_po(po_id: int, data: PurchaseOrderEmailRequest, db: Session = Depends(get_db),
             current_user: User = Depends(get_current_active_user)):
    po = PurchaseOrderService.get(db, po_id)
    email_service.send_purchase_order(db, po, data.to, data.cc, data.subject, data.body, data.attach_pdf,
                                      sent_by=current_user.username)
    db.refresh(po)
    return po


@router.put("/{po_id}", response_model=PurchaseOrderResponse)
def update_order(po_id: int, data: PurchaseOrderUpdate, db: Session = Depends(get_db)):
    return PurchaseOrderService.update(db, po_id, data)


@router.post("/{po_id}/lines", response_model=PurchaseOrderResponse)
def add_line(po_id: int, data: PurchaseOrderLineAdd, db: Session = Depends(get_db)):
    return PurchaseOrderService.add_line(db, po_id, data)


@router.put("/{po_id}/lines/{line_id}", response_model=PurchaseOrderResponse)
def update_line(po_id: int, line_id: int, data: PurchaseOrderLineUpdate, db: Session = Depends(get_db)):
    return PurchaseOrderService.update_line(db, po_id, line_id, data)


@router.delete("/{po_id}/lines/{line_id}", response_model=PurchaseOrderResponse)
def remove_line(po_id: int, line_id: int, db: Session = Depends(get_db)):
    return PurchaseOrderService.remove_line(db, po_id, line_id)


@router.post("/{po_id}/mark-ordered", response_model=PurchaseOrderResponse)
def mark_ordered(po_id: int, db: Session = Depends(get_db)):
    return PurchaseOrderService.mark_ordered(db, po_id)


@router.post("/{po_id}/cancel", response_model=PurchaseOrderResponse)
def cancel_order(po_id: int, db: Session = Depends(get_db)):
    return PurchaseOrderService.cancel(db, po_id)


@router.post("/{po_id}/receive", response_model=PurchaseOrderResponse)
def receive_order(po_id: int, data: ReceiveOrderRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return PurchaseOrderService.receive(db, po_id, data, created_by=current_user.username)


@router.post("/{po_id}/payments", response_model=PurchaseOrderResponse)
def record_payment(po_id: int, data: PurchaseOrderPaymentInput, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return PurchaseOrderPaymentService.record(db, po_id, data, created_by=current_user.username)


@router.post("/{po_id}/bills", response_model=PurchaseOrderResponse)
def add_bill(po_id: int, data: VendorBillInput, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Record a vendor invoice (one PO can be billed in several)."""
    return VendorBillService.create(db, po_id, data, created_by=current_user.username)


@router.delete("/{po_id}/bills/{bill_id}", response_model=PurchaseOrderResponse, dependencies=[Depends(require_role("manager"))])
def delete_bill(po_id: int, bill_id: int, db: Session = Depends(get_db)):
    return VendorBillService.delete(db, po_id, bill_id)


@router.post("/{po_id}/charges", response_model=PurchaseOrderResponse)
def add_charge(po_id: int, data: PurchaseOrderChargeInput, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Freight / shipping / handling on top of the lines (counted in the PO total)."""
    return PurchaseOrderChargeService.add(db, po_id, data, created_by=current_user.username)


@router.delete("/{po_id}/charges/{charge_id}", response_model=PurchaseOrderResponse)
def remove_charge(po_id: int, charge_id: int, db: Session = Depends(get_db)):
    return PurchaseOrderChargeService.remove(db, po_id, charge_id)
