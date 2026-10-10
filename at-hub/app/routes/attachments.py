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
from app.dependencies import get_current_active_user
from app.services.permissions import has
from app.models import Attachment, CustomerOrder, MtrLink, PurchaseOrder, Shipment, User
from app.schemas import AttachmentResponse
from app.services import type_lists
from app.services.crud import ShipmentService

router = APIRouter(prefix="/api/attachments", tags=["attachments"])

from app.models import Quote  # noqa: E402
ENTITIES = {"customer_order": CustomerOrder, "purchase_order": PurchaseOrder, "shipment": Shipment, "quote": Quote}
# Which kinds of document belong on which record: Company Settings -> Types & Tags (app/services/type_lists.py).
def _categories(db: Session, entity_type: str) -> set:
    return type_lists.keys(db, "attachment", entity_type)
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif",
                      ".xlsx", ".xls", ".csv", ".doc", ".docx", ".txt", ".eml", ".msg"}
MAX_BYTES = 25 * 1024 * 1024
# Documents that carry prices (types marked "has prices"); employees never list, open or upload them.
def _money(db: Session) -> set:
    return type_lists.money_keys(db)
THUMB_WIDTH = 160


def _hides_money(user: User) -> bool:
    return not has(user, "money.view")


def thumb_path(stored_name: str) -> Path:
    """The page-1 picture of a stored file, named after the FILE (not the row id: ids are renumbered by a fresh
    import, and a picture cached under an old id would show on a different file)."""
    import hashlib
    return upload_root() / ".thumbs" / f"{hashlib.sha1(stored_name.encode()).hexdigest()[:20]}.png"


def upload_root() -> Path:
    root = Path(settings.upload_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def store_file(db: Session, entity_type: str, entity_id: int, category: str, name: str, content_type: Optional[str],
               data: bytes, note: Optional[str], username: str) -> Attachment:
    """Write one file under uploads/<type>/<id>/ and add its Attachment row (caller commits). Big photos and
    scanned PDFs are shrunk first (app/services/shrink.py) -- a .heic photo is stored as .jpg."""
    from app.services.shrink import shrink
    name, content_type, data = shrink(name, content_type, data)
    folder = upload_root() / entity_type / str(entity_id)
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[-80:]
    stored = f"{uuid.uuid4().hex}_{safe}"
    (folder / stored).write_bytes(data)
    att = Attachment(entity_type=entity_type, entity_id=entity_id, category=category, filename=name,
                     stored_name=f"{entity_type}/{entity_id}/{stored}", content_type=content_type or mimetypes.guess_type(name)[0],
                     size=len(data), note=(note or "").strip() or None, uploaded_by=username)
    db.add(att)
    return att


def read_uploads(files) -> list:
    """[(name, content type, bytes)] -- refuses file types we don't keep, empty files and anything over 25 MB."""
    if not files:
        raise HTTPException(status_code=400, detail="Choose at least one file")
    blobs = []
    for f in files:
        name = os.path.basename(f.filename or "file")
        ext = os.path.splitext(name)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"{name}: file type {ext or '(none)'} isn't allowed")
        data = f.file.read()
        if len(data) > MAX_BYTES:
            raise HTTPException(status_code=400, detail=f"{name} is larger than 25 MB")
        if not data:
            raise HTTPException(status_code=400, detail=f"{name} is empty")
        blobs.append((name, f.content_type or mimetypes.guess_type(name)[0], data))
    return blobs


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
                     user: User = Depends(get_current_active_user)):
    q = db.query(Attachment).filter(Attachment.entity_type == entity_type, Attachment.entity_id == entity_id)
    if _hides_money(user):
        q = q.filter(Attachment.category.notin_(_money(db)))
    return q.order_by(Attachment.created_at.desc()).all()


@router.get("/counts")
def attachment_counts(entity_type: str = Query(...), db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """{record id: number of files} for one record type -- the list pages' paperclips."""
    from sqlalchemy import func
    q = db.query(Attachment.entity_id, func.count(Attachment.id)).filter(Attachment.entity_type == entity_type)
    if _hides_money(user):
        q = q.filter(Attachment.category.notin_(_money(db)))
    return {str(eid): n for eid, n in q.group_by(Attachment.entity_id).all()}


@router.post("/", response_model=List[AttachmentResponse])
def upload(entity_type: str = Form(...), entity_id: int = Form(...), category: str = Form("other"),
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
    if category not in _categories(db, entity_type):
        raise HTTPException(status_code=400, detail=f"'{category}' isn't a valid document type here")
    if category in _money(db) and _hides_money(user):
        raise HTTPException(status_code=403, detail="This document type needs the manager role")
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
        data = f.file.read()
        if len(data) > MAX_BYTES:
            raise HTTPException(status_code=400, detail=f"{name} is larger than 25 MB")
        if not data:
            raise HTTPException(status_code=400, detail=f"{name} is empty")
        blobs.append((name, f.content_type or mimetypes.guess_type(name)[0], data))

    saved = [store_file(db, entity_type, eid, category, name, content_type, data, note, user.username)
             for eid in entity_ids for name, content_type, data in blobs]
    for shipment in shipments:
        if not shipment.delivered_at:
            ShipmentService.mark_delivered(db, shipment, None, user.username, commit=False)
    db.commit()
    for att in saved:
        db.refresh(att)
    return saved


@router.get("/{attachment_id}/file")
def download(attachment_id: int, download: bool = False, db: Session = Depends(get_db),
             user: User = Depends(get_current_active_user)):
    att = _get(db, attachment_id)
    if att.category in _money(db) and _hides_money(user):
        raise HTTPException(status_code=403, detail="This document needs the manager role")
    path = (upload_root() / att.stored_name).resolve()
    if upload_root() not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="The file is missing from the server")
    return FileResponse(path, media_type=att.content_type or "application/octet-stream", filename=att.filename,
                        content_disposition_type="attachment" if download else "inline")


@router.put("/{attachment_id}", response_model=AttachmentResponse)
def retag(attachment_id: int, category: Optional[str] = Form(None), note: Optional[str] = Form(None),
          db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """Change what kind of document a file is (its tag), or its note."""
    att = _get(db, attachment_id)
    if att.uploaded_by != user.username and not has(user, "money.view"):
        raise HTTPException(status_code=403, detail="Only the uploader or a manager can change this file")
    if category is not None:
        if category not in _categories(db, att.entity_type):
            raise HTTPException(status_code=400, detail=f"'{category}' isn't a kind of file for this record")
        if category in _money(db) and _hides_money(user):
            raise HTTPException(status_code=403, detail="That kind of document needs the manager role")
        att.category = category
    if note is not None:
        att.note = note.strip() or None
    db.commit()
    db.refresh(att)
    return att


@router.get("/{attachment_id}/thumb")
def thumbnail(attachment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """A small PNG of the file's first page (PDF) or the picture itself, made once and kept beside the uploads."""
    att = _get(db, attachment_id)
    if att.category in _money(db) and _hides_money(user):
        raise HTTPException(status_code=403, detail="This document needs the manager role")
    path = (upload_root() / att.stored_name).resolve()
    if upload_root() not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="The file is missing from the server")
    cache = thumb_path(att.stored_name)
    if not cache.exists():
        from PIL import Image
        try:
            if path.suffix.lower() == ".pdf":
                import pypdfium2 as pdfium
                pdf = pdfium.PdfDocument(str(path))
                page = pdf[0]
                img = page.render(scale=THUMB_WIDTH / max(page.get_width(), 1)).to_pil()
                pdf.close()
            elif (att.content_type or "").startswith("image/"):
                img = Image.open(path)
                img.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 2))
            else:
                raise HTTPException(status_code=404, detail="No preview for this kind of file")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=404, detail="Couldn't make a preview of this file")
        cache.parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(cache, "PNG", optimize=True)
    return FileResponse(cache, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/{attachment_id}/preview")
def preview(attachment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """A spreadsheet as a table, a Word / text / email file as text (services/doc_text.py) -- for the file viewer."""
    att = _get(db, attachment_id)
    if att.category in _money(db) and _hides_money(user):
        raise HTTPException(status_code=403, detail="This document needs the manager role")
    path = (upload_root() / att.stored_name).resolve()
    if upload_root() not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="The file is missing from the server")
    from app.services import doc_text
    p = doc_text.preview(path.read_bytes(), att.filename)
    if not p:
        raise HTTPException(status_code=404, detail="No preview for this kind of file")
    return p


@router.delete("/{attachment_id}", status_code=204)
def delete(attachment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """Uploaders can remove their own files; managers and above can remove any."""
    att = _get(db, attachment_id)
    if att.uploaded_by != user.username and not has(user, "money.view"):
        raise HTTPException(status_code=403, detail="Only the uploader or a manager can delete this file")
    from app.services.recycle_bin import move_to_trash
    move_to_trash(att.stored_name)  # kept until the recycle bin entry is emptied
    thumb_path(att.stored_name).unlink(missing_ok=True)
    for link in db.query(MtrLink).filter(MtrLink.attachment_id == att.id).all():
        db.delete(link)
    db.delete(att)
    db.commit()
    return Response(status_code=204)
