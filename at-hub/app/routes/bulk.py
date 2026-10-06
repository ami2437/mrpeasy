"""Bulk Operations: the same action on many records at once (app/services/bulk_docs.py)."""
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_any
from app.models import User
from app.services import bulk_docs
from app.services.permissions import has

router = APIRouter(prefix="/api/bulk", tags=["bulk"], dependencies=[Depends(require_any("shipments.work", "invoices"))])


class DocsIn(BaseModel):
    shipment_ids: List[int] = []
    invoice_ids: List[int] = []
    kinds: List[str] = ["packing_list"]
    group_by: str = "order"   # shipment | order | customer
    attach: str = "separate"  # separate files | combined (one merged PDF per email)


class SendIn(DocsIn):
    edits: Dict[str, dict] = {}  # group key -> {to, cc, subject, body, skip}


def _check(user, data: DocsIn):
    if (data.invoice_ids or "invoice" in data.kinds) and not has(user, "invoices"):
        raise HTTPException(status_code=403, detail="Your role doesn't include invoices")
    if set(data.kinds) - {"invoice"} and not has(user, "shipments.work"):
        raise HTTPException(status_code=403, detail="Your role doesn't include shipping documents")


@router.post("/documents/plan")
def plan(data: DocsIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """The emails Send would make -- recipient, subject, message, attachments, warnings. Nothing is sent."""
    _check(user, data)
    groups = bulk_docs.plan(db, data.shipment_ids, data.invoice_ids, data.kinds, data.group_by, has(user, "invoices"), data.attach)
    from app.services import email as email_service
    return {"groups": groups, "email_ready": email_service.is_configured()}


@router.post("/documents/send")
def send(data: SendIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    _check(user, data)
    groups = bulk_docs.plan(db, data.shipment_ids, data.invoice_ids, data.kinds, data.group_by, has(user, "invoices"), data.attach)
    return {"results": bulk_docs.send(db, groups, data.edits, user.username)}


from app.services.filenames import disposition  # noqa: E402


@router.get("/documents.pdf")
def documents_pdf(shipment_ids: str = "", invoice_ids: str = "", kinds: str = Query("packing_list"), split: bool = False,
                  db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """Every chosen document of every chosen record in one PDF to print -- or with ?split=true a ZIP of one file each."""
    ids = lambda s: [int(x) for x in s.split(",") if x.strip().isdigit()]
    data = DocsIn(shipment_ids=ids(shipment_ids), invoice_ids=ids(invoice_ids), kinds=[k for k in kinds.split(",") if k])
    _check(user, data)
    if split:
        return Response(bulk_docs.split_zip(db, data.shipment_ids, data.invoice_ids, data.kinds, has(user, "invoices")),
                        media_type="application/zip", headers={"Content-Disposition": disposition("Documents.zip", inline=False)})
    pdf = bulk_docs.merged_pdf(db, data.shipment_ids, data.invoice_ids, data.kinds, has(user, "invoices"))
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": disposition(bulk_docs.single_name(
        db, data.shipment_ids, data.invoice_ids, data.kinds) or "Documents.pdf")})
