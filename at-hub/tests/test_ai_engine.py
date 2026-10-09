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


# ---- AI_ENGINE=auto: the office PC's Ollama while it's on, Claude while it's off ----
def _pc(monkeypatch, up):
    import time
    monkeypatch.setattr(ai_cloud, "_local_seen", {"at": time.monotonic(), "up": up})   # as if just checked


def test_auto_uses_the_pc_while_it_is_on(cloud, monkeypatch, client, admin_headers):
    monkeypatch.setattr(settings, "ai_engine", "auto")
    _pc(monkeypatch, True)
    from app.services import ai_docs
    local = []
    monkeypatch.setattr(ai_docs, "_ask_model", lambda text, prompt=None, images=None: local.append(bool(images)) or
                        {"invoices": [{"invoice_number": "LOCAL-1", "lines": [], "total": 1}]})
    for pdf in (_text_pdf(["INVOICE LOCAL-1", "Total 1.00"]), _scan_pdf()):   # scans too: they never leave our machines
        r = client.post("/api/ai-docs/extract", headers=admin_headers, data={"kind": "vendor_invoice"},
                        files={"file": ("doc.pdf", pdf, "application/pdf")})
        assert r.status_code == 200, r.text[:300]
    assert local == [False, True] and cloud == []                            # the PC read both; Claude never called
    s = client.get("/api/ai-orders/status", headers=admin_headers).json()
    assert s.get("engine") == "local" and s.get("via") == "pc-link"


def test_auto_falls_back_to_claude_while_the_pc_is_off(cloud, monkeypatch, client, admin_headers):
    monkeypatch.setattr(settings, "ai_engine", "auto")
    _pc(monkeypatch, False)
    r = client.post("/api/ai-docs/extract", headers=admin_headers, data={"kind": "vendor_invoice"},
                    files={"file": ("inv.pdf", _text_pdf(["INVOICE VB-77", "Total 12.50"]), "application/pdf")})
    assert r.status_code == 200 and len(cloud) == 1                          # text: Claude
    r = client.post("/api/ai-docs/extract", headers=admin_headers, data={"kind": "vendor_invoice"},
                    files={"file": ("scan.pdf", _scan_pdf(), "application/pdf")})
    assert r.status_code == 400 and "office PC" in r.json()["detail"] and len(cloud) == 1   # scan: refused, not sent
    s = client.get("/api/ai-orders/status", headers=admin_headers).json()
    assert s["engine"] == "claude" and s["pc_offline"] is True


def test_pc_check_is_cached_and_quick(monkeypatch):
    import httpx
    monkeypatch.setattr(settings, "ai_engine", "auto")
    monkeypatch.setattr(ai_cloud, "_local_seen", {"at": 0.0, "up": False})
    calls = []
    def boom(*a, **k):
        calls.append(k.get("timeout"))
        raise httpx.ConnectError("PC off")
    monkeypatch.setattr(httpx, "get", boom)
    assert ai_cloud.claude_engine() is True and ai_cloud.claude_engine() is True
    assert calls == [1.5]                                                    # one short check, then the cached answer
