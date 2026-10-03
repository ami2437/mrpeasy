from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.dependencies import require_role
from app.services import ai_orders

router = APIRouter(prefix="/api/ai-orders", tags=["ai-orders"], dependencies=[Depends(require_role("manager"))])  # drafting priced orders is manager work

MAX_PDF_BYTES = 15 * 1024 * 1024


@router.get("/status")
def ai_status():
    """Whether the private (local) AI model is available."""
    return ai_orders.status()


@router.post("/extract")
async def extract(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Read a customer PO PDF with the local model and return a draft order to review.
    Nothing is saved -- the order is created only when the user confirms the draft."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Upload a PDF")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=400, detail="PDF is larger than 15 MB")
    return ai_orders.extract_order(db, data)


@router.post("/validate/{order_id}")
def validate(order_id: int, claude: bool = True, db: Session = Depends(get_db)):
    """Check a saved order against its attached customer PO with every reader we have (exact layout reader,
    local AI, and -- when asked -- Claude on redacted text). Reports differences; changes nothing."""
    from app.services import ai_validate
    return ai_validate.validate_order(db, order_id, use_claude=claude)


@router.get("/missing-nuts/{order_id}")
def missing_nuts(order_id: int, db: Session = Depends(get_db)):
    """Bolt lines on a saved order without their $0 nut line, with the nut to add (existing or new)."""
    return ai_orders.missing_nuts(db, order_id)
