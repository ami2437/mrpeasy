from typing import Optional
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import PodEmailRequest, ShipmentEmailResponse
from app.schemas import ShipmentResponse, SetBoxesRequest, SetPalletWeightsRequest, ShipmentUpdate, PickRequest, UnbookRequest, MarkDeliveredRequest, UnshipRequest
from app.services.crud import ShipmentService
from app.dependencies import get_current_active_user, require_any, require_perm
from app.services.permissions import has
from app.models import User
from app.services.pdf import packing_list_pdf

router = APIRouter(prefix="/api/shipments", tags=["shipments"], dependencies=[Depends(require_any("shipments.view", "shipments.work", "orders.view", "invoices", "pod.upload"))])


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
def packing_lists(ids: str, boxes: bool = True, pallets: bool = False, lots: bool = False, notes: bool = True, pallet_boxes: bool = False,
                  split: bool = False, db: Session = Depends(get_db)):
    """Several packing lists (batch screen): ?ids=3,7,9 -- one PDF, or with ?split=true a ZIP of one PDF each."""
    from app.services.bulk_docs import merge_pdfs, zip_files
    files = []
    for raw in ids.split(","):
        if raw.strip().isdigit():
            shipment = ShipmentService.get(db, int(raw))
            files.append((packing_list_pdf(db, shipment, include_boxes=boxes, include_pallets=pallets, include_lots=lots, show_notes=notes,
                                           include_pallet_boxes=pallet_boxes), f"Packing-List-{shipment.code}.pdf", "application/pdf"))
    if split:
        return Response(zip_files(files), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="Packing-Lists.zip"'})
    return Response(merge_pdfs(files), media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="Packing-Lists.pdf"'})


@router.get("/pack-suggestions")
def pack_suggestions(ids: str, db: Session = Depends(get_db)):
    """The pack size each line of these shipments pre-fills, and why: {shipment_id: {order_line_id: {size, source, label}}}."""
    from app.services import pack_sizes
    return pack_sizes.suggestions_for_shipments(db, [int(x) for x in ids.split(",") if x.strip().isdigit()])


@router.get("/{shipment_id}", response_model=ShipmentResponse)
def get_shipment(shipment_id: int, db: Session = Depends(get_db)):
    return with_pods(db, ShipmentService.get(db, shipment_id))


@router.get("/{shipment_id}/pod-emails", response_model=list[ShipmentEmailResponse])
def pod_emails(shipment_id: int, db: Session = Depends(get_db)):
    from app.models import ShipmentEmail
    return db.query(ShipmentEmail).filter(ShipmentEmail.shipment_id == shipment_id).order_by(ShipmentEmail.sent_at.desc()).all()


@router.post("/{shipment_id}/email-pod", response_model=ShipmentEmailResponse, dependencies=[Depends(require_perm("shipments.work"))])
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
def packing_list(shipment_id: int, boxes: bool = True, pallets: bool = False, lots: bool = False, notes: bool = True, pallet_boxes: bool = False,
                 db: Session = Depends(get_db)):
    """?boxes= / ?pallets= / ?lots= choose whether box breakdown, pallet info and lot #s print on the list."""
    shipment = ShipmentService.get(db, shipment_id)
    return Response(packing_list_pdf(db, shipment, include_boxes=boxes, include_pallets=pallets, include_lots=lots, show_notes=notes,
                                     include_pallet_boxes=pallet_boxes), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="Packing-List-{shipment.code}.pdf"'})


@router.put("/{shipment_id}/boxes", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.work"))])
def set_boxes(shipment_id: int, data: SetBoxesRequest, db: Session = Depends(get_db)):
    """Set the packing-list/box breakdown for this shipment, used to print box labels."""
    return ShipmentService.set_boxes(db, shipment_id, data)


@router.put("/{shipment_id}/pallet-weights", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.work"))])
def set_pallet_weights(shipment_id: int, data: SetPalletWeightsRequest, db: Session = Depends(get_db)):
    return ShipmentService.set_pallet_weights(db, shipment_id, data)


@router.put("/{shipment_id}", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.work"))])
def update_shipment(shipment_id: int, data: ShipmentUpdate, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_active_user)):
    if not has(current_user, "money.view"):
        data.shipping_cost = ShipmentService.get(db, shipment_id).shipping_cost  # employees don't see or set costs
    return ShipmentService.update(db, shipment_id, data)


@router.post("/{shipment_id}/confirm-booking", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.work"))])
def confirm_booking(shipment_id: int, db: Session = Depends(get_db)):
    return ShipmentService.confirm_booking(db, shipment_id)


@router.post("/{shipment_id}/pick", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.work"))])
def pick(shipment_id: int, data: PickRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Record picked quantities; the shipment ships automatically once every line is fully picked."""
    return ShipmentService.pick(db, shipment_id, data, created_by=current_user.username)


@router.post("/{shipment_id}/accept-packing", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.work"))])
def accept_packing(shipment_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Packing reviewed: Ship is allowed once everything is picked. Unsaved packing is made from pack sizes."""
    return with_pods(db, ShipmentService.accept_packing(db, shipment_id, current_user.username))


@router.post("/{shipment_id}/unconfirm-booking", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.work"))])
def unconfirm_booking(shipment_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Undo Confirm Bookings (ready -> new) while nothing is picked; stock stays booked."""
    return with_pods(db, ShipmentService.unconfirm_booking(db, shipment_id, current_user.username))


@router.post("/{shipment_id}/unpick", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.work"))])
def unpick(shipment_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Set every line back to 0 picked on a shipment that hasn't shipped (bookings and packing stay)."""
    return with_pods(db, ShipmentService.unpick(db, shipment_id, current_user.username))


@router.post("/{shipment_id}/unpack", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.work"))])
def unpack(shipment_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Clear the boxes, pallets and accepted packing of a shipment that hasn't shipped (picking stays)."""
    return with_pods(db, ShipmentService.unpack(db, shipment_id, current_user.username))


@router.post("/{shipment_id}/ship", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.work"))])
def ship(shipment_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Everything picked and packing accepted: stock leaves on-hand and the shipment is shipped."""
    return with_pods(db, ShipmentService.ship(db, shipment_id, current_user.username))


@router.post("/{shipment_id}/unbook", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.work"))])
def unbook(shipment_id: int, data: UnbookRequest, db: Session = Depends(get_db)):
    """Release booked, unpicked quantity back to stock (all or part of a line)."""
    return ShipmentService.unbook(db, shipment_id, data)


@router.post("/{shipment_id}/cancel", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.work"))])
def cancel_shipment(shipment_id: int, db: Session = Depends(get_db)):
    return ShipmentService.cancel(db, shipment_id)


@router.get("/{shipment_id}/undo-plan", dependencies=[Depends(require_perm("shipments.undo"))])
def undo_plan(shipment_id: int, db: Session = Depends(get_db)):
    """What undoing this shipment involves (its invoice and the steps a sent one needs)."""
    return ShipmentService.undo_plan(db, shipment_id)


@router.post("/{shipment_id}/unship", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.undo"))])
def unship(shipment_id: int, data: Optional[UnshipRequest] = None, db: Session = Depends(get_db),
           current_user: User = Depends(get_current_active_user)):
    """Undo a sent shipment: stock returns and stays booked so it can be edited or cancelled; its invoice is voided."""
    return ShipmentService.unship(db, shipment_id, created_by=current_user.username, data=data)


@router.delete("/{shipment_id}", status_code=204, dependencies=[Depends(require_perm("shipments.undo"))])
def delete_shipment(shipment_id: int, db: Session = Depends(get_db)):
    ShipmentService.delete(db, shipment_id)
    return Response(status_code=204)


@router.post("/{shipment_id}/delivered", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.deliver"))])
def mark_delivered(shipment_id: int, data: MarkDeliveredRequest, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_active_user)):
    """Mark delivered by hand (managers and up). Uploading a POD does this automatically."""
    shipment = ShipmentService.get(db, shipment_id)
    return ShipmentService.mark_delivered(db, shipment, data.delivered_at, current_user.username)


@router.post("/{shipment_id}/undeliver", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.undo"))])
def clear_delivered(shipment_id: int, db: Session = Depends(get_db)):
    return ShipmentService.clear_delivered(db, shipment_id)
