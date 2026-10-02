import json
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.dependencies import get_current_active_user, require_role
from app.models import User
from app.schemas import FundingImportResponse
from app.services import funding

router = APIRouter(prefix="/api/invoice-funding", tags=["invoice-funding"], dependencies=[Depends(require_role("manager"))])  # no dollar work for employees


async def _rows(file: UploadFile) -> list:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    return funding.read_rows(data, file.filename)


@router.post("/preview")
async def preview(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Check a factor's funding report against our invoices without changing anything."""
    return {"file_name": file.filename, **funding.preview(db, await _rows(file))}


@router.post("/apply", dependencies=[Depends(require_role("manager"))])
async def apply(file: UploadFile = File(...), record_payments: bool = Form(True), db: Session = Depends(get_db),
                current_user: User = Depends(get_current_active_user)):
    """Set disbursement date / funding amount / discount on each matched invoice and, if
    record_payments, record funding + discount as payments. Existing values are never overwritten."""
    return {"file_name": file.filename, **funding.apply(db, file.filename, await _rows(file), record_payments, current_user.username)}


@router.get("/imports", response_model=list[FundingImportResponse])
def imports(db: Session = Depends(get_db)):
    return [_import_out(b) for b in funding.history(db)]


@router.post("/imports/{import_id}/rollback", response_model=FundingImportResponse, dependencies=[Depends(require_role("manager"))])
def rollback(import_id: int, db: Session = Depends(get_db)):
    return _import_out(funding.rollback(db, import_id))


def _import_out(batch) -> dict:
    return {**{c: getattr(batch, c) for c in ("id", "file_name", "record_payments", "updated_count", "skipped_count",
                                               "rolled_back", "created_by", "created_at")},
            "summary": json.loads(batch.summary) if batch.summary else None}
