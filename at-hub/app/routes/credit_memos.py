"""Credit memos (money back to a customer) -- app/services/credit_memos.py. Money work: permission "credit_memos"."""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_perm
from app.models import User
from app.services import credit_memos as svc

router = APIRouter(prefix="/api/credit-memos", tags=["credit memos"], dependencies=[Depends(require_perm("credit_memos"))])


class CreditLineIn(BaseModel):
    item_id: Optional[int] = None
    order_line_id: Optional[int] = None
    description: str = ""
    quantity: float = 1
    unit_price: float = 0


class CreditLineOut(CreditLineIn):
    id: int
    amount: float = 0

    class Config:
        from_attributes = True


class CreditMemoIn(BaseModel):
    customer_id: Optional[int] = None
    order_id: Optional[int] = None
    invoice_id: Optional[int] = None
    memo_date: Optional[datetime] = None
    reason: Optional[str] = None
    lines: List[CreditLineIn] = []


class CreditMemoUpdate(BaseModel):
    memo_date: Optional[datetime] = None
    reason: Optional[str] = None
    lines: Optional[List[CreditLineIn]] = None


class Application(BaseModel):
    payment_id: int
    invoice_id: int
    amount: float
    paid_date: Optional[datetime] = None


class CreditMemoOut(BaseModel):
    id: int
    row_version: int = 1
    code: str
    customer_id: int
    order_id: Optional[int] = None
    invoice_id: Optional[int] = None
    memo_date: Optional[datetime] = None
    reason: Optional[str] = None
    status: str
    void_reason: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    lines: List[CreditLineOut] = []
    total: float = 0
    applied: float = 0
    remaining: float = 0
    applied_to: List[Application] = []

    class Config:
        from_attributes = True


class ApplyIn(BaseModel):
    invoice_id: int
    amount: Optional[float] = None  # default: as much as fits


class VoidIn(BaseModel):
    reason: Optional[str] = None


@router.get("/", response_model=List[CreditMemoOut])
def list_memos(customer_id: Optional[int] = None, db: Session = Depends(get_db)):
    return svc.list_all(db, customer_id)


@router.post("/", response_model=CreditMemoOut)
def create_memo(data: CreditMemoIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    return svc.create(db, data, user.username)


@router.get("/{memo_id}", response_model=CreditMemoOut)
def get_memo(memo_id: int, db: Session = Depends(get_db)):
    return svc.get(db, memo_id)


@router.put("/{memo_id}", response_model=CreditMemoOut)
def update_memo(memo_id: int, data: CreditMemoUpdate, db: Session = Depends(get_db)):
    return svc.update(db, memo_id, data)


@router.post("/{memo_id}/issue", response_model=CreditMemoOut)
def issue_memo(memo_id: int, db: Session = Depends(get_db)):
    return svc.issue(db, memo_id)


@router.post("/{memo_id}/apply", response_model=CreditMemoOut)
def apply_memo(memo_id: int, data: ApplyIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """Use the credit on one of the customer's open invoices (recorded there as a payment)."""
    return svc.apply(db, memo_id, data.invoice_id, data.amount, user.username)


@router.post("/{memo_id}/void", response_model=CreditMemoOut)
def void_memo(memo_id: int, data: VoidIn, db: Session = Depends(get_db)):
    return svc.void(db, memo_id, data.reason)


@router.delete("/{memo_id}", status_code=204)
def delete_memo(memo_id: int, db: Session = Depends(get_db)):
    svc.delete(db, memo_id)
    return Response(status_code=204)


@router.get("/{memo_id}/pdf")
def memo_pdf(memo_id: int, db: Session = Depends(get_db)):
    from app.services.pdf import credit_memo_pdf
    memo = svc.get(db, memo_id)
    return Response(credit_memo_pdf(db, memo), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="Credit-Memo-{memo.code}.pdf"'})
