"""Cost Sheets (user, 2026-10-17): an UNOFFICIAL scratch sheet per PO -- the user's own extra costs (lunch for the
unloading crew, labor, forklift...) spread over the PO's items to see their "real" cost per unit, with tick boxes for what
goes into it (purchase / each official landed cost / the extras, and each extra row). Scratch only: lot costs, margins,
accounting and reports never see it. Money permission "cost_sheets"."""
import io
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_perm
from app.models import PurchaseOrder, StockItem, User, VacctCostSheet, Vendor
from app.services.filenames import disposition

router = APIRouter(prefix="/api/cost-sheets", tags=["cost-sheets"], dependencies=[Depends(require_perm("cost_sheets"))])


def _po(db: Session, po_id: int) -> PurchaseOrder:
    po = db.get(PurchaseOrder, po_id)
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")
    return po


def _sheet(db: Session, po: PurchaseOrder) -> Optional[VacctCostSheet]:
    return db.query(VacctCostSheet).filter(VacctCostSheet.po_code == po.code).first()


def _lines(db: Session, po: PurchaseOrder) -> list:
    out = []
    for l in po.lines:
        it = db.get(StockItem, l.item_id)
        out.append({"id": l.id, "item_code": it.code if it else "", "title": it.title if it else "", "quantity": l.quantity, "unit_cost": l.unit_cost,
                    "landed_parts": l.landed_parts})
    return out


@router.get("/po/{po_id}")
def get_sheet(po_id: int, db: Session = Depends(get_db)):
    po = _po(db, po_id)
    s = _sheet(db, po)
    vendor = db.get(Vendor, po.vendor_id)
    return {"po": {"id": po.id, "code": po.code, "vendor": vendor.name if vendor else "", "vendor_so": po.vendor_so_number},
            "lines": _lines(db, po), "data": json.loads(s.data) if s and s.data else None,
            "updated_by": s.updated_by if s else None, "updated_at": s.updated_at.isoformat() + "Z" if s and s.updated_at else None}


class SheetIn(BaseModel):
    data: dict


@router.put("/po/{po_id}")
def save_sheet(po_id: int, body: SheetIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    po = _po(db, po_id)
    raw = json.dumps(body.data)
    if len(raw) > 500_000:
        raise HTTPException(status_code=400, detail="That sheet is too big")
    s = _sheet(db, po)
    vendor = db.get(Vendor, po.vendor_id)
    if not s:
        s = VacctCostSheet(po_code=po.code, vendor_code=vendor.code if vendor else None, created_by=user.username)
        db.add(s)
    s.data, s.updated_by, s.updated_at = raw, user.username, datetime.utcnow()
    db.commit()
    return {"ok": True, "updated_at": s.updated_at.isoformat() + "Z"}


@router.get("/")
def list_sheets(vendor_id: Optional[int] = None, db: Session = Depends(get_db)):
    """The sheets there are (a vendor's, on its card)."""
    q = db.query(VacctCostSheet)
    if vendor_id:
        v = db.get(Vendor, vendor_id)
        q = q.filter(VacctCostSheet.vendor_code == (v.code if v else "-"))
    out = []
    for s in q.order_by(VacctCostSheet.updated_at.desc()).all():
        po = db.query(PurchaseOrder).filter(PurchaseOrder.code == s.po_code).first()
        d = json.loads(s.data or "{}")
        extras = sum(float(r.get("amount") or 0) for r in d.get("rows", []) if r.get("on", True))
        out.append({"po_code": s.po_code, "po_id": po.id if po else None, "extras": round(extras, 2), "rows": len(d.get("rows", [])),
                    "updated_by": s.updated_by, "updated_at": s.updated_at.isoformat() + "Z" if s.updated_at else None})
    return out


class ExportIn(BaseModel):
    table: list  # rows as the screen shows them: [[...], ...] with the header first


@router.post("/po/{po_id}/xlsx")
def export(po_id: int, body: ExportIn, db: Session = Depends(get_db)):
    """The sheet as the user sees it (what's ticked, how it's spread) -> Excel."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    po = _po(db, po_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "Cost Sheet"
    ws.append([f"Cost Sheet -- {po.code} (unofficial)"])
    ws["A1"].font = Font(bold=True, size=13)
    for r in body.table[:2000]:
        ws.append([c if isinstance(c, (int, float)) else str(c) for c in r][:30])
    for col in "ABCDEFGHIJ":
        ws.column_dimensions[col].width = 18
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": disposition(f"{po.code}-Cost Sheet.xlsx", inline=False)})
