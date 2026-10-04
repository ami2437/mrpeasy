import base64
import re
from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, File
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import CompanyProfileResponse, CompanyProfileUpdate
from app.services.crud import get_company_profile
from app.dependencies import get_current_active_user, require_role

router = APIRouter(prefix="/api/company", tags=["company"])

MAX_LOGO_BYTES = 1024 * 1024
LOGO_TYPES = {"image/png", "image/jpeg"}


@router.get("/", response_model=CompanyProfileResponse, dependencies=[Depends(get_current_active_user)])
def get_company(db: Session = Depends(get_db)):
    return get_company_profile(db)


@router.put("/", response_model=CompanyProfileResponse, dependencies=[Depends(require_role("admin"))])
def update_company(data: CompanyProfileUpdate, db: Session = Depends(get_db)):
    profile = get_company_profile(db)
    for key, value in data.dict(exclude_unset=True).items():
        if key == "name" and not (value or "").strip():
            continue  # name is required on printed invoices -- ignore a blank
        setattr(profile, key, value)
    db.commit()
    db.refresh(profile)
    return profile


def logo_bytes(profile):
    """(bytes, media type) of the stored logo, or (None, None)."""
    m = re.match(r"data:(image/[\w+.-]+);base64,(.*)$", profile.logo_data or "", re.S)
    if not m:
        return None, None
    return base64.b64decode(m.group(2)), m.group(1)


@router.get("/logo")
def get_logo(db: Session = Depends(get_db)):
    """Public on purpose: <img> tags (labels, sidebar, login page) can't send the auth header."""
    data, media_type = logo_bytes(get_company_profile(db))
    if not data:
        raise HTTPException(status_code=404, detail="No logo uploaded")
    return Response(data, media_type=media_type, headers={"Cache-Control": "no-cache"})


@router.post("/logo", response_model=CompanyProfileResponse, dependencies=[Depends(require_role("admin"))])
def upload_logo(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if file.content_type not in LOGO_TYPES:
        raise HTTPException(status_code=400, detail="Logo must be a PNG or JPEG image")
    data = file.file.read()
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(status_code=400, detail="Logo must be 1 MB or smaller")
    profile = get_company_profile(db)
    profile.logo_data = f"data:{file.content_type};base64,{base64.b64encode(data).decode()}"
    db.commit()
    db.refresh(profile)
    return profile


@router.delete("/logo", response_model=CompanyProfileResponse, dependencies=[Depends(require_role("admin"))])
def delete_logo(db: Session = Depends(get_db)):
    profile = get_company_profile(db)
    profile.logo_data = None
    db.commit()
    db.refresh(profile)
    return profile
