from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import VendorCreate, VendorUpdate, PartyResponse, VendorItemInput, VendorItemResponse
from app.services.crud import vendor_service, VendorItemService
from app.dependencies import require_role, get_current_active_user

router = APIRouter(prefix="/api/vendors", tags=["vendors"], dependencies=[Depends(require_role("manager"))])  # no dollar work for employees


@router.get("/", response_model=list[PartyResponse])
def list_vendors(db: Session = Depends(get_db)):
    return vendor_service.list(db)


@router.post("/", response_model=PartyResponse)
def create_vendor(data: VendorCreate, db: Session = Depends(get_db)):
    return vendor_service.create(db, data)


@router.get("/{party_id}", response_model=PartyResponse)
def get_vendor(party_id: int, db: Session = Depends(get_db)):
    return vendor_service.get(db, party_id)


@router.put("/{party_id}", response_model=PartyResponse)
def update_vendor(party_id: int, data: VendorUpdate, db: Session = Depends(get_db)):
    return vendor_service.update(db, party_id, data)


# ---- Vendor item cross-reference: their part # <-> our item ----
@router.get("/{party_id}/items", response_model=list[VendorItemResponse])
def list_vendor_items(party_id: int, db: Session = Depends(get_db)):
    vendor_service.get(db, party_id)
    return VendorItemService.list(db, vendor_id=party_id)


@router.get("/{party_id}/items/lookup", response_model=VendorItemResponse)
def lookup_vendor_item(party_id: int, code: str, db: Session = Depends(get_db)):
    """Which of our items this vendor's part # is -- used to auto-match while entering a PO."""
    mapping = VendorItemService.find(db, party_id, code)
    if not mapping:
        raise HTTPException(status_code=404, detail=f"No item is mapped to vendor item # {code}")
    return mapping


@router.post("/{party_id}/items", response_model=VendorItemResponse)
def add_vendor_item(party_id: int, data: VendorItemInput, db: Session = Depends(get_db)):
    vendor_service.get(db, party_id)
    mapping = VendorItemService.upsert(db, party_id, data.item_id, data.vendor_item_code, data.vendor_description)
    db.commit()
    db.refresh(mapping)
    return mapping


@router.delete("/{party_id}/items/{mapping_id}", status_code=204)
def delete_vendor_item(party_id: int, mapping_id: int, db: Session = Depends(get_db)):
    VendorItemService.delete(db, party_id, mapping_id)
    return Response(status_code=204)
