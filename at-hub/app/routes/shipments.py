from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import ShipmentResponse, SetBoxesRequest, SetPalletWeightsRequest, ShipmentUpdate, PickRequest, UnbookRequest, MarkDeliveredRequest
from app.services.crud import ShipmentService
from app.dependencies import get_current_active_user, require_role
from app.models import User
from app.services.pdf import packing_list_pdf

router = APIRouter(prefix="/api/shipments", tags=["shipments"], dependencies=[Depends(get_current_active_user)])


@router.get("/", response_model=list[ShipmentResponse])
def list_shipments(db: Session = Depends(get_db)):
    return ShipmentService.list(db)


@router.get("/unpacked/list", response_model=list[ShipmentResponse])
def list_unpacked(db: Session = Depends(get_db)):
    """Shipments still needing a packing list/labels -- feeds the batch packing screen."""
    return ShipmentService.unpacked(db)


@router.get("/{shipment_id}", response_model=ShipmentResponse)
def get_shipment(shipment_id: int, db: Session = Depends(get_db)):
    return ShipmentService.get(db, shipment_id)


@router.get("/{shipment_id}/packing-list.pdf")
def packing_list(shipment_id: int, boxes: bool = True, pallets: bool = False, lots: bool = False, db: Session = Depends(get_db)):
    """?boxes= / ?pallets= / ?lots= choose whether box breakdown, pallet info and lot #s print on the list."""
    shipment = ShipmentService.get(db, shipment_id)
    return Response(packing_list_pdf(db, shipment, include_boxes=boxes, include_pallets=pallets, include_lots=lots), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="Packing-List-{shipment.code}.pdf"'})


@router.put("/{shipment_id}/boxes", response_model=ShipmentResponse)
def set_boxes(shipment_id: int, data: SetBoxesRequest, db: Session = Depends(get_db)):
    """Set the packing-list/box breakdown for this shipment, used to print box labels."""
    return ShipmentService.set_boxes(db, shipment_id, data)


@router.put("/{shipment_id}/pallet-weights", response_model=ShipmentResponse)
def set_pallet_weights(shipment_id: int, data: SetPalletWeightsRequest, db: Session = Depends(get_db)):
    return ShipmentService.set_pallet_weights(db, shipment_id, data)


@router.put("/{shipment_id}", response_model=ShipmentResponse)
def update_shipment(shipment_id: int, data: ShipmentUpdate, db: Session = Depends(get_db)):
    return ShipmentService.update(db, shipment_id, data)


@router.post("/{shipment_id}/confirm-booking", response_model=ShipmentResponse)
def confirm_booking(shipment_id: int, db: Session = Depends(get_db)):
    return ShipmentService.confirm_booking(db, shipment_id)


@router.post("/{shipment_id}/pick", response_model=ShipmentResponse)
def pick(shipment_id: int, data: PickRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Record picked quantities; the shipment ships automatically once every line is fully picked."""
    return ShipmentService.pick(db, shipment_id, data, created_by=current_user.username)


@router.post("/{shipment_id}/unbook", response_model=ShipmentResponse)
def unbook(shipment_id: int, data: UnbookRequest, db: Session = Depends(get_db)):
    """Release booked, unpicked quantity back to stock (all or part of a line)."""
    return ShipmentService.unbook(db, shipment_id, data)


@router.post("/{shipment_id}/cancel", response_model=ShipmentResponse)
def cancel_shipment(shipment_id: int, db: Session = Depends(get_db)):
    return ShipmentService.cancel(db, shipment_id)


@router.post("/{shipment_id}/unship", response_model=ShipmentResponse, dependencies=[Depends(require_role("manager"))])
def unship(shipment_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Undo a sent shipment: stock returns and stays booked so it can be edited or cancelled."""
    return ShipmentService.unship(db, shipment_id, created_by=current_user.username)


@router.delete("/{shipment_id}", status_code=204, dependencies=[Depends(require_role("manager"))])
def delete_shipment(shipment_id: int, db: Session = Depends(get_db)):
    ShipmentService.delete(db, shipment_id)
    return Response(status_code=204)


@router.post("/{shipment_id}/delivered", response_model=ShipmentResponse, dependencies=[Depends(require_role("manager"))])
def mark_delivered(shipment_id: int, data: MarkDeliveredRequest, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_active_user)):
    """Mark delivered by hand (managers and up). Uploading a POD does this automatically."""
    shipment = ShipmentService.get(db, shipment_id)
    return ShipmentService.mark_delivered(db, shipment, data.delivered_at, current_user.username)


@router.post("/{shipment_id}/undeliver", response_model=ShipmentResponse, dependencies=[Depends(require_role("manager"))])
def clear_delivered(shipment_id: int, db: Session = Depends(get_db)):
    return ShipmentService.clear_delivered(db, shipment_id)
