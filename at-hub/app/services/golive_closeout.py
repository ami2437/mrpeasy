"""Go-live close-out (user, 2026-10-16): run once after the final MRPeasy import, so AT-HUB starts with real open work only.

  1. Old purchase orders still open (ordered / shipped / partially received) and ordered more than N months ago (3 by
     default): the goods came, MRPeasy just never had them received. Every open quantity is RECEIVED (user's choice:
     stock goes up, through the normal receiving -- lots, ledger, costs), the PO closes itself, and it gets a note.
  2. Shipments that shipped, have no delivery date, and are paid in full on a fully invoiced order: delivered on their
     ship date, with a note.

preview() shows exactly what would change (quantities, stock value); apply() does it and keeps a log
(AppSetting "golive_closeout"), and undo() puts it back -- a receipt whose stock has since been booked or shipped can't be
undone and is reported."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import List

from sqlalchemy.orm import Session

from app.models import (AppSetting, Customer, CustomerOrder, Invoice, InvoiceShipment, Lot, PurchaseOrder, PurchaseOrderLine, Shipment,
                        StockItem, Vendor)
from app.services.money import cents

KEY = "golive_closeout"
OPEN_PO = ("ordered", "shipped", "partially_received")


def _cutoff(months: int) -> datetime:
    return datetime.utcnow() - timedelta(days=int(round(30.44 * months)))


def old_pos(db: Session, months: int = 3) -> List[dict]:
    from app.models import ProductGroup
    non_stock = {g.name for g in db.query(ProductGroup).filter(ProductGroup.non_stock.is_(True)).all()}
    out = []
    for po in (db.query(PurchaseOrder).filter(PurchaseOrder.status.in_(OPEN_PO), PurchaseOrder.order_date < _cutoff(months))
               .order_by(PurchaseOrder.order_date).all()):
        lines = []
        for ln in db.query(PurchaseOrderLine).filter(PurchaseOrderLine.po_id == po.id).order_by(PurchaseOrderLine.position).all():
            left = round((ln.quantity or 0) - (ln.received_quantity or 0), 6)
            if left > 1e-9:
                item = db.get(StockItem, ln.item_id)
                lines.append({"line_id": ln.id, "item": item.code if item else "", "quantity": left,
                              "value": cents(left * (ln.unit_cost or 0)), "non_stock": bool(item and item.category in non_stock)})
        if lines:
            vendor = db.get(Vendor, po.vendor_id)
            out.append({"po_id": po.id, "code": po.code, "vendor": vendor.name if vendor else "", "status": po.status,
                        "vendor_so": po.vendor_so_number, "ordered": po.order_date.date().isoformat() if po.order_date else None,
                        "lines": lines, "quantity": sum(l["quantity"] for l in lines), "value": cents(sum(l["value"] for l in lines)),
                        "non_stock": all(l["non_stock"] for l in lines)})
    return out


def paid_shipments(db: Session) -> List[dict]:
    """Shipped, no delivery date, every live invoice on it paid, and its order fully invoiced."""
    out = []
    for sh in (db.query(Shipment).filter(Shipment.delivered_at.is_(None), Shipment.ship_date.isnot(None),
                                         Shipment.status.in_(("shipped", "invoiced"))).order_by(Shipment.ship_date).all()):
        invs = (db.query(Invoice).join(InvoiceShipment, InvoiceShipment.invoice_id == Invoice.id)
                .filter(InvoiceShipment.shipment_id == sh.id, Invoice.status.notin_(("void", "draft"))).all())
        if not invs or any(i.status != "paid" for i in invs):
            continue
        order = db.get(CustomerOrder, sh.order_id)
        # fully invoiced: nothing left to ship, and every shipment of the order is on an invoice
        if order is None or order.status not in ("shipped", "invoiced"):
            continue
        if any(o.status != "invoiced" for o in db.query(Shipment).filter(Shipment.order_id == order.id, Shipment.status != "cancelled")):
            continue
        out.append({"shipment_id": sh.id, "code": sh.code, "ship_date": sh.ship_date.date().isoformat(), "status": sh.status,
                    "order": order.code if order else None, "customer": _customer(db, order),
                    "invoices": [i.code for i in invs]})
    return out


def _customer(db, order) -> str:
    c = db.get(Customer, order.customer_id) if order else None
    return c.name if c else ""


def preview(db: Session, months: int = 3) -> dict:
    pos, ships = old_pos(db, months), paid_shipments(db)
    return {"months": months, "pos": pos, "shipments": ships, "po_value": cents(sum(p["value"] for p in pos)),
            "non_stock_value": cents(sum(l["value"] for p in pos for l in p["lines"] if l["non_stock"])),
            "po_quantity": sum(p["quantity"] for p in pos), "done": log(db)}


def log(db: Session):
    row = db.get(AppSetting, KEY)
    return json.loads(row.value) if row and row.value else None


def apply(db: Session, months: int, by: str, po_ids=None, shipment_ids=None) -> dict:
    """Receive and close the old POs, deliver the paid shipments. po_ids / shipment_ids: only those (None = all shown)."""
    from app.services.crud import PurchaseOrderService
    if log(db) and not log(db).get("undone_at"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="The close-out was already run -- undo it first to run it again")
    today = datetime.utcnow().strftime("%b %d, %Y")
    done = {"at": datetime.utcnow().isoformat(timespec="seconds"), "by": by, "months": months, "pos": [], "shipments": [], "problems": []}
    for p in old_pos(db, months):
        if po_ids is not None and p["po_id"] not in po_ids:
            continue
        before = {l.id for l in db.query(Lot).join(PurchaseOrderLine, Lot.po_line_id == PurchaseOrderLine.id)
                  .filter(PurchaseOrderLine.po_id == p["po_id"]).all()}
        try:
            data = SimpleNamespace(lines=[SimpleNamespace(line_id=l["line_id"], quantity=l["quantity"], lot_code=None, expiry_date=None)
                                          for l in p["lines"]], freight_cost=None, tariff_cost=None)
            PurchaseOrderService.receive(db, p["po_id"], data, created_by=by)
        except Exception as e:  # one PO that can't be received doesn't stop the rest
            db.rollback()
            done["problems"].append(f"{p['code']}: {getattr(e, 'detail', e)}")
            continue
        po = db.get(PurchaseOrder, p["po_id"])
        lots = [l.id for l in db.query(Lot).join(PurchaseOrderLine, Lot.po_line_id == PurchaseOrderLine.id)
                .filter(PurchaseOrderLine.po_id == po.id).all() if l.id not in before]
        note = (f"Closed at go-live {today}: received the {len(p['lines'])} line(s) still open -- never received in MRPeasy "
                f"(ordered {p['ordered'] or '?'}).")
        po.notes = ((po.notes or "").rstrip() + "\n" + note).strip()
        done["pos"].append({"po_id": po.id, "code": po.code, "lot_ids": lots, "note": note, "value": p["value"]})
        db.commit()
    for s in paid_shipments(db):
        if shipment_ids is not None and s["shipment_id"] not in shipment_ids:
            continue
        sh = db.get(Shipment, s["shipment_id"])
        prev = sh.status
        sh.delivered_at, sh.delivered_by = sh.ship_date, f"{by} (go-live)"
        if sh.status == "shipped":
            sh.status = "delivered"
        done["shipments"].append({"id": sh.id, "code": sh.code, "prev_status": prev})
    row = db.get(AppSetting, KEY) or AppSetting(key=KEY)
    row.value, row.updated_by = json.dumps(done), by
    db.merge(row)
    db.commit()
    return done


def undo(db: Session, by: str) -> dict:
    from fastapi import HTTPException
    from app.services import receipt_edit
    done = log(db)
    if not done or done.get("undone_at"):
        raise HTTPException(status_code=400, detail="Nothing to undo")
    problems = []
    for p in done["pos"]:
        po = db.get(PurchaseOrder, p["po_id"])
        if not po:
            continue
        lots = [i for i in p["lot_ids"] if db.get(Lot, i)]
        try:
            if lots:
                receipt_edit.undo(db, po, lots, by)
        except HTTPException as e:
            db.rollback()
            problems.append(f"{p['code']}: {e.detail}")
            continue
        po = db.get(PurchaseOrder, p["po_id"])
        po.notes = (po.notes or "").replace(p["note"], "").strip() or None
        db.commit()
    for s in done["shipments"]:
        sh = db.get(Shipment, s["id"])
        if sh and sh.delivered_at == sh.ship_date:
            sh.delivered_at = sh.delivered_by = None
            if sh.status == "delivered":
                sh.status = s["prev_status"]
    done["undone_at"], done["undone_by"], done["undo_problems"] = datetime.utcnow().isoformat(timespec="seconds"), by, problems
    row = db.get(AppSetting, KEY)
    row.value = json.dumps(done)
    db.commit()
    return done
