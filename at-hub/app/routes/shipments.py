from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import ShipmentResponse, SetBoxesRequest
from app.services.crud import ShipmentService
from app.dependencies import get_current_active_user

router = APIRouter(prefix="/api/shipments", tags=["shipments"], dependencies=[Depends(get_current_active_user)])


@router.get("/", response_model=list[ShipmentResponse])
def list_shipments(db: Session = Depends(get_db)):
    return ShipmentService.list(db)


@router.get("/{shipment_id}", response_model=ShipmentResponse)
def get_shipment(shipment_id: int, db: Session = Depends(get_db)):
    return ShipmentService.get(db, shipment_id)


@router.put("/{shipment_id}/boxes", response_model=ShipmentResponse)
def set_boxes(shipment_id: int, data: SetBoxesRequest, db: Session = Depends(get_db)):
    """Set the packing-list/box breakdown for this shipment, used to print box labels."""
    return ShipmentService.set_boxes(db, shipment_id, data)
