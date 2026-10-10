"""Overdue reminders: customers with sent invoices past their due date, the last reminder each got, and one-click
"send reminder" -- an email with their statement attached. Invoicing work: permission "invoices"."""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_perm
from app.models import Customer, Invoice, ReminderLog, User
from app.services import clock
from app.services.money import cents

router = APIRouter(prefix="/api/reminders", tags=["reminders"], dependencies=[Depends(require_perm("invoices"))])
QUIET_DAYS = 7  # a customer reminded this recently isn't suggested again


def overdue_by_customer(db: Session) -> dict:
    """customer_id -> {customer, invoices: [...], amount, oldest_days, last_reminder}"""
    from app.services.terms import due_date_for
    today = clock.today()
    out = {}
    customers = {}
    for inv in db.query(Invoice).filter(Invoice.status == "sent").all():
        if inv.balance <= 0.005:
            continue
        cust = customers.get(inv.customer_id) or db.get(Customer, inv.customer_id)
        customers[inv.customer_id] = cust
        due = inv.due_date or due_date_for(cust, inv.invoice_date or today)
        late = (today.date() - due.date()).days
        if late <= 0:
            continue
        row = out.setdefault(inv.customer_id, {"customer_id": inv.customer_id, "customer": cust.name if cust else "?",
                                               "email": cust.invoice_email if cust else None, "invoices": [], "amount": 0.0, "oldest_days": 0})
        row["invoices"].append({"id": inv.id, "code": inv.code, "due_date": due, "balance": inv.balance, "days_late": late})
        row["amount"] = cents(row["amount"] + inv.balance)
        row["oldest_days"] = max(row["oldest_days"], late)
    for cid, row in out.items():
        last = db.query(ReminderLog).filter(ReminderLog.customer_id == cid).order_by(ReminderLog.sent_at.desc()).first()
        row["last_reminder"] = last.sent_at if last else None
        row["reminder_due"] = not last or last.sent_at < datetime.utcnow() - timedelta(days=QUIET_DAYS)
        row["invoices"].sort(key=lambda i: -i["days_late"])
    return out


@router.get("/overdue")
def overdue(db: Session = Depends(get_db)):
    return sorted(overdue_by_customer(db).values(), key=lambda r: -r["oldest_days"])


@router.get("/draft/{customer_id}")
def draft(customer_id: int, db: Session = Depends(get_db)):
    """The reminder as it would go out: to, subject, body -- edited in the pop-up before sending."""
    row = overdue_by_customer(db).get(customer_id)
    if not row:
        raise HTTPException(status_code=400, detail="This customer has nothing overdue")
    from app.services.crud import get_company_profile
    company = get_company_profile(db)
    cust = db.get(Customer, customer_id)
    lines = "\n".join(f"  {i['code']}  due {i['due_date']:%b %d, %Y}  ({i['days_late']} days late)  ${i['balance']:,.2f}" for i in row["invoices"])
    return {"to": row["email"] or "", "cc": "",
            "subject": f"Payment reminder -- {len(row['invoices'])} overdue invoice{'s' if len(row['invoices']) != 1 else ''} (${row['amount']:,.2f})",
            "body": f"Hello {cust.contact_name or cust.name},\n\nOur records show the following invoice{'s are' if len(row['invoices']) != 1 else ' is'} "
                    f"past due:\n\n{lines}\n\nTotal overdue: ${row['amount']:,.2f}\n\nYour statement is attached. If payment is already on its way, "
                    f"thank you -- please disregard this note. Otherwise we'd appreciate payment at your earliest convenience.\n\n"
                    f"Thank you,\n{company.name or ''}",
            **row}


class SendIn(BaseModel):
    from_id: Optional[int] = None  # the From address picked (Company Settings -> Email); none = the default for this kind
    to: str
    cc: Optional[str] = None
    subject: str
    body: str


@router.post("/send/{customer_id}")
def send(customer_id: int, data: SendIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """Email the reminder with the customer's statement attached, and log it."""
    from app.routes.analytics import build_statement
    from app.services import email as email_service
    row = overdue_by_customer(db).get(customer_id)
    if not row:
        raise HTTPException(status_code=400, detail="This customer has nothing overdue")
    pdf, name = build_statement(db, customer_id)
    rows = [("Overdue invoices", ", ".join(i["code"] for i in row["invoices"])), ("Total overdue", f"${row['amount']:,.2f}")]
    from app.models import Customer
    cust = db.get(Customer, customer_id)
    to_list, _ = email_service._send(db, data.to, data.cc or "", data.subject, data.body, rows, (pdf, name), kind="statement",
                                     sender_id=data.from_id, party=cust, record=("customer", customer_id, cust.name if cust else ""),
                                     sent_by=user.username)
    db.add(ReminderLog(customer_id=customer_id, invoice_codes=", ".join(i["code"] for i in row["invoices"]), amount=row["amount"],
                       to_address=", ".join(to_list), sent_by=user.username))
    db.commit()
    return {"ok": True, "to": to_list}
