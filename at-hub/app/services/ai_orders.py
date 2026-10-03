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
  "job_number": string|null,           // project / job number printed on the PO or its lines, if any
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


def _match_item(items: List[StockItem], codes: List[Optional[str]], description: Optional[str], matcher=None,
                codes_are_ours: bool = False) -> Dict[str, Any]:
    """Our item for a PO line: certain on an exact code, otherwise ranked by fastener attributes.
    Always returns the top candidates so the user can pick when it isn't sure.
    codes_are_ours: the customer prints OUR item # (Chart: 53552 / 53552-HPC), so an item # we don't have is a
    new item -- never quietly swapped for a look-alike with the same description."""
    from app.services.item_match import ItemMatcher, pick
    candidates = (matcher or ItemMatcher(items)).rank(codes, description)
    wanted = {(c or "").strip().lower() for c in codes if c}
    exact = next((i.id for i in items if (i.code or "").strip().lower() in wanted), None)
    if candidates and candidates[0]["why"].startswith("learned") and candidates[0]["score"] >= 0.97:
        return {"item_id": candidates[0]["item_id"], "match": "learned", "confidence": candidates[0]["score"], "candidates": candidates}
    if exact:
        return {"item_id": exact, "match": "code", "confidence": 1.0, "candidates": candidates}
    item_id = None if (codes_are_ours and wanted) else pick(candidates)
    top = candidates[0] if candidates else None
    return {"item_id": item_id, "match": "description" if item_id else None,
            "confidence": top["score"] if top else 0, "candidates": candidates}


NUT_SUFFIX = re.compile(r"[\s-]*nuts?$", re.I)
TWO_NUTS = re.compile(r"\(2\)|\b2\s*(?:hvy\s*|heavy\s*)?(?:hex\s*)?nuts\b|\btwo\s+nuts\b|\bdouble\s+nut", re.I)


WITH_NUT = re.compile(r"(?:^|[^a-z])w/.*nuts?(?![a-z])", re.I)  # "_w/A194...NUTS_P-0198": "_" is a word char, so no \b  # "..._W/A194-2H HEX NUT", "w/(2) HVY HEX NUTS"
ASSEMBLED = re.compile(r"assembl", re.I)
MENTIONS_NUT = re.compile(r"nuts?(?![a-z])", re.I)


def nut_history(db: Session) -> Dict[int, List[int]]:
    """Per bolt: [orders that had its nut line, orders that didn't] -- how the team actually entered them."""
    from app.models import CustomerOrder, CustomerOrderLine
    all_items = db.query(StockItem).all()
    items = {i.id: i.code or "" for i in all_items}
    nut_ids = {NUT_SUFFIX.sub("", code).strip().lower(): iid for iid, code in items.items() if NUT_SUFFIX.search(code)}
    # a nut made here (not imported) only counts from when it existed: orders before it couldn't have had it
    since = {i.id: i.created_at for i in all_items if i.mrp_id is None and i.created_at}
    per_order, when = {}, {}
    for order_id, item_id, created in (db.query(CustomerOrderLine.order_id, CustomerOrderLine.item_id, CustomerOrder.created_at)
                                       .join(CustomerOrder, CustomerOrder.id == CustomerOrderLine.order_id).all()):
        per_order.setdefault(order_id, set()).add(item_id)
        when[order_id] = created
    hist = {}
    for order_id, ids in per_order.items():
        for iid in ids:
            nut = nut_ids.get(items.get(iid, "").strip().lower())
            if nut and not NUT_SUFFIX.search(items[iid]):
                if nut not in ids and nut in since and when[order_id] and when[order_id] < since[nut]:
                    continue
                hist.setdefault(iid, [0, 0])[0 if nut in ids else 1] += 1
    return hist


def nut_needed(bolt: StockItem, history: Optional[Dict[int, List[int]]], po_text: str = "") -> Optional[str]:
    """None when the bolt gets a separate $0 nut line, else the reason it doesn't.
    po_text: the PO line's own description + note -- it can say "W/A194-2H HEX NUT" when our title doesn't."""
    from app.services.item_naming import usually_with_nut
    title = f"{bolt.title or ''} {po_text or ''}"
    if ASSEMBLED.search(title):
        return "comes with the nut assembled"
    # what the team did before with this exact bolt beats any reading of its description
    w, wo = (history or {}).get(bolt.id, (0, 0))
    if w + wo:
        return None if w >= wo else f"past orders for it didn't have a separate nut ({w} of {w + wo} did)"
    if not MENTIONS_NUT.search(title) and not usually_with_nut(bolt.title) and not usually_with_nut(po_text):
        return "its description doesn't mention a nut"
    return None


def add_nut_companions(lines: List[Dict[str, Any]], items: List[StockItem], history=None) -> List[Dict[str, Any]]:
    """Our convention: a bolt/stud sold with a nut is followed by its matching nut at $0. The nut is the
    item whose code is the bolt's code + "-NUT"/"-NUTS" (any spacing/case). One nut per bolt, two when
    the bolt is described with (2) nuts. Not added when the nut comes assembled, the bolt isn't sold
    with a nut, or past orders show the team doesn't add one (see nut_needed)."""
    by_id = {i.id: i for i in items}
    nut_for = {}
    for i in items:
        if NUT_SUFFIX.search(i.code or ""):
            nut_for.setdefault(NUT_SUFFIX.sub("", i.code).strip().lower(), i)
    on_po = {l.get("item_id") for l in lines}  # nuts the customer listed themselves are never doubled
    out = []
    for line in lines:
        out.append(line)
        bolt = by_id.get(line.get("item_id"))
        if not bolt or NUT_SUFFIX.search(bolt.code or ""):
            continue
        nut = nut_for.get(bolt.code.strip().lower())
        if not nut or nut.id in on_po or not line.get("quantity"):
            continue
        reason = nut_needed(bolt, history, f"{line.get('description') or ''} {line.get('line_note') or ''}")
        if reason:
            line["nut_skipped"] = f"No $0 nut added for {bolt.code}: {reason}"
            continue
        two = bool(TWO_NUTS.search(f"{bolt.title} {line.get('description') or ''}"))
        out.append({
            "item_code": nut.code, "customer_item_code": None, "description": nut.title,
            "quantity": line["quantity"] * (2 if two else 1), "unit": line.get("unit"), "unit_price": 0.0,
            "delivery_date": line.get("delivery_date"), "item_id": nut.id, "match": "code", "confidence": 1.0,
            "candidates": [], "companion_of": bolt.code,
            "companion_note": f"$0 matching nut for {bolt.code}{' (2 per bolt)' if two else ''}",
        })
    return out


def suggest_new_items(lines: List[Dict[str, Any]], items: List[StockItem], codes_are_ours: bool = False) -> None:
    """For PO lines we have no item for, the item to create (their item #, their description, the PO price) --
    and for a bolt sold with a nut, its $0 nut (<code>-NUT, titled from the bolt's description; see item_naming).
    A bolt we do have but whose nut item is missing gets just the nut suggestion. Nothing is created here."""
    from app.services import item_naming
    codes = {(i.code or "").strip().lower() for i in items}
    has_nut = {NUT_SUFFIX.sub("", i.code).strip().lower() for i in items if NUT_SUFFIX.search(i.code or "")}
    by_id = {i.id: i for i in items}
    for line in lines:
        if line.get("companion_of"):
            continue
        code = (line.get("item_code") or "").strip()
        bolt = by_id.get(line.get("item_id")) if line.get("match") == "code" else None  # only a sure match is "ours"
        # the item keeps the PO's note with its description, as we've always titled them ("..._NUT SHALL BE WAXED DIP")
        full = " ".join(x for x in [(line.get("description") or "").strip(), (line.get("line_note") or "").strip()] if x)
        if not bolt and code and code.lower() not in codes and line.get("description"):
            near = next((c for c in line.get("candidates") or [] if c.get("score", 0) >= 0.85), None)
            line["new_item"] = {"code": code, "title": line["description"].strip() if not line.get("line_note") else full,
                                "category": item_naming.item_category(line["description"]),
                                "selling_price": line.get("unit_price") or 0,
                                # an existing item reads the same: offer it, but don't tick "create" by default
                                "looks_like": near and {"code": near["code"], "score": round(near["score"], 2)},
                                # ticked to create unless the customer's own part # merely reads like an item we have
                                "tick": codes_are_ours or not near}
        # our title plus what this PO line says ("W/A194-2H HEX NUT" may be only on the PO)
        desc = f"{bolt.title} {full}" if bolt else full
        base = bolt.code if bolt else code
        if (not bolt and "new_item" not in line) or not base or base.strip().lower() in has_nut:
            continue
        if item_naming.item_category(bolt.title if bolt else desc) not in ("Bolt", "Stud"):
            continue
        said = WITH_NUT.search(desc) or MENTIONS_NUT.search(desc)
        if not said and not item_naming.usually_with_nut(bolt.title if bolt else desc):
            continue
        nut = item_naming.nut_title(desc)
        if nut["title"] and not said:  # sold with a nut by habit, not because the PO says so
            nut["confidence"], nut["why"] = "check", f"{nut['why']} -- the PO doesn't mention a nut, but these bolts always get one: check"
        if nut["title"]:  # None = assembled: no separate nut
            line["new_nut"] = {"code": item_naming.nut_code(base), "title": nut["title"], "category": "Nut", "selling_price": 0,
                               "per_bolt": nut["per_bolt"], "confidence": nut["confidence"], "why": nut["why"]}


def extract_order(db: Session, file_bytes: bytes) -> Dict[str, Any]:
    from app.services import customer_po_templates
    from app.services.item_match import ItemMatcher
    text = pdf_text(file_bytes)
    # A known layout is read exactly; anything else goes to the local AI model.
    data = customer_po_templates.parse(text)
    source = data["template"] if data else settings.ai_model
    if not data:
        data = _ask_model(text)
    items = db.query(StockItem).filter(StockItem.is_active == True).all()  # noqa: E712
    from app.services import item_alias
    customer = _match_customer(db, data.get("customer_name"))
    matcher = ItemMatcher(items, learned=item_alias.for_party(db, "customer", customer.get("customer_id")))

    lines = []
    for raw in data.get("lines") or []:
        if not isinstance(raw, dict):
            continue
        qty = _num(raw.get("quantity"))
        if not qty and not raw.get("item_code") and not raw.get("description"):
            continue
        code = raw.get("item_code")
        # customers print their part #; ours is often the same with "-HPC" added
        codes = [code, raw.get("customer_item_code"), f"{code}-HPC" if code else None]
        match = _match_item(items, codes, raw.get("description"), matcher, codes_are_ours=bool(data.get("template")))
        lines.append({
            "item_code": code,
            "customer_item_code": raw.get("customer_item_code"),
            "description": raw.get("description"),
            "line_note": raw.get("line_note"),
            "quantity": qty,
            "unit": raw.get("unit"),
            "unit_price": _num(raw.get("unit_price")),
            "delivery_date": _date(raw.get("delivery_date")),
            **match,
        })
    lines = add_nut_companions(lines, items, nut_history(db))
    suggest_new_items(lines, items, codes_are_ours=bool(data.get("template")))

    return {
        "model": source,
        "template": data.get("template"),
        "customer_name": data.get("customer_name"),
        "customer": customer,
        "po_number": data.get("po_number"),
        "order_date": _date(data.get("order_date")),
        "delivery_date": _date(data.get("delivery_date")),
        "job_number": data.get("job_number"),
        "ship_to_address": data.get("ship_to_address"),
        "notes": data.get("notes"),
        "problems": data.get("problems") or [],
        "skipped": data.get("skipped") or [],
        "lines": lines,
        "text_preview": text[:4000],
    }


def missing_nuts(db: Session, order_id: int) -> List[Dict[str, Any]]:
    """For a saved order: bolt lines that should have a $0 nut line under them but don't.
    Each: the nut item to add (existing, or a new one to create first) and how many."""
    from app.models import CustomerOrder
    from app.services import item_naming
    order = db.query(CustomerOrder).filter(CustomerOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    items = db.query(StockItem).all()
    by_id = {i.id: i for i in items}
    nut_for = {}
    for i in items:
        if NUT_SUFFIX.search(i.code or "") and i.is_active is not False:
            nut_for.setdefault(NUT_SUFFIX.sub("", i.code).strip().lower(), i)
    on_order = {l.item_id for l in order.lines}
    history = nut_history(db)
    out = []
    for l in order.lines:
        bolt = by_id.get(l.item_id)
        if not bolt or NUT_SUFFIX.search(bolt.code or "") or item_naming.item_category(bolt.title) not in ("Bolt", "Stud"):
            continue
        nut = nut_for.get(bolt.code.strip().lower())
        if nut and nut.id in on_order:
            continue
        reason = nut_needed(bolt, history)
        if reason:
            continue
        two = bool(TWO_NUTS.search(bolt.title or ""))
        entry = {"bolt_line_id": l.id, "bolt_code": bolt.code, "quantity": l.quantity * (2 if two else 1)}
        if nut:
            entry["nut"] = {"id": nut.id, "code": nut.code, "title": nut.title, "existing": True}
        else:
            named = item_naming.nut_title(bolt.title)
            if not named["title"]:
                continue
            said = WITH_NUT.search(bolt.title or "") or MENTIONS_NUT.search(bolt.title or "")
            entry["nut"] = {"code": item_naming.nut_code(bolt.code), "title": named["title"], "existing": False,
                            "confidence": named["confidence"] if said else "check",
                            "why": named["why"] if said else f"{named['why']} -- the title doesn't mention a nut, but these bolts always get one"}
        out.append(entry)
    return out
