"""AI_ENGINE=claude (the cloud server, no local model): every reader uses Claude -- text only, details removed first;
scans are refused, never sent. Claude itself is faked here: no network, no cost."""
import io

import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from app.config.settings import settings
from app.services import ai_cloud


def _text_pdf(lines):
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for line in lines:
        c.drawString(40, y, line)
        y -= 16
    c.save()
    return buf.getvalue()


def _scan_pdf():
    buf = io.BytesIO()
    Image.new("RGB", (800, 1000), "white").save(buf, "PDF")
    return buf.getvalue()


@pytest.fixture
def cloud(monkeypatch):
    """Claude mode with a fake Claude that records what it was sent."""
    sent = []
    monkeypatch.setattr(settings, "ai_engine", "claude")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")

    def fake(prompt, text, schema):
        sent.append({"prompt": prompt, "text": text, "schema": schema})
        if "invoices" in schema.get("properties", {}):
            data = {"invoices": [{"vendor_name": None, "invoice_number": "VB-77", "invoice_date": "2026-10-01", "due_date": None,
                                  "po_number": None, "lines": [], "subtotal": None, "shipping_handling": None, "tax": None,
                                  "total": 12.5, "notes": None}]}
        elif "kind" in schema.get("properties", {}):
            data = {"kind": "vendor_invoice"}
        else:
            data = {"po_number": "CLOUD-PO-1", "order_date": None, "delivery_date": None, "job_number": None, "notes": None,
                    "lines": [{"item_code": None, "customer_item_code": None, "description": "Hex bolt", "quantity": 5,
                               "unit": None, "unit_price": 1.0, "delivery_date": None}]}
        return {"data": data, "model": "claude-opus-5-5", "tokens": {"input": 1, "output": 1}}

    monkeypatch.setattr(ai_cloud, "ask_claude", fake)
    return sent


def test_vendor_invoice_text_pdf_goes_to_claude_cleaned(cloud, client, admin_headers):
    company = client.get("/api/company/", headers=admin_headers).json()["name"]
    pdf = _text_pdf([f"INVOICE VB-77 to {company}", "Total 12.50"])
    r = client.post("/api/ai-docs/extract", headers=admin_headers, data={"kind": "vendor_invoice"},  # no engine asked for: local by default
                    files={"file": ("inv.pdf", pdf, "application/pdf")})
    assert r.status_code == 200, r.text[:300]
    assert len(cloud) == 1 and company.upper() not in cloud[0]["text"].upper()   # our name never left
    assert r.json()["model"].startswith("Claude")


def test_scans_are_never_sent(cloud, client, admin_headers):
    r = client.post("/api/ai-docs/extract", headers=admin_headers, data={"kind": "vendor_invoice"},
                    files={"file": ("scan.pdf", _scan_pdf(), "application/pdf")})
    assert r.status_code == 400 and "never sent" in r.json()["detail"] and cloud == []


def test_customer_po_read_by_claude_customer_found_locally(cloud, client, admin_headers, make):
    cust = make.customer(name="Zephyr Fabrication Works")
    pdf = _text_pdf(["Zephyr Fabrication Works", "PURCHASE ORDER CLOUD-PO-1", "5 x Hex bolt @ 1.00"])
    r = client.post("/api/ai-orders/extract", headers=admin_headers, files={"file": ("po.pdf", pdf, "application/pdf")})
    assert r.status_code == 200, r.text[:300]
    assert "ZEPHYR" not in cloud[-1]["text"].upper()                 # the customer's name was removed before sending
    assert r.json()["customer"]["customer_id"] == cust["id"]         # ...and matched here, locally


def test_status_says_claude(cloud, client, admin_headers, monkeypatch):
    s = client.get("/api/ai-orders/status", headers=admin_headers).json()
    assert s["engine"] == "claude" and s["reachable"] and s["model_installed"]
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    s = client.get("/api/ai-orders/status", headers=admin_headers).json()
    assert not s["reachable"] and "no API key" in s["message"]


def test_local_mode_is_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "ai_engine", "local")
    assert ai_cloud.claude_engine() is False
