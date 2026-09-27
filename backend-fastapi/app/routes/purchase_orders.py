"""
Purchase Orders module.

MRPeasy's REST API (both v1 and the newer v2) has no create/update endpoint
for purchase orders or vendors -- only GET. Verified against the full path
list of both API versions; every other resource (customer orders, invoices,
items, BOMs, etc.) has POST/PUT, these two do not. So this module reads
purchase orders live from MRPeasy (no local cache of MRP's own data) and
layers local tracking -- payments and notes/delivery-notes -- on top, since
MRPeasy has no real equivalent for either (its `bills` field on a purchase
order is just three free-text-ish fields, not payment tracking).

Also includes a beta AI PDF parser (Claude) for reconciling a vendor PDF
against an existing MRP-sourced purchase order. Extraction only -- it does
not create or modify anything anywhere.
"""
import base64
import json
from datetime import datetime
from typing import Dict, Optional

import anthropic
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.config.settings import settings
from app.dependencies import require_module
from app.models import User, PurchaseOrderPayment, PurchaseOrderNote
from app.services.mrpeasy_client import mrpeasy_client

router = APIRouter(
    prefix="/api/purchase-orders",
    tags=["purchase_orders"],
    dependencies=[Depends(require_module("purchase_orders"))]
)


class RecordPaymentRequest(BaseModel):
    amount: float
    currency: Optional[str] = None
    paid_date: Optional[str] = None
    method: Optional[str] = None
    reference: Optional[str] = None
    note: Optional[str] = None


class AddNoteRequest(BaseModel):
    note_type: str = "general"  # 'general' | 'delivery'
    note_text: Optional[str] = None
    received_quantity: Optional[float] = None
    carrier: Optional[str] = None
    tracking_number: Optional[str] = None
    delivery_date: Optional[str] = None


def _serialize_payment(payment: PurchaseOrderPayment) -> Dict:
    return {
        "id": payment.id,
        "pur_ord_id": payment.pur_ord_id,
        "po_code": payment.po_code,
        "amount": payment.amount,
        "currency": payment.currency,
        "paid_date": payment.paid_date,
        "method": payment.method,
        "reference": payment.reference,
        "note": payment.note,
        "recorded_by": payment.recorded_by,
        "created_at": payment.created_at.isoformat() if payment.created_at else None,
    }


def _serialize_note(note: PurchaseOrderNote) -> Dict:
    return {
        "id": note.id,
        "pur_ord_id": note.pur_ord_id,
        "po_code": note.po_code,
        "note_type": note.note_type,
        "note_text": note.note_text,
        "received_quantity": note.received_quantity,
        "carrier": note.carrier,
        "tracking_number": note.tracking_number,
        "delivery_date": note.delivery_date,
        "created_by": note.created_by,
        "created_at": note.created_at.isoformat() if note.created_at else None,
    }


def _lookup_po_code(pur_ord_id: int) -> Optional[str]:
    """Best-effort PO code lookup for display; local tracking still works if MRP is briefly unreachable."""
    try:
        order = mrpeasy_client.get_purchase_order(pur_ord_id)
        return order.get("code") if order else None
    except Exception:
        return None


@router.get("/")
def list_purchase_orders(
    vendor_id: Optional[int] = None,
    vendor_code: Optional[str] = None,
    status: Optional[int] = None,
    code: Optional[str] = None,
):
    """Live list of purchase orders from MRPeasy -- no local cache."""
    filters = {}
    if vendor_id is not None:
        filters["vendor_id"] = vendor_id
    if vendor_code:
        filters["vendor_code"] = vendor_code
    if status is not None:
        filters["status"] = status
    if code:
        filters["code"] = code
    try:
        orders = mrpeasy_client.get_purchase_orders(filters) or []
        return {"success": True, "purchase_orders": orders, "count": len(orders)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch purchase orders: {exc}")


@router.get("/{pur_ord_id}")
def get_purchase_order_detail(pur_ord_id: int):
    """Live detail for one purchase order from MRPeasy."""
    try:
        order = mrpeasy_client.get_purchase_order(pur_ord_id)
        if not order:
            raise HTTPException(status_code=404, detail="Purchase order not found")
        return order
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch purchase order: {exc}")


@router.get("/{pur_ord_id}/tracking")
def get_purchase_order_tracking(pur_ord_id: int, db: Session = Depends(get_db)):
    """Local payments + notes recorded against this purchase order."""
    payments = (
        db.query(PurchaseOrderPayment)
        .filter(PurchaseOrderPayment.pur_ord_id == pur_ord_id)
        .order_by(PurchaseOrderPayment.created_at.desc())
        .all()
    )
    notes = (
        db.query(PurchaseOrderNote)
        .filter(PurchaseOrderNote.pur_ord_id == pur_ord_id)
        .order_by(PurchaseOrderNote.created_at.desc())
        .all()
    )

    return {
        "success": True,
        "payments": [_serialize_payment(p) for p in payments],
        "notes": [_serialize_note(n) for n in notes],
        "total_paid": sum(p.amount for p in payments),
    }


@router.post("/{pur_ord_id}/payments")
def record_purchase_order_payment(
    pur_ord_id: int,
    request: RecordPaymentRequest,
    current_user: User = Depends(require_module("purchase_orders")),
    db: Session = Depends(get_db)
):
    if request.amount <= 0:
        raise HTTPException(status_code=400, detail="Payment amount must be greater than 0")

    payment = PurchaseOrderPayment(
        pur_ord_id=pur_ord_id,
        po_code=_lookup_po_code(pur_ord_id),
        amount=request.amount,
        currency=request.currency,
        paid_date=request.paid_date,
        method=request.method,
        reference=request.reference,
        note=request.note,
        recorded_by=current_user.username,
        created_at=datetime.utcnow(),
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    return {"success": True, "payment": _serialize_payment(payment)}


@router.post("/{pur_ord_id}/notes")
def add_purchase_order_note(
    pur_ord_id: int,
    request: AddNoteRequest,
    current_user: User = Depends(require_module("purchase_orders")),
    db: Session = Depends(get_db)
):
    if request.note_type not in ("general", "delivery"):
        raise HTTPException(status_code=400, detail="note_type must be 'general' or 'delivery'")
    if not (request.note_text or "").strip():
        raise HTTPException(status_code=400, detail="Note text is required")

    note = PurchaseOrderNote(
        pur_ord_id=pur_ord_id,
        po_code=_lookup_po_code(pur_ord_id),
        note_type=request.note_type,
        note_text=request.note_text,
        received_quantity=request.received_quantity,
        carrier=request.carrier,
        tracking_number=request.tracking_number,
        delivery_date=request.delivery_date,
        created_by=current_user.username,
        created_at=datetime.utcnow(),
    )
    db.add(note)
    db.commit()
    db.refresh(note)

    return {"success": True, "note": _serialize_note(note)}


# ---------------------------------------------------------------------------
# Beta: AI PDF parser (Claude). Extraction only -- nothing is persisted or
# sent to MRPeasy. A sandbox for iterating on vendor-PDF reconciliation
# ahead of eventually wiring it into the tracking flow above.
# ---------------------------------------------------------------------------

PDF_EXTRACTION_PROMPT = """You are extracting structured data from a vendor purchase-order-related PDF (this could be a vendor quote, order confirmation, packing slip, or invoice).

Return ONLY a JSON object (no other text, no markdown code fence) with this exact shape:
{
  "vendor_name": string or null,
  "document_type": one of "quote", "order_confirmation", "packing_slip", "invoice", "other",
  "document_number": string or null,
  "document_date": string ("YYYY-MM-DD") or null,
  "line_items": [
    {
      "description": string,
      "item_code": string or null,
      "quantity": number or null,
      "unit_price": number or null,
      "total_price": number or null
    }
  ],
  "total_amount": number or null,
  "currency": string or null
}

If a field isn't present in the document, use null. Do not invent values. Extract every line item you can find."""


@router.post("/beta/parse-pdf")
async def beta_parse_purchase_order_pdf(file: UploadFile = File(...)):
    """
    Beta: send an uploaded vendor PDF to Claude for structured extraction,
    for reconciling against an existing MRP-sourced purchase order.
    Extraction only -- does not create or modify anything in MRPeasy or our
    own database (MRPeasy's API has no create/update endpoint for purchase
    orders in either API version, so this was never going to auto-create
    anything there).
    """
    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=503,
            detail="AI PDF parsing is not configured -- set ANTHROPIC_API_KEY to enable this beta feature."
        )

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    encoded_pdf = base64.standard_b64encode(contents).decode("utf-8")

    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": encoded_pdf,
                            },
                        },
                        {"type": "text", "text": PDF_EXTRACTION_PROMPT},
                    ],
                }
            ],
        )
        raw_text = "".join(block.text for block in response.content if block.type == "text")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Claude API request failed: {exc}")

    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        extracted = json.loads(cleaned)
    except Exception:
        raise HTTPException(
            status_code=502,
            detail=f"Claude returned a response that could not be parsed as JSON: {raw_text[:500]}"
        )

    return {"success": True, "extracted": extracted, "filename": file.filename}
