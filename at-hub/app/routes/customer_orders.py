from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import (
    CustomerOrderCreate, CustomerOrderResponse, CreateShipmentRequest, ShipmentResponse,
    CustomerOrderUpdate, CustomerOrderLineAdd, CustomerOrderLineUpdate, OrderProfitResponse, LineOrderRequest,
)
from app.services.crud import CustomerOrderService, OrderProfitService
from app.dependencies import get_current_active_user, require_perm, require_any

manager = [Depends(require_perm("orders.edit"))]  # creating/editing/pricing orders
from app.models import User

router = APIRouter(prefix="/api/customer-orders", tags=["customer-orders"], dependencies=[Depends(require_any("orders.view", "shipments.view", "invoices", "quotes", "pod.upload"))])


@router.get("/", response_model=list[CustomerOrderResponse])
def list_orders(status: str | None = Query(None), db: Session = Depends(get_db)):
    return CustomerOrderService.list(db, status=status)


@router.post("/", response_model=CustomerOrderResponse, dependencies=manager)
def create_order(data: CustomerOrderCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return CustomerOrderService.create(db, data, created_by=current_user.username)


@router.get("/{order_id}", response_model=CustomerOrderResponse)
def get_order(order_id: int, db: Session = Depends(get_db)):
    return CustomerOrderService.get(db, order_id)


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
    return CustomerOrderService.update(db, order_id, data)


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
    return CustomerOrderService.add_line(db, order_id, data)


@router.put("/{order_id}/lines/{line_id}", response_model=CustomerOrderResponse, dependencies=manager)
def update_line(order_id: int, line_id: int, data: CustomerOrderLineUpdate, db: Session = Depends(get_db)):
    return CustomerOrderService.update_line(db, order_id, line_id, data)


@router.delete("/{order_id}/lines/{line_id}", response_model=CustomerOrderResponse, dependencies=manager)
def remove_line(order_id: int, line_id: int, db: Session = Depends(get_db)):
    return CustomerOrderService.remove_line(db, order_id, line_id)


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
