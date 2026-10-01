from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import TestDataResult
from app.services.test_data import ensure_test_data
from app.dependencies import get_current_active_user

router = APIRouter(prefix="/api/test-data", tags=["test-data"], dependencies=[Depends(get_current_active_user)])


@router.post("/ensure", response_model=TestDataResult)
def ensure(db: Session = Depends(get_db)):
    """Create the TEST-* products/customer/vendor if missing, top up their stock, and make
    sure an untouched test order exists (a new one is created if the last was used)."""
    return ensure_test_data(db)
