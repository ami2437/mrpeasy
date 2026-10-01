"""Files attached to orders, purchase orders and shipments: customer PO PDFs, vendor invoices,
material test reports (MTRs), proof-of-delivery photos. Stored on disk under settings.upload_dir;
the database keeps the metadata, so the files can later be fed to automated parsing."""
import mimetypes
import os
import re
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.config.settings import settings
from app.dependencies import ROLE_RANK, get_current_active_user
from app.models import Attachment, CustomerOrder, PurchaseOrder, Shipment, User
from app.schemas import AttachmentResponse
from app.services.crud import ShipmentService

router = APIRouter(prefix="/api/attachments", tags=["attachments"])

ENTITIES = {"customer_order": CustomerOrder, "purchase_order": PurchaseOrder, "shipment": Shipment}
# Which kinds of document belong on which record.
CATEGORIES = {
    "customer_order": {"customer_po", "other"},
    "purchase_order": {"vendor_invoice", "mtr", "vendor_quote", "other"},
    "shipment": {"pod", "bol", "other"},
}
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif",
                      ".xlsx", ".xls", ".csv", ".doc", ".docx", ".txt", ".eml", ".msg"}
MAX_BYTES = 25 * 1024 * 1024


def upload_root() -> Path:
    root = Path(settings.upload_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _check_entity(db: Session, entity_type: str, entity_id: int) -> None:
    model = ENTITIES.get(entity_type)
    if not model:
        raise HTTPException(status_code=400, detail=f"Unknown record type '{entity_type}'")
    if not db.query(model).filter(model.id == entity_id).first():
        raise HTTPException(status_code=404, detail="That record no longer exists")


def _get(db: Session, attachment_id: int) -> Attachment:
    att = db.query(Attachment).filter(Attachment.id == attachment_id).first()
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return att


@router.get("/", response_model=List[AttachmentResponse])
def list_attachments(entity_type: str = Query(...), entity_id: int = Query(...), db: Session = Depends(get_db),
                     _: User = Depends(get_current_active_user)):
    return (db.query(Attachment)
            .filter(Attachment.entity_type == entity_type, Attachment.entity_id == entity_id)
            .order_by(Attachment.created_at.desc()).all())


@router.post("/", response_model=List[AttachmentResponse])
async def upload(entity_type: str = Form(...), entity_id: int = Form(...), category: str = Form("other"),
                 note: Optional[str] = Form(None), files: List[UploadFile] = File(...),
                 also_entity_ids: Optional[str] = Form(None),
                 db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """also_entity_ids: comma-separated ids of more records of the same type that get the same
    files (a driver's photos covering several deliveries). A proof of delivery on a shipped
    shipment marks it delivered."""
    entity_ids = [entity_id] + [int(x) for x in (also_entity_ids or "").split(",") if x.strip().isdigit()]
    entity_ids = list(dict.fromkeys(entity_ids))
    for eid in entity_ids:
        _check_entity(db, entity_type, eid)
    if category not in CATEGORIES[entity_type]:
        raise HTTPException(status_code=400, detail=f"'{category}' isn't a valid document type here")
    if not files:
        raise HTTPException(status_code=400, detail="Choose at least one file")

    shipments = []
    if entity_type == "shipment" and category == "pod":
        shipments = [db.query(Shipment).filter(Shipment.id == eid).first() for eid in entity_ids]
        not_shipped = [s.code for s in shipments if s.status not in ShipmentService.SHIPPED_STATUSES]
        if not_shipped:
            raise HTTPException(status_code=400, detail=f"Not shipped yet, so no proof of delivery: {', '.join(not_shipped)}")

    blobs = []
    for f in files:
        name = os.path.basename(f.filename or "file")
        ext = os.path.splitext(name)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"{name}: file type {ext or '(none)'} isn't allowed")
        data = await f.read()
        if len(data) > MAX_BYTES:
            raise HTTPException(status_code=400, detail=f"{name} is larger than 25 MB")
        if not data:
            raise HTTPException(status_code=400, detail=f"{name} is empty")
        blobs.append((name, f.content_type or mimetypes.guess_type(name)[0], data))

    saved = []
    for eid in entity_ids:
        folder = upload_root() / entity_type / str(eid)
        folder.mkdir(parents=True, exist_ok=True)
        for name, content_type, data in blobs:
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[-80:]
            stored = f"{uuid.uuid4().hex}_{safe}"
            (folder / stored).write_bytes(data)
            att = Attachment(
                entity_type=entity_type, entity_id=eid, category=category,
                filename=name, stored_name=f"{entity_type}/{eid}/{stored}",
                content_type=content_type, size=len(data),
                note=(note or "").strip() or None, uploaded_by=user.username,
            )
            db.add(att)
            saved.append(att)
    for shipment in shipments:
        if not shipment.delivered_at:
            ShipmentService.mark_delivered(db, shipment, None, user.username, commit=False)
    db.commit()
    for att in saved:
        db.refresh(att)
    return saved


@router.get("/{attachment_id}/file")
def download(attachment_id: int, download: bool = False, db: Session = Depends(get_db),
             _: User = Depends(get_current_active_user)):
    att = _get(db, attachment_id)
    path = (upload_root() / att.stored_name).resolve()
    if upload_root() not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="The file is missing from the server")
    return FileResponse(path, media_type=att.content_type or "application/octet-stream", filename=att.filename,
                        content_disposition_type="attachment" if download else "inline")


@router.delete("/{attachment_id}", status_code=204)
def delete(attachment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """Uploaders can remove their own files; managers and above can remove any."""
    att = _get(db, attachment_id)
    if att.uploaded_by != user.username and ROLE_RANK.get(user.role, 0) < ROLE_RANK["manager"]:
        raise HTTPException(status_code=403, detail="Only the uploader or a manager can delete this file")
    path = (upload_root() / att.stored_name).resolve()
    if upload_root() in path.parents and path.exists():
        path.unlink()
    db.delete(att)
    db.commit()
    return Response(status_code=204)
