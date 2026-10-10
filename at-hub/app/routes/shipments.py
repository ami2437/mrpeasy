from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import PodEmailRequest, ShipmentEmailResponse, ShipmentComboInput
from app.schemas import ShipmentResponse, SetBoxesRequest, SetPalletWeightsRequest, ShipmentUpdate, PickRequest, UnbookRequest, MarkDeliveredRequest, UnshipRequest, DeliveryDatesRequest
from app.services.crud import ShipmentService
from app.dependencies import get_current_active_user, require_any, require_perm
from app.services.permissions import has
from app.models import User
from app.services.pdf import packing_list_pdf
from app.services import filenames

router = APIRouter(prefix="/api/shipments", tags=["shipments"], dependencies=[Depends(require_any("shipments.view", "shipments.work", "orders.view", "invoices"))])  # drivers: /api/pod only


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
def packing_lists(ids: str, boxes: Optional[bool] = None, pallets: Optional[bool] = None, lots: Optional[bool] = None,
                  notes: Optional[bool] = None, pallet_boxes: Optional[bool] = None,
                  split: bool = False, db: Session = Depends(get_db)):
    """Several packing lists (batch screen): ?ids=3,7,9 -- one PDF, or with ?split=true a ZIP of one PDF each."""
    from app.services.bulk_docs import merge_pdfs, zip_files
    files = []
    for raw in ids.split(","):
        if raw.strip().isdigit():
            shipment = ShipmentService.get(db, int(raw))
            files.append((packing_list_pdf(db, shipment, include_boxes=boxes, include_pallets=pallets, include_lots=lots, show_notes=notes,
                                           include_pallet_boxes=pallet_boxes), filenames.packing_list_name(db, shipment), "application/pdf"))
    if split:
        return Response(zip_files(files), media_type="application/zip",
                        headers={"Content-Disposition": filenames.disposition("Packing Lists.zip", inline=False)})
    return Response(merge_pdfs(files), media_type="application/pdf",
                    headers={"Content-Disposition": filenames.disposition(files[0][1] if len(files) == 1 else "Packing Lists.pdf")})


def _export(db, shipments, fmt: str, part: str, boxes: bool, pallets: bool, lots: bool, notes: bool, name: str):
    """Packing lists as Excel / CSV (app/services/packing_export.py)."""
    from app.services import packing_export
    if not shipments:
        raise HTTPException(status_code=400, detail="Pick at least one shipment")
    opts = {"boxes": boxes, "pallets": pallets, "lots": lots, "notes": notes}
    docs = packing_export.collect(db, shipments, opts)
    base = name[:-4] if name.endswith(".pdf") else name
    if fmt == "xlsx":
        return Response(packing_export.to_xlsx(docs, opts), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": filenames.disposition(f"{base}.xlsx", inline=False)})
    suffix = {"boxes": " Boxes", "pallets": " Pallets"}.get(part, "")
    return Response(packing_export.to_csv(docs, part), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": filenames.disposition(f"{base}{suffix}.csv", inline=False)})


@router.get("/packing-lists.{fmt}")
def packing_lists_export(fmt: str, ids: str, part: str = "lines", boxes: bool = True, pallets: bool = False, lots: bool = False,
                         notes: bool = True, db: Session = Depends(get_db)):
    """Several packing lists as one workbook (fmt=xlsx: Lines / Shipments / Boxes / Pallets sheets) or one CSV (part=lines|boxes|pallets)."""
    if fmt not in ("xlsx", "csv"):
        raise HTTPException(status_code=404, detail="Packing lists come as .pdf, .xlsx or .csv")
    ships = [ShipmentService.get(db, int(x)) for x in ids.split(",") if x.strip().isdigit()]
    name = filenames.packing_list_name(db, ships[0]) if len(ships) == 1 else "Packing Lists"
    return _export(db, ships, fmt, part, boxes, pallets, lots, notes, name)


def _pallet_label_spec(spec: dict, opts: dict) -> dict:
    """Print-time choices: no Boxes column; one pallet per label -> its items in bigger type."""
    if opts.get("only_own"):
        for blk in (spec.get("header") or {}).get("blocks") or []:
            if blk.get("type") == "table":
                st = blk.setdefault("style", {})
                st["size"] = max(float(st.get("size", 10.5)), 20)  # shrinks back down if the item list is long
                st["header_size"] = max(float(st.get("header_size", 7)), 8)
    if not opts.get("boxes_col", True):
        for blk in (spec.get("header") or {}).get("blocks") or []:
            if blk.get("type") == "table":
                blk["columns"] = [{**c, "hidden": True} if c.get("key") == "boxes" else c for c in blk.get("columns") or []]
    return spec


def _pallet_labels(db, ships, per_pallet: bool, template_id=None, opts=None):
    """Shipment pallet labels (4 x 6): the customer's / general default design, else the built-in Classic."""
    import json
    from app.models import CustomerOrder, DocTemplate
    from app.services import doc_context, template_engine, template_starters
    from app.services.templates import default_for
    out = b""
    from app.services.bulk_docs import merge_pdfs
    files = []
    for sh in ships:
        order = db.get(CustomerOrder, sh.order_id)
        t = db.get(DocTemplate, template_id) if template_id else default_for(db, "pallet_label", order.customer_id if order else None)
        spec = json.loads(t.spec) if t and t.doc_type == "pallet_label" else template_starters.classic_pallet_label()
        opts = opts or {}
        spec = _pallet_label_spec(spec, opts)
        totals = [k for k in ("pallets", "boxes", "weight") if opts.get(f"total_{k}", True)]
        footer = [k for k in ("company", "ship_date") if opts.get(f"show_{k}", True)]
        ctxs = doc_context.pallet_label_contexts(db, sh, per_pallet=per_pallet, totals=totals, footer=footer, only_own=opts.get("only_own", False),
                                                 show_of=opts.get("show_of", True))
        files.append((template_engine.render_labels(spec, ctxs), filenames.doc_name(sh.code, order.po_number if order else None, "Pallet Labels"), "application/pdf"))
    return merge_pdfs(files) if len(files) > 1 else files[0][0] if files else out


@router.get("/pallet-labels.pdf")
def pallet_labels_many(ids: str, per_pallet: bool = True, only_own: bool = False, show_of: bool = True, boxes_col: bool = True, total_pallets: bool = True, total_boxes: bool = True,
                       total_weight: bool = True, show_company: bool = True, show_ship_date: bool = True, db: Session = Depends(get_db)):
    """Pallet labels for several shipments in one PDF (?ids=3,7,9)."""
    ships = [ShipmentService.get(db, int(x)) for x in ids.split(",") if x.strip().isdigit()]
    if not ships:
        raise HTTPException(status_code=400, detail="Pick at least one shipment")
    name = filenames.doc_name(ships[0].code, None, "Pallet Labels") if len(ships) == 1 else "Pallet Labels.pdf"
    opts = {"boxes_col": boxes_col, "total_pallets": total_pallets, "total_boxes": total_boxes, "total_weight": total_weight,
            "show_company": show_company, "show_ship_date": show_ship_date, "only_own": only_own and per_pallet, "show_of": show_of}
    return Response(_pallet_labels(db, ships, per_pallet, opts=opts), media_type="application/pdf", headers={"Content-Disposition": filenames.disposition(name)})


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
    return email_service.send_pods(db, shipment, files, data.to, data.cc, data.subject, data.body, current_user.username, sender_id=data.from_id)


@router.get("/{shipment_id}/packing-list.pdf")
def packing_list(shipment_id: int, boxes: Optional[bool] = None, pallets: Optional[bool] = None, lots: Optional[bool] = None,
                 notes: Optional[bool] = None, pallet_boxes: Optional[bool] = None,
                 db: Session = Depends(get_db)):
    """?boxes= / ?pallets= / ?lots= choose whether box breakdown, pallet info and lot #s print on the list."""
    shipment = ShipmentService.get(db, shipment_id)
    return Response(packing_list_pdf(db, shipment, include_boxes=boxes, include_pallets=pallets, include_lots=lots, show_notes=notes,
                                     include_pallet_boxes=pallet_boxes), media_type="application/pdf",
                    headers={"Content-Disposition": filenames.disposition(filenames.packing_list_name(db, shipment))})


@router.get("/{shipment_id}/pallet-labels.pdf")
def pallet_labels(shipment_id: int, per_pallet: bool = True, only_own: bool = False, show_of: bool = True, template_id: Optional[int] = None, boxes_col: bool = True,
                  total_pallets: bool = True, total_boxes: bool = True, total_weight: bool = True, show_company: bool = True, show_ship_date: bool = True,
                  db: Session = Depends(get_db)):
    """Shipment pallet labels: per_pallet=true -> one per pallet (its row highlighted, "Pallet 3 of 5");
    false -> one summary label. Every label lists every pallet with its customer item #s, PO # and job #."""
    shipment = ShipmentService.get(db, shipment_id)
    if not any(b.pallet_number for b in shipment.boxes):
        raise HTTPException(status_code=400, detail=f"{shipment.code} has no pallets yet -- set pallet #s when packing (Process Shipment)")
    from app.models import CustomerOrder
    order = db.get(CustomerOrder, shipment.order_id)
    opts = {"boxes_col": boxes_col, "total_pallets": total_pallets, "total_boxes": total_boxes, "total_weight": total_weight,
            "show_company": show_company, "show_ship_date": show_ship_date, "only_own": only_own and per_pallet, "show_of": show_of}
    return Response(_pallet_labels(db, [shipment], per_pallet, template_id, opts=opts), media_type="application/pdf",
                    headers={"Content-Disposition": filenames.disposition(filenames.doc_name(shipment.code, order.po_number if order else None, "Pallet Labels"))})


@router.get("/{shipment_id}/packing-list.{fmt}")
def packing_list_export(shipment_id: int, fmt: str, part: str = "lines", boxes: bool = True, pallets: bool = False, lots: bool = False,
                        notes: bool = True, db: Session = Depends(get_db)):
    """One packing list as Excel (.xlsx) or CSV (part=lines|boxes|pallets)."""
    if fmt not in ("xlsx", "csv"):
        raise HTTPException(status_code=404, detail="Packing lists come as .pdf, .xlsx or .csv")
    shipment = ShipmentService.get(db, shipment_id)
    return _export(db, [shipment], fmt, part, boxes, pallets, lots, notes, filenames.packing_list_name(db, shipment))


class RenameIn(BaseModel):
    code: str


@router.put("/{shipment_id}/code", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.work"))])
def rename(shipment_id: int, data: RenameIn, db: Session = Depends(get_db)):
    """Change the shipment # (unique; printed on its documents from now on)."""
    return with_pods(db, ShipmentService.rename(db, shipment_id, data.code))


@router.put("/{shipment_id}/boxes", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.work"))])
def set_boxes(shipment_id: int, data: SetBoxesRequest, db: Session = Depends(get_db)):
    """Set the packing-list/box breakdown for this shipment, used to print box labels."""
    return ShipmentService.set_boxes(db, shipment_id, data)


@router.get("/{shipment_id}/combo-suggestions")
def combo_suggestions(shipment_id: int, db: Session = Depends(get_db)):
    """Bolt + $0 nut pairs booked on this shipment that could go out as assembled units (not combined yet)."""
    from app.services import nut_combos
    return nut_combos.suggestions(db, ShipmentService.get(db, shipment_id))


@router.post("/{shipment_id}/combos", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.work"))])
def combine_lines(shipment_id: int, data: ShipmentComboInput, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_active_user)):
    """Send a bolt line and a nut line together as assembled units (or change how many). The packing is re-checked after."""
    from app.services import nut_combos
    shipment = ShipmentService.get(db, shipment_id)
    nut_combos.combine(db, shipment, data, current_user.username)
    db.commit()
    db.refresh(shipment)
    return with_pods(db, shipment)


@router.delete("/{shipment_id}/combos/{combo_id}", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.work"))])
def split_lines(shipment_id: int, combo_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Bolts and nuts go out separately again (this shipment only)."""
    from app.services import nut_combos
    shipment = ShipmentService.get(db, shipment_id)
    nut_combos.uncombine(db, shipment, combo_id, current_user.username)
    db.commit()
    db.refresh(shipment)
    return with_pods(db, shipment)


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
    """Record picked quantities (added to what is picked). unbook_rest: then release whatever is still unpicked back to stock."""
    return ShipmentService.pick(db, shipment_id, data, created_by=current_user.username)


@router.post("/{shipment_id}/accept-packing", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.work"))])
def accept_packing(shipment_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Packing reviewed: Ship is allowed once everything is picked. Unsaved packing is made from pack sizes."""
    return with_pods(db, ShipmentService.accept_packing(db, shipment_id, current_user.username))


@router.post("/{shipment_id}/unbook-all", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.work"))])
def unbook_all(shipment_id: int, db: Session = Depends(get_db)):
    """Release every booked, unpicked unit back to stock (nothing picked -> the shipment is cancelled)."""
    return with_pods(db, ShipmentService.unbook_all(db, shipment_id))


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
    return ShipmentService.mark_delivered(db, shipment, data.delivered_at, current_user.username, tz_name=current_user.timezone)


@router.put("/{shipment_id}/delivery-dates", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.deliver"))])
def set_delivery_dates(shipment_id: int, data: DeliveryDatesRequest, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_active_user)):
    """The Delivery Date pop-up: the shipment's delivery date, and any line that arrived on a different day."""
    shipment = ShipmentService.get(db, shipment_id)
    return ShipmentService.set_delivery_dates(db, shipment, data, current_user.username, tz_name=current_user.timezone)


@router.post("/{shipment_id}/undeliver", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.undo"))])
def clear_delivered(shipment_id: int, db: Session = Depends(get_db)):
    return ShipmentService.clear_delivered(db, shipment_id)
