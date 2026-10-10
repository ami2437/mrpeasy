from typing import Optional
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import (
    CustomerOrderCreate, CustomerOrderResponse, CreateShipmentRequest, ShipmentResponse,
    CustomerOrderUpdate, CustomerOrderLineAdd, CustomerOrderLineUpdate, OrderProfitResponse, LineOrderRequest, LookalikeOkIn,
)
from app.services.crud import CustomerOrderService, OrderProfitService
from app.dependencies import get_current_active_user, require_perm, require_any

manager = [Depends(require_perm("orders.edit"))]  # creating/editing/pricing orders
from app.models import User

def _la(db, rec):
    """The order with its look-alikes not OK'd yet (services/lookalike.py), for the screens to show / ask about."""
    from app.services import lookalike
    rec.lookalikes = lookalike.pending(db, "customer", rec)
    return rec


router = APIRouter(prefix="/api/customer-orders", tags=["customer-orders"], dependencies=[Depends(require_any("orders.view", "shipments.view", "invoices", "quotes"))])  # drivers: /api/pod only


@router.get("/", response_model=list[CustomerOrderResponse])
def list_orders(status: str | None = Query(None), db: Session = Depends(get_db)):
    from app.services import lookalike
    orders = CustomerOrderService.list(db, status=status)
    lookalike.annotate(db, "customer", orders)
    return orders


@router.post("/", response_model=CustomerOrderResponse, dependencies=manager)
def create_order(data: CustomerOrderCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return _la(db, CustomerOrderService.create(db, data, created_by=current_user.username))


@router.post("/capture", response_model=CustomerOrderResponse, dependencies=manager)
def capture_order(customer_id: int = Form(...), po_number: str | None = Form(None), note: str | None = Form(None),
                  files: list[UploadFile] = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Quick capture: the customer's PO file + who it's from. The order waits as "Validation needed" (no lines, nothing
    confirmed or booked) until someone fills it in and validates it."""
    from app.routes.attachments import read_uploads, store_file
    blobs = read_uploads(files)
    order = CustomerOrderService.capture(db, customer_id, po_number, current_user.username)
    for name, ctype, data in blobs:
        store_file(db, "customer_order", order.id, "customer_po", name, ctype, data, note or "Quick capture", current_user.username)
    db.commit()
    db.refresh(order)
    return order


class ValidateIn(BaseModel):
    confirm: bool = False  # validate and confirm in one go


class PendingMatchIn(BaseModel):
    item_id: int
    quantity: Optional[float] = None
    unit_price: Optional[float] = None


@router.post("/{order_id}/ai-pending/{idx}/match", response_model=CustomerOrderResponse, dependencies=manager)
def match_pending(order_id: int, idx: int, data: PendingMatchIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """A line the AI read couldn't place: add it with this item (their wording is remembered for the next read)."""
    from app.services import ai_pending
    return ai_pending.match(db, "customer", order_id, idx, data.item_id, data.quantity, data.unit_price, current_user.username)


@router.post("/{order_id}/ai-pending/{idx}/discard", response_model=CustomerOrderResponse, dependencies=manager)
def discard_pending(order_id: int, idx: int, db: Session = Depends(get_db)):
    from app.services import ai_pending
    return ai_pending.discard(db, "customer", order_id, idx)


@router.post("/{order_id}/validate", response_model=CustomerOrderResponse, dependencies=manager)
def validate_order(order_id: int, data: ValidateIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """A quick-captured order checked against its PO: on to Draft (or Confirmed)."""
    return CustomerOrderService.validate(db, order_id, current_user.username, confirm=data.confirm)


@router.get("/{order_id}", response_model=CustomerOrderResponse)
def get_order(order_id: int, db: Session = Depends(get_db)):
    rec = CustomerOrderService.get(db, order_id)
    if rec.status == "validation" and rec.ai_pending:  # items made since: their waiting lines fill in now
        from app.services import ai_pending
        rec.ai_filled = ai_pending.rematch(db, "customer", rec)
    return _la(db, rec)


@router.get("/{order_id}/removal-plan", dependencies=[Depends(require_perm("orders.edit"))])
def removal_plan(order_id: int, db: Session = Depends(get_db)):
    """What has to go before this order can be cancelled / deleted -- the steps the Cancel / Delete pop-up runs."""
    return CustomerOrderService.removal_plan(db, order_id)


@router.get("/{order_id}/billing", dependencies=[Depends(require_perm("invoices"))])
def order_billing(order_id: int, db: Session = Depends(get_db)):
    """Per order line: ordered, shipped, billed across every invoice -- and the accepted differences with reasons."""
    from app.models import BillingVariance, CustomerOrder, Invoice
    from app.services import billing
    order = db.get(CustomerOrder, order_id)
    if not order:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Order not found")
    codes = dict(db.query(Invoice.id, Invoice.code).filter(Invoice.order_id == order_id).all())
    return {"lines": billing.order_ledger(db, order),
            "accepted": [{"order_line_id": v.order_line_id, "invoice": codes.get(v.invoice_id), "delivered": v.delivered_qty, "billed": v.billed_qty,
                          "reason": v.reason, "by": v.accepted_by} for v in db.query(BillingVariance).filter(BillingVariance.order_id == order_id).all()]}


@router.get("/{order_id}/profit", response_model=OrderProfitResponse, dependencies=[Depends(require_perm("money.view"))])
def order_profit(order_id: int, db: Session = Depends(get_db)):
    """Revenue vs. lot cost (incl. landed costs) for shipped, booked, and not-yet-booked quantity."""
    return OrderProfitService.calculate(db, order_id)


@router.put("/{order_id}", response_model=CustomerOrderResponse, dependencies=manager)
def update_order(order_id: int, data: CustomerOrderUpdate, db: Session = Depends(get_db)):
    return _la(db, CustomerOrderService.update(db, order_id, data))


@router.post("/{order_id}/lookalike-ok", response_model=CustomerOrderResponse, dependencies=manager)
def lookalike_ok(order_id: int, data: LookalikeOkIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Someone looked: this order is separate from the look-alike order(s), not a duplicate (kept with who and when)."""
    from app.services import lookalike
    return _la(db, lookalike.acknowledge(db, "customer", CustomerOrderService.get(db, order_id), data.codes, current_user.username))


@router.post("/{order_id}/duplicate-po-ok", response_model=CustomerOrderResponse, dependencies=manager)
def accept_duplicate_po(order_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Clear the possible-duplicate banner: this order is separate from the earlier one with the same customer PO #."""
    return CustomerOrderService.accept_duplicate_po(db, order_id, current_user.username)


@router.put("/{order_id}/line-order", response_model=CustomerOrderResponse, dependencies=manager)
def reorder_lines(order_id: int, data: LineOrderRequest, db: Session = Depends(get_db)):
    """Drag-to-reorder: the order's lines in their new display order."""
    from app.services.crud import reorder_lines as save_order
    order = CustomerOrderService.get(db, order_id)
    save_order(db, order.lines, data.line_ids)
    db.refresh(order)
    return order


@router.post("/{order_id}/lines", response_model=CustomerOrderResponse, dependencies=manager)
def add_line(order_id: int, data: CustomerOrderLineAdd, db: Session = Depends(get_db)):
    return _la(db, CustomerOrderService.add_line(db, order_id, data))


@router.put("/{order_id}/lines/{line_id}", response_model=CustomerOrderResponse, dependencies=manager)
def update_line(order_id: int, line_id: int, data: CustomerOrderLineUpdate, db: Session = Depends(get_db)):
    return _la(db, CustomerOrderService.update_line(db, order_id, line_id, data))


@router.delete("/{order_id}/lines/{line_id}", response_model=CustomerOrderResponse, dependencies=manager)
def remove_line(order_id: int, line_id: int, db: Session = Depends(get_db)):
    return _la(db, CustomerOrderService.remove_line(db, order_id, line_id))


@router.post("/{order_id}/confirm", response_model=CustomerOrderResponse, dependencies=manager)
def confirm_order(order_id: int, db: Session = Depends(get_db)):
    return CustomerOrderService.confirm(db, order_id)


@router.delete("/{order_id}", status_code=204, dependencies=manager)
def delete_order(order_id: int, db: Session = Depends(get_db)):
    """Cancelled orders only."""
    from fastapi import Response
    CustomerOrderService.delete(db, order_id)
    return Response(status_code=204)


@router.post("/{order_id}/cancel", response_model=CustomerOrderResponse, dependencies=manager)
def cancel_order(order_id: int, db: Session = Depends(get_db)):
    return CustomerOrderService.cancel(db, order_id)


@router.post("/{order_id}/shipments", response_model=ShipmentResponse, dependencies=[Depends(require_perm("shipments.work"))])
def create_shipment(order_id: int, data: CreateShipmentRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Create a shipment and book stock into it. Stock leaves on-hand only once the shipment is fully picked."""
    return CustomerOrderService.create_shipment(db, order_id, data, created_by=current_user.username)
