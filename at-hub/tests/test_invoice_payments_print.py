"""Print Previous Payments: a part-paid invoice, resent, shows its payments and only the balance as due."""
import io
import re

from pypdf import PdfReader


def _text(pdf_bytes):
    return " ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf_bytes)).pages)


def _part_paid(make, api):
    a = make.item()
    o, sh, inv = make.sold([(a, 10, 10)])                                  # $100.00
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"})
    return api.post(f"/api/invoices/{inv['id']}/payments", json={"amount": 70, "method": "check", "reference": "1234"})


def test_part_paid_invoice_prints_payments_and_balance_by_default(make, api, client, admin_headers):
    inv = _part_paid(make, api)
    assert inv["print_payments"] is not False and inv["amount_due_printed"] == 30
    text = _text(client.get(f"/api/invoices/{inv['id']}/pdf", headers=admin_headers).content)
    assert "Balance due" in text and "-$70.00" in text and "$30.00" in text and "1234" in text


def test_unticked_prints_the_full_total_as_due(make, api, client, admin_headers):
    inv = _part_paid(make, api)
    inv = api.put(f"/api/invoices/{inv['id']}/print-options", json={"print_payments": False})
    assert inv["print_payments"] is False and inv["amount_due_printed"] == 100
    assert inv["print_zero_lines"] is False                                # the other option is left alone
    text = _text(client.get(f"/api/invoices/{inv['id']}/pdf", headers=admin_headers).content)
    assert "Total due" in text and "Balance due" not in text and "-$70.00" not in text


def test_factoring_payments_never_reduce_what_the_customer_owes(make, api, client, admin_headers):
    a = make.item()
    o, sh, inv = make.sold([(a, 10, 10)])
    api.put(f"/api/invoices/{inv['id']}/status", json={"status": "sent"})
    csv = f"Item Number,Disbursement Date,Funding Amount,Discount\n{inv['code']},10/01/2026,95.00,0\n"
    r = client.post("/api/invoice-funding/apply", headers=admin_headers, files={"file": ("f.csv", csv, "text/csv")},
                    data={"record_payments": "true"})
    assert r.status_code == 200, r.text
    inv = api.get(f"/api/invoices/{inv['id']}")
    assert inv["balance"] == 5 and inv["amount_due_printed"] == 100   # the factor paid 95; the customer still owes 100


def test_email_summary_shows_paid_and_amount_due(make, api):
    from app.config.database import SessionLocal
    from app.models import Invoice
    from app.services.email import _invoice_rows
    inv = _part_paid(make, api)
    db = SessionLocal()
    try:
        rows = dict(_invoice_rows(db.get(Invoice, inv["id"])))
    finally:
        db.close()
    assert rows["Invoice total"] == "$100.00" and rows["Paid"] == "$70.00" and rows["Amount due"] == "$30.00"


def test_designed_invoice_template_shows_the_balance(make, api, client, admin_headers):
    inv = _part_paid(make, api)
    for starter in api.get("/api/templates/starters/invoice"):
        r = client.post("/api/templates/preview", headers=admin_headers,
                        json={"doc_type": "invoice", "spec": starter["spec"], "record_id": inv["id"]})
        assert r.status_code == 200, (starter["key"], r.text[:300])
        text = _text(r.content)
        assert "$30.00" in text and "-$70.00" in text, (starter["key"], re.findall(r"-?\$[\d,]+\.\d\d", text))
