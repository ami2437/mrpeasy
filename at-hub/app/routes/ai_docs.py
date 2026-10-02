from typing import Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.dependencies import get_current_active_user, ROLE_RANK
from app.models import User
from app.services import ai_docs

router = APIRouter(prefix="/api/ai-docs", tags=["ai-docs"])

MAX_BYTES = 15 * 1024 * 1024


@router.post("/extract")
async def extract(kind: str = Form(...), po_id: Optional[int] = Form(None), engine: str = Form("local"), file: UploadFile = File(...),
                  db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """Scan a vendor invoice, vendor quote/confirmation or a proof of delivery with the local
    model and return suggestions to review. Nothing is saved."""
    if engine == "claude" and ROLE_RANK.get(user.role, 0) < ROLE_RANK["manager"]:
        raise HTTPException(status_code=403, detail="Ask Claude (cloud) needs the manager role or higher")
    if kind != "pod" and ROLE_RANK.get(user.role, 0) < ROLE_RANK["manager"]:
        raise HTTPException(status_code=403, detail="Scanning vendor documents needs the manager role or higher")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="File is larger than 15 MB")
    return ai_docs.extract(db, kind, data, file.filename or "", po_id, engine="claude" if engine == "claude" else "local")
