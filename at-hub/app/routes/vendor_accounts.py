"""Vendor Accounts (services/vendor_accounts.py): vendors paid partly by wire and partly in cash -- bills at their true
amount, every wire / cash payment and what it paid, the running balance, a statement to send them (PDF / Excel), their
spreadsheet loaded as the opening history, and a statement they send back compared. Money permission "vendor_accounts"
(Admin and Super admin until a role is given it)."""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_perm
from app.models import User
from app.services import vendor_accounts as VA
from app.services.filenames import disposition

router = APIRouter(prefix="/api/vendor-accounts", tags=["vendor-accounts"], dependencies=[Depends(require_perm("vendor_accounts"))])
MAX_SHEET = 15 * 1024 * 1024


class AccountIn(BaseModel):
    vendor_id: int
    cash_share: Optional[float] = 50


class AccountEdit(BaseModel):
    cash_share: Optional[float] = None
    note: Optional[str] = None


class BillIn(BaseModel):
    so_number: Optional[str] = None
    so_amount: Optional[float] = None
    po_code: Optional[str] = None
    bill_number: Optional[str] = None
    bill_date: Optional[str] = None
    goods: Optional[float] = None
    freight: Optional[float] = None
    customs: Optional[float] = None
    other: Optional[float] = None
    paper_amount: Optional[float] = None
    cash_due: Optional[float] = None
    note: Optional[str] = None
    recovery_for: Optional[List[dict]] = None  # [{bill_id, amount}]: the old cash inside this invoice is for those invoices


class PartIn(BaseModel):
    bill_id: int
    part: str = "wire"
    amount: float


class PaymentIn(BaseModel):
    kind: str = "wire"
    amount: float
    paid_date: Optional[str] = None
    reference: Optional[str] = None
    note: Optional[str] = None
    came_with_bill_id: Optional[int] = None
    for_bill_ids: Optional[List[int]] = None
    parts: Optional[List[PartIn]] = None


class SuggestIn(BaseModel):
    kind: str = "wire"
    amount: float
    for_bill_ids: Optional[List[int]] = None
    came_with_bill_id: Optional[int] = None
    payment_id: Optional[int] = None


def _set(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


async def _read(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > MAX_SHEET:
        raise HTTPException(status_code=413, detail="That file is too big for a statement (15 MB at most)")
    if not data:
        raise HTTPException(status_code=400, detail="The file is empty")
    return data


@router.get("/")
def list_accounts(db: Session = Depends(get_db)):
    from app.models import VacctAccount
    for acc in db.query(VacctAccount).all():  # payments recorded on the PO screens since: into their accounts
        VA.pickup(db, acc)
    return VA.list_accounts(db)


@router.get("/by-vendor")
def by_vendor(db: Session = Depends(get_db)):
    """{vendor_code: account id} -- the PO screens send this vendor's payments to its account."""
    from app.models import VacctAccount
    return {a.vendor_code: a.id for a in db.query(VacctAccount).all()}


@router.post("/")
def create_account(data: AccountIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    acc = VA.create_account(db, data.vendor_id, data.cash_share, user.username)
    return VA.view(db, acc.id)


@router.get("/{account_id}")
def get_account(account_id: int, db: Session = Depends(get_db)):
    VA.pickup(db, VA._account(db, account_id))
    return VA.view(db, account_id)


@router.put("/{account_id}")
def edit_account(account_id: int, data: AccountEdit, db: Session = Depends(get_db)):
    VA.update_account(db, account_id, _set(data))
    return VA.view(db, account_id)


@router.delete("/{account_id}", status_code=204)
def delete_account(account_id: int, db: Session = Depends(get_db)):
    VA.delete_account(db, account_id)
    return Response(status_code=204)


@router.post("/{account_id}/bills")
def add_bill(account_id: int, data: BillIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    VA.save_bill(db, account_id, _set(data), user.username)
    return VA.view(db, account_id)


@router.put("/{account_id}/bills/{bill_id}")
def edit_bill(account_id: int, bill_id: int, data: BillIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    VA.save_bill(db, account_id, _set(data), user.username, bill_id)
    return VA.view(db, account_id)


@router.delete("/{account_id}/bills/{bill_id}")
def delete_bill(account_id: int, bill_id: int, db: Session = Depends(get_db)):
    VA.delete_bill(db, account_id, bill_id)
    return VA.view(db, account_id)


@router.post("/{account_id}/suggest")
def suggest(account_id: int, data: SuggestIn, db: Session = Depends(get_db)):
    return VA.suggest(db, account_id, data.kind, data.amount, data.for_bill_ids, data.came_with_bill_id, data.payment_id)


@router.post("/{account_id}/payments")
def add_payment(account_id: int, data: PaymentIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    d = _set(data)
    if data.parts is not None:
        d["parts"] = [p.model_dump() for p in data.parts]
    VA.save_payment(db, account_id, d, user.username)
    return VA.view(db, account_id)


@router.put("/{account_id}/payments/{payment_id}")
def edit_payment(account_id: int, payment_id: int, data: PaymentIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    d = _set(data)
    if data.parts is not None:
        d["parts"] = [p.model_dump() for p in data.parts]
    VA.save_payment(db, account_id, d, user.username, payment_id)
    return VA.view(db, account_id)


@router.delete("/{account_id}/payments/{payment_id}")
def delete_payment(account_id: int, payment_id: int, db: Session = Depends(get_db)):
    VA.delete_payment(db, account_id, payment_id)
    return VA.view(db, account_id)


class NoteIn(BaseModel):
    target: str
    target_id: int
    text: str


@router.get("/{account_id}/notes")
def notes(account_id: int, target: str, target_id: int, db: Session = Depends(get_db)):
    return VA.list_notes(db, account_id, target, target_id)


@router.post("/{account_id}/notes")
def add_note(account_id: int, data: NoteIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    return VA.add_note(db, account_id, data.target, data.target_id, data.text, user.username)


@router.delete("/{account_id}/notes/{note_id}", status_code=204)
def delete_note(account_id: int, note_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    VA.delete_note(db, account_id, note_id, user)
    return Response(status_code=204)


@router.post("/{account_id}/sheet/preview")
async def sheet_preview(account_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    return VA.plan_sheet(db, account_id, await _read(file), file.filename or "sheet.xlsx")


@router.post("/{account_id}/sheet/apply")
async def sheet_apply(account_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
                      user: User = Depends(get_current_active_user)):
    from app.services import backups
    try:  # it replaces the account's history: keep a copy of the database first
        backups.backup_now("before-vendor-sheet")
    except Exception:
        pass
    res = VA.apply_sheet(db, account_id, await _read(file), file.filename or "sheet.xlsx", user.username)
    return {**res, "view": VA.view(db, account_id)}


@router.post("/{account_id}/compare")
async def compare(account_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    return VA.compare(db, account_id, await _read(file), file.filename or "statement.xlsx")


def _name(db, account_id) -> str:
    v = VA.view(db, account_id)["account"]
    return f"Statement - {v['vendor_name'] or v['vendor_code']} - {datetime.utcnow():%Y-%m-%d}"


@router.get("/{account_id}/statement.xlsx")
def statement_xlsx(account_id: int, open_only: bool = False, db: Session = Depends(get_db)):
    from app.services.crud import get_company_profile
    data = VA.statement_xlsx(db, account_id, get_company_profile(db).name, open_only)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": disposition(_name(db, account_id) + ".xlsx", inline=False)})


@router.get("/{account_id}/statement.pdf")
def statement_pdf(account_id: int, open_only: bool = False, db: Session = Depends(get_db)):
    return Response(VA.statement_pdf(db, account_id, open_only), media_type="application/pdf",
                    headers={"Content-Disposition": disposition(_name(db, account_id) + ".pdf")})
