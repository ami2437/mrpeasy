"""Read a customer's purchase order PDF into a draft customer order with a private,
self-hosted model (Ollama). Nothing is sent to an outside service: the model URL must be
localhost or a private-network address, and that is checked on every call.

Flow: PDF -> text (pypdf) -> local model returns JSON -> we match the customer and items
against our own records -> the user reviews the draft and creates the order.
"""
import difflib
import io
import ipaddress
import json
import re
import socket
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.models import Customer, StockItem

MAX_TEXT_CHARS = 24000  # keeps the prompt inside a 7B model's context window

EXTRACTION_PROMPT = """You read purchase orders that customers send to our company and turn them into JSON.
Return ONLY a JSON object with exactly these keys:
{
  "customer_name": string|null,        // the company that SENT/ISSUED the PO (the buyer), not us (the vendor/supplier)
  "po_number": string|null,
  "order_date": "YYYY-MM-DD"|null,
  "delivery_date": "YYYY-MM-DD"|null,  // requested ship/delivery/due date for the whole order, if any
  "ship_to_address": string|null,      // full ship-to address, lines joined with newlines
  "notes": string|null,                // special instructions worth keeping (terms, packing, delivery notes)
  "lines": [
    {
      "item_code": string|null,        // the part / item number exactly as printed (our item # if shown, else theirs)
      "customer_item_code": string|null,
      "description": string|null,
      "quantity": number|null,
      "unit": string|null,
      "unit_price": number|null,       // price per single unit; if priced per 100 / per M, convert to per unit
      "delivery_date": "YYYY-MM-DD"|null
    }
  ]
}
Rules: one entry per order line; never invent values -- use null when something isn't on the document;
numbers without $ or commas; skip subtotal, tax, freight and total rows.

Purchase order text:
---
"""


# ---- privacy guard ----
def _is_private_host(host: str) -> bool:
    if not host:
        return False
    if host in ("localhost",) or host.endswith(".local") or host.endswith(".internal") or host.endswith(".lan"):
        return True
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        return False
    return all(ipaddress.ip_address(a.split("%")[0]).is_private or ipaddress.ip_address(a.split("%")[0]).is_loopback
               for a in addresses)


def model_url() -> str:
    url = settings.ai_ollama_url.rstrip("/")
    if not _is_private_host(urlparse(url).hostname or ""):
        raise HTTPException(status_code=400, detail=f"AI_OLLAMA_URL ({url}) is not a local or private-network address. "
                                                    "AT-HUB only uses a self-hosted model so documents stay internal.")
    return url


def status() -> Dict[str, Any]:
    """Is the local model server up and is the configured model pulled?"""
    out = {"url": settings.ai_ollama_url, "model": settings.ai_model, "private": False, "reachable": False,
           "model_installed": False, "installed_models": [], "message": ""}
    try:
        url = model_url()
        out["private"] = True
    except HTTPException as exc:
        out["message"] = exc.detail
        return out
    try:
        resp = httpx.get(f"{url}/api/tags", timeout=4)
        resp.raise_for_status()
        names = [m.get("name", "") for m in resp.json().get("models", [])]
        out["reachable"] = True
        out["installed_models"] = names
        wanted = settings.ai_model if ":" in settings.ai_model else f"{settings.ai_model}:latest"
        out["model_installed"] = wanted in names or settings.ai_model in names
        if not out["model_installed"]:
            out["message"] = f"Ollama is running but the model isn't downloaded. Run: ollama pull {settings.ai_model}"
    except Exception:
        out["message"] = ("No local AI server found. Install Ollama (https://ollama.com/download), "
                          f"then run: ollama pull {settings.ai_model}")
    return out


# ---- PDF -> text ----
def pdf_text(file_bytes: bytes) -> str:
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read the PDF: {exc}")
    text = "\n\n".join(f"[Page {i + 1}]\n{t.strip()}" for i, t in enumerate(pages) if t.strip())
    if len(text.strip()) < 20:
        raise HTTPException(status_code=400, detail="This PDF has no readable text (it's probably a scan/photo). "
                                                    "Scanned POs need OCR first -- try printing it to PDF from the original email or file.")
    return text


# ---- model call ----
def _ask_model(text: str, prompt: str = EXTRACTION_PROMPT, images: Optional[List[str]] = None) -> Dict[str, Any]:
    """Ask the local model for JSON. `images` (base64) switches to the vision model, for scans and photos."""
    url = model_url()
    model = settings.ai_vision_model if images else settings.ai_model
    body = {
        "model": model,
        "prompt": prompt + text[:MAX_TEXT_CHARS] + "\n---",
        "format": "json",
        "stream": False,
        "options": {"temperature": 0, "num_ctx": 16384},
    }
    if images:
        body["images"] = images
    try:
        resp = httpx.post(f"{url}/api/generate", json=body, timeout=settings.ai_timeout_seconds)
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail=status()["message"] or "Local AI server is not reachable")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="The local AI model took too long. Try again (the first run loads the model), or use a smaller model.")
    if resp.status_code == 404:
        raise HTTPException(status_code=503, detail=f"Model '{model}' isn't downloaded. Run: ollama pull {model}")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Local AI error: {resp.text[:300]}")
    raw = resp.json().get("response", "")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            raise HTTPException(status_code=502, detail="The AI didn't return a readable result. Try again.")
        data = json.loads(match.group(0))
    return data if isinstance(data, dict) else {}


# ---- matching against our records ----
def _norm(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _num(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def _date(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _match_customer(db: Session, name: Optional[str]) -> Dict[str, Any]:
    customers = db.query(Customer).filter(Customer.is_active == True).all()  # noqa: E712
    if not name or not customers:
        return {"customer_id": None, "confidence": 0}
    target = _norm(name)
    best, score = None, 0.0
    for c in customers:
        cn = _norm(c.name)
        s = 1.0 if cn == target else (0.92 if cn and (cn in target or target in cn) else difflib.SequenceMatcher(None, cn, target).ratio())
        if s > score:
            best, score = c, s
    return {"customer_id": best.id if best and score >= 0.6 else None, "confidence": round(score, 2),
            "suggested_name": best.name if best else None}


def _match_item(items: List[StockItem], codes: List[Optional[str]], description: Optional[str], matcher=None) -> Dict[str, Any]:
    """Our item for a PO line: certain on an exact code, otherwise ranked by fastener attributes.
    Always returns the top candidates so the user can pick when it isn't sure."""
    from app.services.item_match import ItemMatcher, pick
    candidates = (matcher or ItemMatcher(items)).rank(codes, description)
    item_id = pick(candidates)
    top = candidates[0] if candidates else None
    return {"item_id": item_id, "match": ("code" if top and top["score"] >= 0.999 else "description") if item_id else None,
            "confidence": top["score"] if top else 0, "candidates": candidates}


def extract_order(db: Session, file_bytes: bytes) -> Dict[str, Any]:
    text = pdf_text(file_bytes)
    data = _ask_model(text)
    items = db.query(StockItem).filter(StockItem.is_active == True).all()  # noqa: E712
    from app.services.item_match import ItemMatcher
    matcher = ItemMatcher(items)

    lines = []
    for raw in data.get("lines") or []:
        if not isinstance(raw, dict):
            continue
        qty = _num(raw.get("quantity"))
        if not qty and not raw.get("item_code") and not raw.get("description"):
            continue
        match = _match_item(items, [raw.get("item_code"), raw.get("customer_item_code")], raw.get("description"), matcher)
        lines.append({
            "item_code": raw.get("item_code"),
            "customer_item_code": raw.get("customer_item_code"),
            "description": raw.get("description"),
            "quantity": qty,
            "unit": raw.get("unit"),
            "unit_price": _num(raw.get("unit_price")),
            "delivery_date": _date(raw.get("delivery_date")),
            **match,
        })

    return {
        "model": settings.ai_model,
        "customer_name": data.get("customer_name"),
        "customer": _match_customer(db, data.get("customer_name")),
        "po_number": data.get("po_number"),
        "order_date": _date(data.get("order_date")),
        "delivery_date": _date(data.get("delivery_date")),
        "ship_to_address": data.get("ship_to_address"),
        "notes": data.get("notes"),
        "lines": lines,
        "text_preview": text[:4000],
    }
