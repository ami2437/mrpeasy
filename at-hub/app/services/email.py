"""Sends invoices, purchase orders and quotes by SMTP, configured through SMTP_* settings in .env."""
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from html import escape

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.models import Invoice, InvoiceEmail, PurchaseOrder, PurchaseOrderEmail, Quote, QuoteEmail
from app.services.crud import get_company_profile
from app.services.money import line_amount
from app.services.pdf import invoice_pdf, purchase_order_pdf, quote_pdf, money

EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


def from_address() -> str:
    return settings.smtp_from or settings.smtp_username


def is_configured() -> bool:
    return bool(settings.smtp_host and from_address())


def parse_addresses(raw: str, field: str) -> list:
    addrs = [a.strip() for a in re.split(r"[,;]", raw or "") if a.strip()]
    bad = [a for a in addrs if not EMAIL_RE.match(a)]
    if bad:
        raise HTTPException(status_code=400, detail=f"Not a valid email address in {field}: {', '.join(bad)}")
    return addrs


def _invoice_rows(invoice: Invoice) -> list:
    rows = [("Invoice", invoice.code), ("Invoice date", invoice.invoice_date.strftime("%b %d, %Y") if invoice.invoice_date else "")]
    if invoice.due_date:
        rows.append(("Due date", invoice.due_date.strftime("%b %d, %Y")))
    if invoice.printed_payments:  # same as the attached PDF: total, what's been paid, what's left
        rows += [("Invoice total", money(invoice.total)), ("Paid", money(invoice.total - invoice.amount_due_printed))]
    rows.append(("Amount due", money(invoice.amount_due_printed)))
    return rows


def _html_body(body: str, rows: list, company) -> str:
    """The user's message, followed by a small summary card of `rows` (label, value)."""
    paragraphs = "".join(
        f'<p style="margin:0 0 12px;">{escape(block).replace(chr(10), "<br>")}</p>'
        for block in re.split(r"\n\s*\n", body.strip()) if block.strip()
    )
    summary = "".join(
        f'<tr><td style="padding:6px 0;color:#5b6472;">{escape(k)}</td>'
        f'<td style="padding:6px 0;text-align:right;font-weight:600;color:#1b2430;">{escape(v)}</td></tr>'
        for k, v in rows
    )
    return f"""<!doctype html><html><body style="margin:0;padding:24px;background:#f4f5f7;font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1b2430;line-height:1.5;">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:8px;overflow:hidden;border:1px solid #e2e6ed;">
    <div style="background:#1b2430;color:#ffffff;padding:16px 24px;font-size:16px;font-weight:700;">{escape(company.name)}</div>
    <div style="height:3px;background:#2563eb;"></div>
    <div style="padding:24px;">
      {paragraphs}
      <table style="width:100%;border-collapse:collapse;margin-top:8px;border-top:1px solid #e2e6ed;border-bottom:1px solid #e2e6ed;">{summary}</table>
    </div>
  </div>
</body></html>"""


def _send(db: Session, to: str, cc: str, subject: str, body: str, summary_rows: list, attachment=None):
    """Validate, build and send one message; attachment is (bytes, filename) of a PDF.
    Returns the (to_list, cc_list) actually used."""
    if not is_configured():
        raise HTTPException(
            status_code=400,
            detail="Email isn't set up yet. Add SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD (and SMTP_FROM) to the "
                   "AT-HUB .env file and restart the server.",
        )
    to_list = parse_addresses(to, "To")
    cc_list = parse_addresses(cc, "CC") if cc else []
    if not to_list:
        raise HTTPException(status_code=400, detail="Enter at least one recipient")
    if not (subject or "").strip():
        raise HTTPException(status_code=400, detail="Enter a subject")

    company = get_company_profile(db)
    msg = EmailMessage()
    msg["From"] = formataddr((company.name, from_address()))
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    if company.email and company.email.lower() != from_address().lower():
        msg["Reply-To"] = company.email
    msg["Subject"] = subject.strip()
    msg["Message-ID"] = make_msgid(domain=from_address().split("@")[-1])
    msg.set_content(body or "")
    msg.add_alternative(_html_body(body or "", summary_rows, company), subtype="html")
    for att in (attachment if isinstance(attachment, list) else [attachment] if attachment else []):
        # (bytes, filename) is a PDF; (bytes, filename, "image/jpeg") gives the type explicitly
        maintype, subtype = (att[2] if len(att) > 2 and att[2] else "application/pdf").split("/", 1)
        msg.add_attachment(att[0], maintype=maintype, subtype=subtype, filename=att[1])

    try:
        if settings.smtp_security == "ssl":
            server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
        with server:
            if settings.smtp_security == "starttls":
                server.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(msg, to_addrs=to_list + cc_list)
    except smtplib.SMTPAuthenticationError:
        raise HTTPException(status_code=502, detail="The mail server rejected the SMTP username/password (for Gmail, use an App Password)")
    except (smtplib.SMTPException, OSError) as e:
        raise HTTPException(status_code=502, detail=f"Could not send email: {e}")
    return to_list, cc_list


def send_invoice(db: Session, invoice: Invoice, to: str, cc: str, subject: str, body: str,
                 attach_pdf: bool, sent_by: str) -> InvoiceEmail:
    if invoice.status == "void":
        raise HTTPException(status_code=400, detail="This invoice is void -- it can't be emailed")
    from app.services.filenames import invoice_name
    attachment = (invoice_pdf(db, invoice), invoice_name(db, invoice)) if attach_pdf else None
    to_list, cc_list = _send(db, to, cc, subject, body, _invoice_rows(invoice), attachment)
    log = InvoiceEmail(
        invoice_id=invoice.id, to_address=", ".join(to_list), cc_address=", ".join(cc_list) or None,
        subject=subject.strip(), body=body, sent_by=sent_by,
    )
    db.add(log)
    if invoice.status == "draft":
        invoice.status = "sent"  # it's in the customer's inbox now
    db.commit()
    db.refresh(log)
    return log


def send_purchase_order(db: Session, po: PurchaseOrder, to: str, cc: str, subject: str, body: str,
                        attach_pdf: bool, sent_by: str) -> PurchaseOrderEmail:
    """Emails the vendor copy of the PO (vendor part #s only -- never our item numbers).
    A draft PO becomes "ordered" once it has gone out."""
    if po.status == "cancelled":
        raise HTTPException(status_code=400, detail="This purchase order is cancelled -- it can't be emailed")
    if po.status == "validation":
        raise HTTPException(status_code=400, detail=f"{po.code} was quick-captured -- validate it before sending it to the vendor")
    rows = [("Purchase order", po.code), ("Order date", po.order_date.strftime("%b %d, %Y") if po.order_date else "")]
    if po.expected_date:
        rows.append(("Required by", po.expected_date.strftime("%b %d, %Y")))
    rows.append(("Total", money(po.lines_total)))
    attachment = (purchase_order_pdf(db, po, for_vendor=True), f"{po.code}.pdf") if attach_pdf else None
    to_list, cc_list = _send(db, to, cc, subject, body, rows, attachment)
    log = PurchaseOrderEmail(po_id=po.id, to_address=", ".join(to_list), cc_address=", ".join(cc_list) or None,
                             subject=subject.strip(), body=body, sent_by=sent_by)
    db.add(log)
    if po.status == "draft":
        po.status = "ordered"
    db.commit()
    db.refresh(log)
    return log


def send_quote(db: Session, q: Quote, to: str, cc: str, subject: str, body: str,
               attach_pdf: bool, sent_by: str) -> QuoteEmail:
    """Emails the quote to the customer. A draft quote becomes "sent" once it has gone out."""
    rows = [("Quote", q.code), ("Quote date", q.quote_date.strftime("%b %d, %Y") if q.quote_date else "")]
    if q.valid_until:
        rows.append(("Valid until", q.valid_until.strftime("%b %d, %Y")))
    if q.customer_ref:
        rows.append(("Your reference", q.customer_ref))
    rows.append(("Total", money(round(sum(line_amount(l.quantity, l.unit_price) for l in q.lines), 2))))
    attachment = (quote_pdf(db, q), f"Quote-{q.code}.pdf") if attach_pdf else None
    to_list, cc_list = _send(db, to, cc, subject, body, rows, attachment)
    log = QuoteEmail(quote_id=q.id, to_address=", ".join(to_list), cc_address=", ".join(cc_list) or None,
                     subject=subject.strip(), body=body, sent_by=sent_by)
    db.add(log)
    if q.status == "draft":
        q.status = "sent"
    db.commit()
    db.refresh(log)
    return log


def send_pods(db: Session, shipment, files: list, to: str, cc: str, subject: str, body: str, sent_by: str):
    """Email proof-of-delivery files to the customer. files = [(bytes, filename, content_type)]."""
    from app.models import ShipmentEmail
    if not files:
        raise HTTPException(status_code=400, detail="This shipment has no proof of delivery to send")
    rows = [("Shipment", shipment.code)]
    if shipment.delivered_at:
        from app.services.clock import local
        rows.append(("Delivered", local(shipment.delivered_at).strftime("%b %d, %Y")))
    from app.models import CustomerOrder
    order = db.get(CustomerOrder, shipment.order_id)
    if order and order.po_number:
        rows.append(("Your PO #", order.po_number))
    to_list, cc_list = _send(db, to, cc, subject, body, rows, files)
    log = ShipmentEmail(shipment_id=shipment.id, to_address=", ".join(to_list), cc_address=", ".join(cc_list) or None,
                        subject=subject.strip(), files=", ".join(f[1] for f in files), sent_by=sent_by)
    db.add(log)
    db.commit()
    db.refresh(log)
    return log
