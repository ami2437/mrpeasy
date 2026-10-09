"""Customer payment terms and the invoice due date they give.

A customer's terms live on its contact card (details["payment_terms"], picked on the Customers screen); none set =
Net 30. An invoice made without a due date gets invoice date + the customer's terms.

    TERMS                          -> the choices, label -> days
    terms_of(customer)             -> "Net 30"
    due_date_for(customer, day)    -> day + the terms' days
    backfill_due_dates(db)         -> fill every invoice that has no due date (runs at startup; cheap when none)
"""
from datetime import datetime, timedelta
from typing import Optional

TERMS = {"Due on Receipt": 0, "Net 15": 15, "Net 30": 30, "Net 45": 45, "Net 60": 60}
DEFAULT = "Net 30"


def terms_of(customer) -> str:
    t = ((customer.details or {}).get("payment_terms") if customer is not None else None) or DEFAULT
    return t if t in TERMS else DEFAULT


def due_date_for(customer, invoice_date: Optional[datetime]) -> Optional[datetime]:
    if invoice_date is None:
        return None
    return invoice_date + timedelta(days=TERMS[terms_of(customer)])


def backfill_due_dates(db) -> int:
    """Invoices saved without a due date get invoice date + their customer's terms (once each; the rest are set)."""
    from app.models import Customer, Invoice
    rows = db.query(Invoice).filter(Invoice.due_date.is_(None), Invoice.invoice_date.isnot(None)).all()
    if not rows:
        return 0
    customers = {c.id: c for c in db.query(Customer).filter(Customer.id.in_({i.customer_id for i in rows})).all()}
    for inv in rows:
        inv.due_date = due_date_for(customers.get(inv.customer_id), inv.invoice_date)
    db.commit()
    return len(rows)
