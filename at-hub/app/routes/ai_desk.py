"""AI Desk: drop files (any kind: PDF, photo, Excel, CSV, Word, email), and each gets one obvious next step.

Files are kept on the server (uploads/desk/) until someone acts on them or removes them -- the desk is an inbox, so
nothing dropped is lost. Each is read once (services/desk.py: names first, then the AI read for its kind); the result
is kept and every action uses it. Actions make drafts in Validation (checked later on the record) or attach the file.

    GET    /files               the inbox (still to do + done in the last 3 days); ?mine=1 only mine
    POST   /files               upload (many); quick "who is it from" straight away, no AI yet
    POST   /files/{id}/read     the AI read (form: kind to force a type, instruction)
    POST   /files/{id}/act      do one action ({action, party_id, record_type, record_id, bill, allow_duplicate})
    DELETE /files/{id}          remove it from the desk
    GET    /files/{id}/raw | /thumb | /preview     the file, a page-1 picture, a table / text preview
    GET    /pick-lists          light lists for the pickers (customers, vendors, open orders / POs, recent shipments)
"""
import json
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_perm
from app.models import Customer, CustomerOrder, DeskFile, PurchaseOrder, Shipment, User, Vendor
from app.routes.attachments import ALLOWED_EXTENSIONS, MAX_BYTES, thumb_path, upload_root
from app.services import desk, doc_text

router = APIRouter(prefix="/api/ai-desk", tags=["ai-desk"], dependencies=[Depends(require_perm("ai"))])

CLASSIFY = """You sort business documents for AMERICAN TRADERS / ATIND SUPPLIES, a fastener distributor.
Answer JSON {"kind": one of "customer_po" (a customer ordering FROM us: their purchase order to us),
"rfq" (a customer asking US for a price / quote), "vendor_invoice" (a supplier billing US),
"vendor_order" (a supplier's sales order, quote, proforma or order confirmation to us -- we are the buyer),
"mtr" (mill / material test report, certificate), "pod" (signed bill of lading or delivery receipt), "other"}.
A document from a supplier that says "Sales Order" is a vendor_order: they sell, we buy.
Document:
---
"""
THUMB_WIDTH = 240


def _dir():
    d = upload_root() / "desk"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(row: DeskFile):
    p = (upload_root() / row.stored_name).resolve()
    if upload_root().resolve() not in p.parents or not p.exists():
        raise HTTPException(status_code=404, detail="The file is missing from the server")
    return p


def _get(db: Session, fid: int) -> DeskFile:
    row = db.get(DeskFile, fid)
    if not row:
        raise HTTPException(status_code=404, detail="That file isn't on the desk any more")
    return row


def _out(db: Session, user: User, row: DeskFile) -> dict:
    o = {"id": row.id, "filename": row.filename, "size": row.size, "status": row.status, "error": row.error,
         "family": doc_text.family(row.filename), "uploaded_by": row.uploaded_by, "created_at": row.created_at,
         "record_type": row.record_type, "record_id": row.record_id, "record_code": row.record_code, "done_label": row.done_label,
         "done_by": row.done_by, "done_at": row.done_at, "plan": None, "quick": None}
    res = json.loads(row.result or "{}")
    if row.status in ("read", "error") and res.get("kind"):
        o["plan"] = desk.plan(db, user, row)
    elif res.get("ident"):  # uploaded, not read yet: who it seems to be from, already
        i = res["ident"]
        p = i.get("vendor") if i.get("side") == "vendor" else i.get("customer") if i.get("side") == "customer" else None
        o["quick"] = {"side": i.get("side"), "party": p, "kind": i.get("kind"), "kind_short": desk.SHORT.get(i.get("kind") or "", "")}
    return o


@router.get("/files")
def list_files(mine: bool = False, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    since = datetime.utcnow() - timedelta(days=3)
    q = db.query(DeskFile).filter((DeskFile.status != "done") | (DeskFile.done_at >= since))
    if mine:
        q = q.filter(DeskFile.uploaded_by == user.username)
    return [_out(db, user, r) for r in q.order_by(DeskFile.id.desc()).limit(300).all()]


@router.post("/files")
def upload(files: List[UploadFile] = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    from pathlib import Path
    rows = []
    for f in files:
        name = Path(f.filename or "document").name
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"{name}: this kind of file can't be kept ({', '.join(sorted(ALLOWED_EXTENSIONS))})")
        data = f.file.read()
        if not data or len(data) > MAX_BYTES:
            raise HTTPException(status_code=400, detail=f"{name}: empty or larger than 25 MB")
        stored = f"desk/{uuid.uuid4().hex[:12]}{ext}"
        (_dir() / stored.split("/", 1)[1]).write_bytes(data)
        row = DeskFile(filename=name, stored_name=stored, content_type=f.content_type, size=len(data), status="new", uploaded_by=user.username)
        try:  # who it's from, from the words alone -- shown at once, before the AI read
            row.result = json.dumps({"ident": desk.identify(db, doc_text.text_of(data, name), name)}, default=str)
        except Exception:
            row.result = None
        db.add(row)
        db.flush()
        rows.append(row)
    db.commit()
    return [_out(db, user, r) for r in rows]


@router.post("/files/{fid}/read")
def read_file(fid: int, kind: str = Form(""), instruction: str = Form(""), db: Session = Depends(get_db),
              user: User = Depends(get_current_active_user)):
    row = _get(db, fid)
    if row.status == "done":
        raise HTTPException(status_code=400, detail=f"Already done: {row.done_label}")
    data = _path(row).read_bytes()
    row.status = "reading"
    db.commit()
    try:
        res = desk.read(db, row, data, instruction, kind)
        row.result, row.kind, row.status, row.error = json.dumps(res, default=str), res.get("kind"), "read", None
    except Exception as e:  # never lose the file: it can still be acted on by hand
        row.status, row.error = "error", f"Couldn't read it: {getattr(e, 'detail', None) or e}"
        row.result = json.dumps({"kind": kind or "other", "how": "", "ident": json.loads(row.result or "{}").get("ident") or {}, "draft": None,
                                 "read_error": row.error}, default=str)
    db.commit()
    return _out(db, user, row)


class ActIn(BaseModel):
    action: str
    party_id: Optional[int] = None
    record_type: Optional[str] = None
    record_id: Optional[int] = None
    bill: Optional[dict] = None
    allow_duplicate: bool = False


@router.post("/files/{fid}/act")
def act(fid: int, a: ActIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    row = _get(db, fid)
    if row.status == "done":
        raise HTTPException(status_code=400, detail=f"Already done: {row.done_label}")
    if not row.result or not json.loads(row.result).get("kind"):  # not read yet (or no AI): act on what's known
        row.result = json.dumps({**json.loads(row.result or "{}"), "kind": "other", "how": "", "draft": None}, default=str)
    extra = desk.act(db, user, row, _path(row).read_bytes(), a.dict())
    return {**_out(db, user, row), **extra}


@router.delete("/files/{fid}", status_code=204)
def remove(fid: int, db: Session = Depends(get_db)):
    row = _get(db, fid)
    try:
        (upload_root() / row.stored_name).unlink(missing_ok=True)
        thumb_path(row.stored_name).unlink(missing_ok=True)
    except OSError:
        pass
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.get("/files/{fid}/raw")
def raw(fid: int, db: Session = Depends(get_db)):
    row = _get(db, fid)
    from app.services.filenames import disposition
    return FileResponse(_path(row), media_type=row.content_type or "application/octet-stream",
                        headers={"Content-Disposition": disposition(row.filename)})


@router.get("/files/{fid}/thumb")
def thumb(fid: int, db: Session = Depends(get_db)):
    row = _get(db, fid)
    path = _path(row)
    cache = thumb_path(row.stored_name)
    if not cache.exists():
        fam = doc_text.family(row.filename)
        try:
            from PIL import Image
            if fam == "pdf":
                import pypdfium2 as pdfium
                pdf = pdfium.PdfDocument(str(path))
                page = pdf[0]
                img = page.render(scale=THUMB_WIDTH / max(page.get_width(), 1)).to_pil()
                pdf.close()
            elif fam == "image":
                try:
                    import pillow_heif
                    pillow_heif.register_heif_opener()
                except Exception:
                    pass
                img = Image.open(path)
                img.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 2))
            else:
                raise HTTPException(status_code=404, detail="No picture for this kind of file")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=404, detail="Couldn't make a picture of this file")
        cache.parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(cache, "PNG", optimize=True)
    return FileResponse(cache, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/files/{fid}/preview")
def preview(fid: int, db: Session = Depends(get_db)):
    row = _get(db, fid)
    p = doc_text.preview(_path(row).read_bytes(), row.filename)
    if not p:
        raise HTTPException(status_code=404, detail="No preview for this kind of file")
    return p


@router.get("/pick-lists")
def pick_lists(db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """Small lists for the desk's pickers (names and numbers only -- no money)."""
    since = datetime.utcnow() - timedelta(days=120)
    cust = {c.id: c.name for c in db.query(Customer).filter(Customer.is_active == True).order_by(Customer.name).all()}  # noqa: E712
    vend = {v.id: v.name for v in db.query(Vendor).filter(Vendor.is_active == True).order_by(Vendor.name).all()}  # noqa: E712
    orders = db.query(CustomerOrder).filter(CustomerOrder.status != "cancelled",
                                            (CustomerOrder.status.in_(("validation", "draft", "confirmed"))) | (CustomerOrder.created_at >= since)) \
        .order_by(CustomerOrder.id.desc()).limit(500).all()
    pos = db.query(PurchaseOrder).filter(PurchaseOrder.status != "cancelled",
                                         (PurchaseOrder.status.notin_(("received",))) | (PurchaseOrder.created_at >= since)) \
        .order_by(PurchaseOrder.id.desc()).limit(500).all()
    ships = db.query(Shipment).filter(Shipment.status != "cancelled").order_by(Shipment.id.desc()).limit(300).all()
    order_code = {o.id: o for o in db.query(CustomerOrder).filter(CustomerOrder.id.in_({s.order_id for s in ships if s.order_id})).all()}
    return {
        "customers": [{"id": k, "name": v} for k, v in cust.items()],
        "vendors": [{"id": k, "name": v} for k, v in vend.items()],
        "orders": [{"id": o.id, "code": o.code, "party_id": o.customer_id, "party": cust.get(o.customer_id, ""), "ref": o.po_number or "",
                    "status": o.status} for o in orders],
        "pos": [{"id": p.id, "code": p.code, "party_id": p.vendor_id, "party": vend.get(p.vendor_id, ""), "ref": p.vendor_so_number or "",
                 "status": p.status} for p in pos],
        "shipments": [{"id": s.id, "code": s.code, "party": cust.get(order_code[s.order_id].customer_id, "") if s.order_id in order_code else "",
                       "ref": order_code[s.order_id].code if s.order_id in order_code else "", "status": s.status} for s in ships],
    }
