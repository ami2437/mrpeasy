from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import TestDataResult
from app.services.test_data import ensure_generic_test_data, ensure_test_data
from app.dependencies import get_current_active_user, require_perm

router = APIRouter(prefix="/api/test-data", tags=["test-data"], dependencies=[Depends(require_perm("company"))])


@router.post("/ensure", response_model=TestDataResult)
def ensure(db: Session = Depends(get_db)):
    """Create the TEST-* products/customer/vendor if missing, top up their stock, and make
    sure an untouched test order exists (a new one is created if the last was used)."""
    return ensure_test_data(db)


@router.post("/generic-nuts")
def generic_nuts(db: Session = Depends(get_db)):
    """Generic stock test set: TEST-GEN-916-NUT received on a PO, two 9/16 nuts that draw from it, and a draft order."""
    return ensure_generic_test_data(db)


@router.post("/pack-orders")
def pack_orders(n: int = 10, lines: int = 15, db: Session = Depends(get_db)):
    """Pack-size test set: n confirmed TEST orders (two TEST customers, real items with packing history, ~lines each),
    each with a shipment booked and waiting in Bulk Operations -> To pick."""
    from app.services.test_data import make_pack_test_orders
    return make_pack_test_orders(db, max(1, min(n, 30)), max(1, min(lines, 40)))
