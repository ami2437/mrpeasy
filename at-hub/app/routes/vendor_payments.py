"""Vendor payments made on their own -- often before the purchase order exists -- and applied
to POs later. Whatever isn't applied yet is offered on every PO for that vendor."""
from typing import List, Optional

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_role
from app.models import User
from app.schemas import ApplyVendorPaymentRequest, VendorPaymentInput, VendorPaymentResponse
from app.services.crud import VendorPaymentService

router = APIRouter(prefix="/api/vendor-payments", tags=["vendor-payments"], dependencies=[Depends(require_role("manager"))])


@router.get("/", response_model=List[VendorPaymentResponse])
def list_payments(vendor_id: Optional[int] = None, open_only: bool = False, db: Session = Depends(get_db)):
    return VendorPaymentService.list(db, vendor_id, open_only)


@router.post("/", response_model=VendorPaymentResponse)
def create_payment(data: VendorPaymentInput, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    return VendorPaymentService.create(db, data, user.username)


@router.post("/{payment_id}/apply", response_model=VendorPaymentResponse)
def apply_payment(payment_id: int, data: ApplyVendorPaymentRequest, db: Session = Depends(get_db),
                  user: User = Depends(get_current_active_user)):
    return VendorPaymentService.apply(db, payment_id, data, user.username)


@router.delete("/{payment_id}/applications/{po_payment_id}", response_model=VendorPaymentResponse)
def unapply_payment(payment_id: int, po_payment_id: int, db: Session = Depends(get_db)):
    return VendorPaymentService.unapply(db, payment_id, po_payment_id)


@router.delete("/{payment_id}", status_code=204)
def delete_payment(payment_id: int, db: Session = Depends(get_db)):
    VendorPaymentService.delete(db, payment_id)
    return Response(status_code=204)
