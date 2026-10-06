"""MTR library: material test reports uploaded on purchase orders, mapped to the PO lines
(and so the items) they certify. Because lots remember the PO line they were received
from, and shipments remember the lot they shipped from, each MTR traces forward to the
customer orders it went out on -- and each customer order traces back to its MTRs, so
the right report can be emailed when a customer asks."""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_perm, require_any
from app.models import (Attachment, Customer, CustomerOrder, Lot, MtrEmail, MtrLink, PurchaseOrder,
                        PurchaseOrderLine, Shipment, ShipmentLine, StockItem, User, Vendor)

router = APIRouter(prefix="/api/mtrs", tags=["mtrs"], dependencies=[Depends(require_any("stock.view", "orders.view", "shipments.view", "purchasing"))])


class LinkRequest(BaseModel):
    po_line_ids: List[int] = []
    heat_number: Optional[str] = None


class MtrEmailRequest(BaseModel):
    attachment_ids: List[int]
    order_id: Optional[int] = None
    to: str
    cc: Optional[str] = None
    subject: str
    body: str = ""


def _iso(d: Optional[datetime]):
    return d.isoformat() if d else None


def _mtr(db: Session, attachment_id: int) -> Attachment:
    att = db.query(Attachment).filter(Attachment.id == attachment_id, Attachment.category == "mtr").first()
    if not att:
        raise HTTPException(status_code=404, detail="MTR not found")
    return att


def _orders_for_po_lines(db: Session, po_line_ids: set) -> dict:
    """po_line_id -> [{order + shipment}] for every shipment line booked from a lot received on that PO line."""
    if not po_line_ids:
        return {}
    rows = (db.query(Lot.po_line_id, ShipmentLine, Shipment, CustomerOrder, Customer)
            .join(ShipmentLine, ShipmentLine.lot_id == Lot.id)
            .join(Shipment, Shipment.id == ShipmentLine.shipment_id)
            .join(CustomerOrder, CustomerOrder.id == Shipment.order_id)
            .join(Customer, Customer.id == CustomerOrder.customer_id)
            .filter(Lot.po_line_id.in_(po_line_ids), Shipment.status != "cancelled").all())
    out: dict = {}
    for po_line_id, sl, sh, order, cust in rows:
        out.setdefault(po_line_id, []).append({
            "order_id": order.id, "order_code": order.code, "po_number": order.po_number, "job_number": order.job_number,
            "customer": cust.name, "shipment_id": sh.id, "shipment_code": sh.code,
            "ship_date": _iso(sh.ship_date), "quantity": sl.picked_quantity or sl.quantity,
        })
    return out


def _library_rows(db: Session, links: list) -> list:
    """One row per (MTR, PO line): item, PO, vendor, file, heat #, and the customer orders it shipped on."""
    if not links:
        return []
    att_ids = {l.attachment_id for l in links}
    line_ids = {l.po_line_id for l in links}
    atts = {a.id: a for a in db.query(Attachment).filter(Attachment.id.in_(att_ids))}
    lines = {l.id: l for l in db.query(PurchaseOrderLine).filter(PurchaseOrderLine.id.in_(line_ids))}
    pos = {p.id: p for p in db.query(PurchaseOrder).filter(PurchaseOrder.id.in_({l.po_id for l in lines.values()}))}
    vendors = {v.id: v for v in db.query(Vendor).filter(Vendor.id.in_({p.vendor_id for p in pos.values()}))}
    items = {i.id: i for i in db.query(StockItem).filter(StockItem.id.in_({l.item_id for l in links}))}
    shipped = _orders_for_po_lines(db, line_ids)
    rows = []
    for link in links:
        att, line = atts.get(link.attachment_id), lines.get(link.po_line_id)
        if not att or not line:
            continue
        po = pos.get(line.po_id)
        item = items.get(link.item_id)
        vendor = vendors.get(po.vendor_id) if po else None
        rows.append({
            "link_id": link.id, "attachment_id": att.id, "filename": att.filename, "note": att.note,
            "uploaded_at": _iso(att.created_at), "uploaded_by": att.uploaded_by, "heat_number": link.heat_number,
            "item_id": link.item_id, "item_code": item.code if item else None, "item_title": item.title if item else None,
            "po_id": po.id if po else None, "po_code": po.code if po else None, "po_date": _iso(po.order_date) if po else None,
            "vendor_id": vendor.id if vendor else None, "vendor": vendor.name if vendor else None,
            "po_line_id": line.id, "po_line_qty": line.quantity, "received_qty": line.received_quantity,
            "shipped_on": shipped.get(line.id, []),
        })
    rows.sort(key=lambda r: (r["po_date"] or "", r["uploaded_at"] or ""), reverse=True)
    return rows


@router.get("/")
def library(item_id: Optional[int] = None, vendor_id: Optional[int] = None, po_id: Optional[int] = None,
            db: Session = Depends(get_db)):
    """Every MTR mapping, newest PO first; filter by item, vendor or PO."""
    q = db.query(MtrLink)
    if item_id:
        q = q.filter(MtrLink.item_id == item_id)
    if po_id or vendor_id:
        q = q.join(PurchaseOrderLine, PurchaseOrderLine.id == MtrLink.po_line_id).join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
        if po_id:
            q = q.filter(PurchaseOrder.id == po_id)
        if vendor_id:
            q = q.filter(PurchaseOrder.vendor_id == vendor_id)
    return _library_rows(db, q.all())


@router.get("/unlinked")
def unlinked(db: Session = Depends(get_db)):
    """MTRs uploaded on a PO but not yet tied to any of its lines."""
    linked = {a for (a,) in db.query(MtrLink.attachment_id).distinct()}
    atts = db.query(Attachment).filter(Attachment.category == "mtr", Attachment.entity_type == "purchase_order").all()
    pos = {p.id: p for p in db.query(PurchaseOrder).filter(PurchaseOrder.id.in_({a.entity_id for a in atts}))}
    return [{"attachment_id": a.id, "filename": a.filename, "uploaded_at": _iso(a.created_at), "po_id": a.entity_id,
             "po_code": pos[a.entity_id].code if a.entity_id in pos else None}
            for a in atts if a.id not in linked]


@router.get("/purchase-order/{po_id}", dependencies=[Depends(require_perm("mtrs.manage"))])
def for_purchase_order(po_id: int, db: Session = Depends(get_db)):
    """The PO's MTR files with the line ids each one covers -- drives the linking grid on the PO page."""
    atts = (db.query(Attachment).filter(Attachment.entity_type == "purchase_order", Attachment.entity_id == po_id,
                                        Attachment.category == "mtr").order_by(Attachment.created_at).all())
    links = db.query(MtrLink).filter(MtrLink.attachment_id.in_([a.id for a in atts])).all() if atts else []
    return [{
        "attachment_id": a.id, "filename": a.filename, "note": a.note, "uploaded_at": _iso(a.created_at),
        "po_line_ids": [l.po_line_id for l in links if l.attachment_id == a.id],
        "heat_number": next((l.heat_number for l in links if l.attachment_id == a.id and l.heat_number), None),
    } for a in atts]


@router.put("/{attachment_id}/links", dependencies=[Depends(require_perm("mtrs.manage"))])
def set_links(attachment_id: int, data: LinkRequest, db: Session = Depends(get_db),
              user: User = Depends(get_current_active_user)):
    """Replace the set of PO lines this MTR covers. Lines must be on the PO the MTR is attached to."""
    att = _mtr(db, attachment_id)
    if att.entity_type != "purchase_order":
        raise HTTPException(status_code=400, detail="Only MTRs on a purchase order can be linked to lines")
    lines = {l.id: l for l in db.query(PurchaseOrderLine).filter(PurchaseOrderLine.po_id == att.entity_id)}
    bad = [i for i in data.po_line_ids if i not in lines]
    if bad:
        raise HTTPException(status_code=400, detail="Some lines aren't on this purchase order")
    heat = (data.heat_number or "").strip() or None
    db.query(MtrLink).filter(MtrLink.attachment_id == att.id).delete()
    for line_id in dict.fromkeys(data.po_line_ids):
        db.add(MtrLink(attachment_id=att.id, po_line_id=line_id, item_id=lines[line_id].item_id,
                       heat_number=heat, created_by=user.username))
    db.commit()
    return for_purchase_order(att.entity_id, db)


@router.get("/customer-order/{order_id}")
def for_customer_order(order_id: int, db: Session = Depends(get_db)):
    """Per order line: MTRs traced exactly (via the lots it shipped from) and, for reference,
    other MTRs on file for the same item."""
    order = db.query(CustomerOrder).filter(CustomerOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    customer = db.query(Customer).filter(Customer.id == order.customer_id).first()
    item_ids = {l.item_id for l in order.lines}
    all_rows = _library_rows(db, db.query(MtrLink).filter(MtrLink.item_id.in_(item_ids)).all()) if item_ids else []
    items = {i.id: i for i in db.query(StockItem).filter(StockItem.id.in_(item_ids))} if item_ids else {}
    out = []
    for line in sorted(order.lines, key=lambda l: l.id):
        lots = {sl.lot_id for sl in line.shipment_lines if sl.lot_id and sl.shipment and sl.shipment.status != "cancelled"}
        lot_lines = {po_line for (po_line,) in db.query(Lot.po_line_id).filter(Lot.id.in_(lots))} if lots else set()
        rows = [r for r in all_rows if r["item_id"] == line.item_id]
        item = items.get(line.item_id)
        out.append({
            "order_line_id": line.id, "item_id": line.item_id,
            "item_code": item.code if item else None, "item_title": item.title if item else None,
            "shipped_lots": len(lots), "untraced": None in lot_lines,  # shipped from a lot with no PO receipt (stock adjustment)
            "traced": [r for r in rows if r["po_line_id"] in lot_lines],
            "other": [r for r in rows if r["po_line_id"] not in lot_lines],
        })
    emails = db.query(MtrEmail).filter(MtrEmail.order_id == order.id).order_by(MtrEmail.sent_at.desc()).all()
    return {
        "order_id": order.id, "order_code": order.code, "po_number": order.po_number,
        "customer": customer.name if customer else None, "customer_email": customer.mtr_email if customer else None,
        "lines": out,
        "emails": [{"to": e.to_address, "cc": e.cc_address, "subject": e.subject, "files": e.files,
                    "sent_by": e.sent_by, "sent_at": _iso(e.sent_at)} for e in emails],
    }


@router.post("/email", dependencies=[Depends(require_any("orders.view", "stock.view", "purchasing"))])  # not drivers
def email_mtrs(data: MtrEmailRequest, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """Email chosen MTR files to a customer, logged against the order."""
    from app.routes.attachments import upload_root
    from app.services import email as email_service
    if not data.attachment_ids:
        raise HTTPException(status_code=400, detail="Pick at least one MTR to send")
    files = []
    for att_id in dict.fromkeys(data.attachment_ids):
        att = _mtr(db, att_id)
        path = (upload_root() / att.stored_name).resolve()
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"{att.filename} is missing from the server")
        files.append((path.read_bytes(), att.filename, att.content_type or "application/octet-stream"))
    rows = [("Material test reports", str(len(files)))]
    order = db.query(CustomerOrder).filter(CustomerOrder.id == data.order_id).first() if data.order_id else None
    if order:
        rows.insert(0, ("Order", order.code))
        if order.po_number:
            rows.insert(1, ("Your PO #", order.po_number))
    to_list, cc_list = email_service._send(db, data.to, data.cc, data.subject, data.body, rows, files)
    log = MtrEmail(order_id=order.id if order else None, to_address=", ".join(to_list), cc_address=", ".join(cc_list) or None,
                   subject=data.subject.strip(), files=", ".join(f[1] for f in files), sent_by=user.username)
    db.add(log)
    db.commit()
    return {"ok": True, "sent_to": to_list}
