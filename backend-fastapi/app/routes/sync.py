from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.services.sync_service import SyncService
from app.models import User
from app.dependencies import get_current_active_user, require_permission

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/customer-orders")
def sync_customer_orders(
    current_user: User = Depends(require_permission("sync")),
    db: Session = Depends(get_db)
):
    """
    Sync customer orders FROM MRPeasy API to local database (READ-ONLY).
    This only fetches data from MRPeasy, never sends or modifies anything.
    Requires: sync permission (admin, editor only)
    """
    result = SyncService.sync_customer_orders(db)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))
    return result


@router.post("/stock-items")
def sync_stock_items(
    current_user: User = Depends(require_permission("sync")),
    db: Session = Depends(get_db)
):
    """
    Sync stock items FROM MRPeasy API to local database (READ-ONLY).
    This only fetches data from MRPeasy, never sends or modifies anything.
    Requires: sync permission (admin, editor only)
    """
    result = SyncService.sync_stock_items(db)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))
    return result


@router.post("/manufacturing-orders")
def sync_manufacturing_orders(
    current_user: User = Depends(require_permission("sync")),
    db: Session = Depends(get_db)
):
    """
    Sync manufacturing orders FROM MRPeasy API to local database (READ-ONLY).
    This only fetches data from MRPeasy, never sends or modifies anything.
    Requires: sync permission (admin, editor only)
    """
    result = SyncService.sync_manufacturing_orders(db)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))
    return result


@router.post("/all")
def sync_all(
    current_user: User = Depends(require_permission("sync")),
    db: Session = Depends(get_db)
):
    """
    Sync all data FROM MRPeasy API to local database (READ-ONLY).
    This only fetches data from MRPeasy, never sends or modifies anything.
    Requires: sync permission (admin, editor only)
    """
    results = {}
    results["customer_orders"] = SyncService.sync_customer_orders(db)
    results["stock_items"] = SyncService.sync_stock_items(db)
    results["manufacturing_orders"] = SyncService.sync_manufacturing_orders(db)
    return results
