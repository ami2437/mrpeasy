from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import PodEmailRequest, ShipmentEmailResponse
from app.schemas import ShipmentResponse, SetBoxesRequest, SetPalletWeightsRequest, ShipmentUpdate, PickRequest, UnbookRequest, MarkDeliveredRequest
from app.services.crud import ShipmentService
from app.dependencies import get_current_active_user, require_role
from app.models import User
from app.services.pdf import packing_list_pdf

router = APIRouter(prefix="/api/shipments", tags=["shipments"], dependencies=[Depends(get_current_active_user)])


def with_pods(db: Session, shipments):
    """Attach each shipment's proof-of-delivery files (for the POD column / email)."""
    from app.models import Attachment
    one = not isinstance(shipments, list)
    items = [shipments] if one else shipments
    by_id = {}
    for att in (db.query(Attachment).filter(Attachment.entity_type == "shipment", Attachment.category == "pod",
                                            Attachment.entity_id.in_([s.id for s in items]))
                .order_by(Attachment.created_at).all()):
        by_id.setdefault(att.entity_id, []).append(att)
    from app.models import Invoice, InvoiceShipment
    live = {}
    from sqlalchemy.orm import selectinload
    for link, inv in (db.query(InvoiceShipment, Invoice).join(Invoice, Invoice.id == InvoiceShipment.invoice_id)
                      .options(selectinload(Invoice.shipments))
                      .filter(InvoiceShipment.shipment_id.in_([s.id for s in items]), Invoice.status != "void").all()):
        live[link.shipment_id] = inv
    for s in items:
        s.pods = by_id.get(s.id, [])
        inv = live.get(s.id)
        s.invoice_id, s.invoice_code, s.invoice_status = (inv.id, inv.code, inv.status) if inv else (None, None, None)
        s.invoice_combined = bool(inv) and len(inv.shipments) > 1
        s.invoice_shipment_codes = inv.shipment_codes if inv else []
        s.invoice_combined_from = inv.combined_from if inv else []
    return shipments


@router.get("/", response_model=list[ShipmentResponse])
def list_shipments(db: Session = Depends(get_db)):
    return with_pods(db, ShipmentService.list(db))


@router.get("/unpacked/list", response_model=list[ShipmentResponse])
def list_unpacked(db: Session = Depends(get_db)):
    """Shipments still needing a packing list/labels -- feeds the batch packing screen."""
    return ShipmentService.unpacked(db)


@router.get("/packing-lists.pdf")
def packing_lists(ids: str, boxes: bool = True, pallets: bool = False, lots: bool = False, db: Session = Depends(get_db)):
    """Several packing lists in one PDF (batch screen): ?ids=3,7,9."""
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    for raw in ids.split(","):
        if raw.strip().isdigit():
            shipment = ShipmentService.get(db, int(raw))
            pdf = packing_list_pdf(db, shipment, include_boxes=boxes, include_pallets=pallets, include_lots=lots)
            for page in PdfReader(BytesIO(pdf)).pages:
                writer.add_page(page)
    out = BytesIO()
    writer.write(out)
    return Response(out.getvalue(), media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="Packing-Lists.pdf"'})


@router.get("/{shipment_id}", response_model=ShipmentResponse)
def get_shipment(shipment_id: int, db: Session = Depends(get_db)):
    return with_pods(db, ShipmentService.get(db, shipment_id))


@router.get("/{shipment_id}/pod-emails", response_model=list[ShipmentEmailResponse])
def pod_emails(shipment_id: int, db: Session = Depends(get_db)):
    from app.models import ShipmentEmail
    return db.query(ShipmentEmail).filter(ShipmentEmail.shipment_id == shipment_id).order_by(ShipmentEmail.sent_at.desc()).all()


@router.post("/{shipment_id}/email-pod", response_model=ShipmentEmailResponse)
def email_pod(shipment_id: int, data: PodEmailRequest, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_active_user)):
    """Email the proof-of-delivery files to the customer (e.g. when they say it never arrived)."""
    from app.models import Attachment
    from app.routes.attachments import upload_root
    from app.services import email as email_service
    shipment = ShipmentService.get(db, shipment_id)
    query = db.query(Attachment).filter(Attachment.entity_type == "shipment", Attachment.entity_id == shipment.id)
    query = query.filter(Attachment.id.in_(data.attachment_ids)) if data.attachment_ids else query.filter(Attachment.category == "pod")
    files = []
    for att in query.all():
        path = (upload_root() / att.stored_name).resolve()
        if path.exists():
            files.append((path.read_bytes(), att.filename, att.content_type or "application/octet-stream"))
    return email_service.send_pods(db, shipment, files, data.to, data.cc, data.subject, data.body, current_user.username)


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
def update_shipment(shipment_id: int, data: ShipmentUpdate, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_active_user)):
    if current_user.role == "employee":
        data.shipping_cost = ShipmentService.get(db, shipment_id).shipping_cost  # employees don't see or set costs
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
