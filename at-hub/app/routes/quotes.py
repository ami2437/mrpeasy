"""Quotations: create (by hand or from pasted RFQ text), price from history, PDF, email, convert to an order."""
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_role
from app.models import Customer, Quote, QuoteLine, StockItem, User
from app.services import item_alias, quotes as quote_svc
from app.services.crud import generate_code
from app.services.money import line_amount

router = APIRouter(prefix="/api/quotes", tags=["quotes"], dependencies=[Depends(require_role("manager"))])
STATUSES = ("draft", "sent", "accepted", "declined", "converted")


class LineIn(BaseModel):
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: float = 1
    unit_price: float = 0
    notes: Optional[str] = None
    source_text: Optional[str] = None


class QuoteIn(BaseModel):
    customer_id: int
    valid_until: Optional[datetime] = None
    customer_ref: Optional[str] = None
    notes: Optional[str] = None
    lines: List[LineIn] = []


def _out(db: Session, q: Quote) -> dict:
    items = {i.id: i for i in db.query(StockItem).filter(StockItem.id.in_({l.item_id for l in q.lines if l.item_id})).all()}
    cust = db.query(Customer).filter(Customer.id == q.customer_id).first()
    lines = [{"id": l.id, "item_id": l.item_id, "item_code": items[l.item_id].code if l.item_id in items else None,
              "description": l.description or (items[l.item_id].title if l.item_id in items else ""), "quantity": l.quantity,
              "unit_price": l.unit_price, "amount": line_amount(l.quantity, l.unit_price), "notes": l.notes, "source_text": l.source_text}
             for l in q.lines]
    return {"id": q.id, "code": q.code, "customer_id": q.customer_id, "customer": cust.name if cust else "", "status": q.status,
            "quote_date": q.quote_date, "valid_until": q.valid_until, "customer_ref": q.customer_ref, "notes": q.notes,
            "order_id": q.order_id, "created_by": q.created_by, "lines": lines, "row_version": q.row_version or 1, "updated_by": q.updated_by, "total": round(sum(l["amount"] for l in lines), 2),
            "customer_email": cust.email_for("quote") if cust else None, "customer_contact": cust.contact_name if cust else None,
            "emails": [{"to": e.to_address, "cc": e.cc_address, "subject": e.subject, "sent_by": e.sent_by,
                        "sent_at": e.sent_at.isoformat() if e.sent_at else None} for e in q.emails]}


def _get(db: Session, quote_id: int) -> Quote:
    q = db.query(Quote).filter(Quote.id == quote_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Quote not found")
    return q


def _set_lines(db: Session, q: Quote, lines: List[LineIn]) -> None:
    q.lines = []
    db.flush()
    for pos, l in enumerate(lines):
        if l.quantity <= 0:
            raise HTTPException(status_code=400, detail=f"Line {pos + 1}: quantity must be more than 0")
        if not l.item_id and not (l.description or "").strip():
            raise HTTPException(status_code=400, detail=f"Line {pos + 1}: pick an item or type a description")
        if l.item_id:
            from app.services.crud import not_for_sale
            not_for_sale(db.query(StockItem).filter(StockItem.id == l.item_id).first())
        q.lines.append(QuoteLine(position=pos, item_id=l.item_id, description=(l.description or "").strip() or None, quantity=l.quantity,
                                 unit_price=l.unit_price, notes=(l.notes or "").strip() or None, source_text=l.source_text))
        if l.item_id and l.source_text:  # the customer's wording -> the item picked: next paste matches it
            item_alias.learn(db, "customer", q.customer_id, l.item_id, None, quote_svc.split_qty(l.source_text)[1])


@router.get("/")
def list_quotes(db: Session = Depends(get_db)):
    return [_out(db, q) for q in db.query(Quote).order_by(Quote.id.desc()).all()]


@router.get("/{quote_id}")
def get_quote(quote_id: int, db: Session = Depends(get_db)):
    return _out(db, _get(db, quote_id))


@router.post("/")
def create(data: QuoteIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    if not db.query(Customer).filter(Customer.id == data.customer_id).first():
        raise HTTPException(status_code=400, detail="Pick a customer")
    q = Quote(code=generate_code(db, Quote, "Q"), customer_id=data.customer_id, customer_ref=data.customer_ref, notes=data.notes,
              valid_until=data.valid_until or datetime.utcnow() + timedelta(days=30), created_by=user.username)
    db.add(q)
    db.flush()
    _set_lines(db, q, data.lines)
    db.commit()
    return _out(db, q)


@router.put("/{quote_id}")
def update(quote_id: int, data: QuoteIn, db: Session = Depends(get_db)):
    q = _get(db, quote_id)
    if q.status == "converted":
        raise HTTPException(status_code=400, detail="This quote is already an order -- change the order instead")
    q.customer_id, q.valid_until, q.customer_ref, q.notes = data.customer_id, data.valid_until, data.customer_ref, data.notes
    _set_lines(db, q, data.lines)
    db.commit()
    return _out(db, q)


class StatusIn(BaseModel):
    status: str


@router.put("/{quote_id}/status")
def set_status(quote_id: int, data: StatusIn, db: Session = Depends(get_db)):
    q = _get(db, quote_id)
    if data.status not in STATUSES[:4] or q.status == "converted":
        raise HTTPException(status_code=400, detail="Can't set that status")
    q.status = data.status
    db.commit()
    return _out(db, q)


class ParseIn(BaseModel):
    customer_id: int
    text: str


@router.post("/parse")
def parse(data: ParseIn, db: Session = Depends(get_db)):
    """Pasted RFQ text -> suggested lines (nothing saved)."""
    return quote_svc.parse_text(db, data.customer_id, data.text)


@router.get("/price/{item_id}")
def price(item_id: int, customer_id: int, exclude_quote_id: Optional[int] = None, db: Session = Depends(get_db)):
    item = db.query(StockItem).filter(StockItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return quote_svc.suggest_price(db, item, customer_id, exclude_quote_id)


class PricesIn(BaseModel):
    customer_id: int
    item_ids: List[int]
    exclude_quote_id: Optional[int] = None


@router.post("/prices")
def prices(data: PricesIn, db: Session = Depends(get_db)):
    """Price hints for every line of a saved quote at once (keyed by item id); prices on the quote stay as they are."""
    items = db.query(StockItem).filter(StockItem.id.in_(set(data.item_ids))).all()
    return {i.id: quote_svc.suggest_price(db, i, data.customer_id, data.exclude_quote_id) for i in items}


@router.get("/item-history/{item_id}")
def item_history(item_id: int, customer_id: Optional[int] = None, exclude_quote_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Every earlier quote of this item, newest first (this customer's flagged `mine`)."""
    return quote_svc.quoted_history(db, item_id, customer_id, exclude_quote_id)


class EmailIn(BaseModel):
    to: str  # one or more addresses, comma/semicolon separated
    cc: Optional[str] = None
    subject: str
    body: str
    attach_pdf: bool = True


@router.post("/{quote_id}/email")
def email(quote_id: int, data: EmailIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    from app.services import email as email_service
    q = _get(db, quote_id)
    email_service.send_quote(db, q, data.to, data.cc, data.subject, data.body, data.attach_pdf, user.username)
    return _out(db, q)


class ConvertIn(BaseModel):
    po_number: Optional[str] = None
    delivery_date: Optional[datetime] = None


@router.post("/{quote_id}/convert")
def convert(quote_id: int, data: ConvertIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    from app import schemas
    from app.services.crud import CustomerOrderService
    q = _get(db, quote_id)
    if q.status == "converted":
        raise HTTPException(status_code=400, detail="Already converted")
    missing = [str(i + 1) for i, l in enumerate(q.lines) if not l.item_id]
    if missing:
        raise HTTPException(status_code=400, detail=f"Pick an item for line(s) {', '.join(missing)} first (create the item if it's new)")
    order = CustomerOrderService.create(db, schemas.CustomerOrderCreate(
        customer_id=q.customer_id, po_number=data.po_number, delivery_date=data.delivery_date,
        notes=f"From quote {q.code}" + (f" ({q.customer_ref})" if q.customer_ref else ""),
        lines=[schemas.CustomerOrderLineCreate(item_id=l.item_id, quantity=l.quantity, unit_price=l.unit_price, notes=l.notes)
               for l in q.lines]), user.username)
    q.status_before_convert, q.status, q.order_id = q.status, "converted", order.id
    db.commit()
    return {"order_id": order.id, "order": order.code, "quote": _out(db, q)}


@router.delete("/{quote_id}")
def delete(quote_id: int, db: Session = Depends(get_db)):
    q = _get(db, quote_id)
    if q.status == "converted":
        raise HTTPException(status_code=400, detail="A converted quote stays as the order's history")
    db.delete(q)
    db.commit()
    return {"deleted": quote_id}


@router.get("/{quote_id}/pdf")
def pdf(quote_id: int, db: Session = Depends(get_db)):
    from app.services.pdf import quote_pdf
    q = _get(db, quote_id)
    return Response(quote_pdf(db, q), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="Quote-{q.code}.pdf"'})
