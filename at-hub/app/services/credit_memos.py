"""Credit memos: money given back to a customer (over-billing, returns, price corrections).

    create(db, data, by)            -> a draft, for an invoice (customer + order from it) or a customer / order
    update(db, memo_id, data)       -> reason, date, lines -- drafts only
    issue(db, memo_id)              -> draft -> issued: it now counts (order ledger, statements, aging)
    apply(db, memo_id, inv_id, amt) -> use it on an open invoice: a payment "credit memo <code>" on that invoice
    void(db, memo_id, reason)       -> nothing applied yet
    delete(db, memo_id)             -> drafts only

Lines tied to an order line lower what that line billed (billing.order_ledger), so a credit for over-billed units
squares the order check. Applying is undone by removing that payment from the invoice.
"""
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import CreditMemo, CreditMemoLine, Customer, CustomerOrder, CustomerOrderLine, Invoice, InvoicePayment
from app.services import clock
from app.services.money import cents


def get(db: Session, memo_id: int) -> CreditMemo:
    m = db.get(CreditMemo, memo_id)
    if not m:
        raise HTTPException(status_code=404, detail="Credit memo not found")
    return m


def list_all(db: Session, customer_id: Optional[int] = None) -> List[CreditMemo]:
    q = db.query(CreditMemo)
    if customer_id:
        q = q.filter(CreditMemo.customer_id == customer_id)
    return q.order_by(CreditMemo.id.desc()).all()


def _lines(db: Session, memo: CreditMemo, rows) -> None:
    order_lines = {l.id: l for l in db.query(CustomerOrderLine).filter(CustomerOrderLine.order_id == memo.order_id).all()} if memo.order_id else {}
    memo.lines.clear()
    for r in rows or []:
        qty, price = float(r.quantity or 0), cents(r.unit_price or 0)
        if qty <= 0:
            continue  # a line left at 0 isn't credited
        if price < 0:
            raise HTTPException(status_code=400, detail=f"{r.description or 'A line'}: a credit is entered as a positive price")
        if r.order_line_id and r.order_line_id not in order_lines:
            raise HTTPException(status_code=400, detail="A line points at an order line of a different order")
        desc = (r.description or "").strip()
        if not desc:
            raise HTTPException(status_code=400, detail="Every credited line needs a description")
        memo.lines.append(CreditMemoLine(item_id=r.item_id, order_line_id=r.order_line_id or None, description=desc[:300], quantity=qty, unit_price=price))


def create(db: Session, data, by: str) -> CreditMemo:
    from app.services.crud import generate_code
    inv = db.get(Invoice, data.invoice_id) if data.invoice_id else None
    if data.invoice_id and not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    customer_id = inv.customer_id if inv else data.customer_id
    order_id = inv.order_id if inv else data.order_id
    if order_id and not inv:
        o = db.get(CustomerOrder, order_id)
        if not o:
            raise HTTPException(status_code=404, detail="Order not found")
        customer_id = customer_id or o.customer_id
    if not customer_id or not db.get(Customer, customer_id):
        raise HTTPException(status_code=400, detail="Pick the customer the credit is for")
    memo = CreditMemo(code=generate_code(db, CreditMemo, "CM"), customer_id=customer_id, order_id=order_id, invoice_id=inv.id if inv else None,
                      memo_date=clock.calendar_from_input(data.memo_date) or clock.today(), reason=(data.reason or "").strip() or None,
                      status="draft", created_by=by)
    db.add(memo)
    db.flush()
    _lines(db, memo, data.lines)
    db.commit()
    db.refresh(memo)
    return memo


def update(db: Session, memo_id: int, data) -> CreditMemo:
    memo = get(db, memo_id)
    if memo.status != "draft":
        raise HTTPException(status_code=400, detail=f"{memo.code} is {memo.status} -- only a draft can be changed (void it and make a new one)")
    if data.reason is not None:
        memo.reason = data.reason.strip() or None
    if data.memo_date is not None:
        memo.memo_date = clock.calendar_from_input(data.memo_date)
    if data.lines is not None:
        _lines(db, memo, data.lines)
    db.commit()
    db.refresh(memo)
    return memo


def issue(db: Session, memo_id: int) -> CreditMemo:
    memo = get(db, memo_id)
    if memo.status != "draft":
        raise HTTPException(status_code=400, detail=f"{memo.code} is already {memo.status}")
    if memo.total <= 0.005:
        raise HTTPException(status_code=400, detail="Add what's being credited first -- the credit is $0.00")
    memo.status = "issued"
    db.commit()
    db.refresh(memo)
    return memo


def apply(db: Session, memo_id: int, invoice_id: int, amount: Optional[float], by: str) -> CreditMemo:
    memo = get(db, memo_id)
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if memo.status != "issued":
        raise HTTPException(status_code=400, detail=f"{memo.code} is {memo.status} -- issue it before using it")
    if inv.customer_id != memo.customer_id:
        raise HTTPException(status_code=400, detail=f"{inv.code} is another customer's invoice")
    if inv.status != "sent":
        raise HTTPException(status_code=400, detail=f"{inv.code} is {inv.status} -- a credit goes on a sent invoice with a balance")
    most = cents(min(memo.remaining, inv.balance))
    amt = most if amount is None else cents(amount)
    if amt <= 0:
        raise HTTPException(status_code=400, detail="Nothing to apply: the credit or the invoice is already used up")
    if amt > most + 0.005:
        raise HTTPException(status_code=400, detail=f"At most {most:,.2f} can go on {inv.code} ({memo.code} has {memo.remaining:,.2f} left, "
                                                    f"{inv.code} owes {inv.balance:,.2f})")
    db.add(InvoicePayment(invoice_id=inv.id, amount=amt, paid_date=clock.today(), method="credit memo", reference=memo.code,
                          note=f"Credit memo {memo.code}", credit_memo_id=memo.id, created_by=by))
    db.flush()
    db.refresh(inv)
    if inv.balance <= 0.005:
        inv.status = "paid"
    db.commit()
    db.refresh(memo)
    return memo


def void(db: Session, memo_id: int, reason: Optional[str]) -> CreditMemo:
    memo = get(db, memo_id)
    if memo.status == "void":
        raise HTTPException(status_code=400, detail=f"{memo.code} is already void")
    if memo.applications:
        raise HTTPException(status_code=400, detail=f"{memo.code} is used on {len(memo.applications)} invoice(s) -- remove those payments first")
    memo.status = "void"
    memo.void_reason = (reason or "").strip() or None
    db.commit()
    db.refresh(memo)
    return memo


def delete(db: Session, memo_id: int) -> None:
    memo = get(db, memo_id)
    if memo.status not in ("draft", "void") or memo.applications:
        raise HTTPException(status_code=400, detail=f"{memo.code} was issued -- void it instead (that keeps a record)")
    for line in list(memo.lines):
        db.delete(line)
    db.delete(memo)
    db.commit()


def credited_by_order_line(db: Session, order_id: Optional[int] = None) -> dict:
    """order_line_id -> (quantity, amount) credited by issued memos (drafts and void ones don't count)."""
    q = db.query(CreditMemoLine.order_line_id, CreditMemoLine.quantity, CreditMemoLine.unit_price).join(CreditMemo, CreditMemo.id == CreditMemoLine.memo_id) \
        .filter(CreditMemo.status == "issued", CreditMemoLine.order_line_id.isnot(None))
    if order_id:
        q = q.filter(CreditMemo.order_id == order_id)
    from app.services.money import line_amount
    out = {}
    for olid, qty, price in q.all():
        a, b = out.get(olid, (0.0, 0.0))
        out[olid] = (a + (qty or 0), b + line_amount(qty or 0, price or 0))
    return out


def open_credits(db: Session, customer_id: Optional[int] = None) -> List[CreditMemo]:
    """Issued memos with something left to use -- they lower what the customer owes (statements, aging)."""
    q = db.query(CreditMemo).filter(CreditMemo.status == "issued")
    if customer_id:
        q = q.filter(CreditMemo.customer_id == customer_id)
    return [m for m in q.all() if m.remaining > 0.005]
