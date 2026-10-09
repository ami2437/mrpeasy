"""Optional "Ask Claude": read a hard document with Claude (Anthropic's cloud API).

Only ever runs when a user clicks it on one document. Before anything leaves this machine
the text is redacted locally:
  - our company: name, addresses, facility names, emails, phone (from the company profile + REDACT_TERMS)
  - the bill-to / ship-to / sold-to block (that's us)
  - bank details: account, routing/ABA, SWIFT/IBAN numbers
The PDF itself is never sent -- only the redacted text -- and nothing is saved by this module.
Matching the result to our vendors and items happens back here, locally (see ai_docs).
"""
import json
import re
from typing import Any, Dict, List, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.models import CompanyProfile

MODEL = "claude-opus-5-5"
# Always redacted, on top of the company profile (short forms people actually print).
REDACT_TERMS = ["ATIND SUPPLIES", "ATIND", "AMERICAN TRADERS", "15208 SANTANDER", "SANTANDER DR", "GAINESVILLE"]
BLOCK_HEADS = re.compile(r"^\s*(BILL\s*TO|SHIP\s*TO|SOLD\s*TO|DELIVER\s*TO|CUSTOMER)\s*:?\s*$", re.I)
BANK = re.compile(r"^(\s*(?:bank\s*)?(?:account|acct|routing|aba|swift|iban|bic|wire)[^:\n]*:?\s*)(.+)$", re.I | re.M)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def redact(text: str, db: Session) -> Tuple[str, List[str]]:
    """Return (text safe to send, what was removed). Runs entirely locally."""
    removed = []
    company = db.query(CompanyProfile).first()
    terms = list(REDACT_TERMS)
    if company:
        terms += [company.name or ""] + [l for l in (company.address or "").splitlines() if len(l.strip()) > 4]
        terms += [company.phone or "", company.email or ""]
    our_domains = {e.split("@")[1].lower() for e in EMAIL.findall(" ".join(t for t in terms if t))}

    # 1. the address block under BILL TO / SHIP TO: up to 5 address lines; a blank line or anything that
    #    looks like a document field ("INVOICE # U0003606", "DATE ...", "PO: ...") ends it
    field = re.compile(r"#|:|^\s*(invoice|date|due|terms|p\.?o\.?|order|account|customer|job|ship\s*via|page)\b", re.I)
    lines, out, skip = text.splitlines(), [], 0
    for line in lines:
        if skip:
            if not line.strip() or field.search(line):
                skip = 0
            else:
                skip -= 1
                out.append("[REDACTED: our company / address]") if not out or not out[-1].startswith("[REDACTED") else None
                continue
        out.append(line)
        if BLOCK_HEADS.match(line):
            skip, removed = 5, removed + [f"{line.strip()} block"]
    text = "\n".join(out)

    # 2. bank details
    text, n = BANK.subn(lambda m: m.group(1) + "[REDACTED: bank details]", text)
    if n:
        removed.append(f"{n} bank-detail line(s)")

    # 3. our names / addresses / phone / emails anywhere else (facility names like "Atind West ...")
    for term in sorted({t.strip() for t in terms if t and len(t.strip()) >= 4}, key=len, reverse=True):
        pattern = re.compile(re.escape(term), re.I)
        text, n = pattern.subn("[OUR COMPANY]", text)
        if n:
            removed.append(f"'{term}' x{n}")
    for dom in our_domains:
        text = re.sub(rf"[\w.+-]+@{re.escape(dom)}", "[OUR EMAIL]", text, flags=re.I)

    # 4. card payments: the masked card # and the auth / merchant / reference numbers printed with it
    text, n = CARD_MASKED.subn("[CARD]", text)
    pages, card_lines = [], 0
    for page in re.split(r"(?=\[Page \d+\])", text):
        if CARD_WORDS.search(page) or "[CARD]" in page:  # a masked card # alone is enough to treat it as a card block
            lines, inside = page.splitlines(), False
            for k, line in enumerate(lines):
                if CARD_START.search(line):
                    inside = True
                elif inside and re.match(r"\s*(SUB-?TOTAL|TOTAL\b)", line, re.I):
                    inside = False
                if inside and re.search(r"\d", line) and not re.fullmatch(r"\s*[\d,]+\.\d{2}\s*:?\s*", line):
                    lines[k], card_lines = "[REDACTED: card payment]", card_lines + 1
            page = "\n".join(lines)
        pages.append(page)
    text = "".join(pages)
    if n or card_lines:
        removed.append(f"card payment details ({n + card_lines} line(s))")

    # 5. us as the vendor's customer: our account #, the people who order, every phone number
    account_ids = {m.group(2).strip() for m in OUR_ACCOUNT.finditer(text) if re.fullmatch(r"[\w-]{3,20}", m.group(2).strip())}
    text, n = OUR_ACCOUNT.subn(lambda m: m.group(1) + "[REDACTED: our account #]", text)
    for acct in account_ids:  # some layouts print the value away from its label
        text, k = re.subn(rf"(?<![\w.,]){re.escape(acct)}(?![\w.,])", "[REDACTED: our account #]", text)
        n += k
    if n:
        removed.append(f"{n} customer/account # line(s)")
    from app.models import User
    people = {p for u in db.query(User).all() for p in [u.full_name or ""] + (u.full_name or "").split()[:1] if len(p) >= 3}
    people |= {m.group(2).strip() for m in PERSON.finditer(text)} - {"[EMAIL]", "[PHONE]", "[OUR COMPANY]"}
    for name in sorted(people, key=len, reverse=True):
        text, n = re.subn(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", "[PERSON]", text, flags=re.I)
        if n:
            removed.append(f"person '{name}' x{n}")
    text, n = PHONE.subn("[PHONE]", text)
    if n:
        removed.append(f"{n} phone(s)")
    return text, removed


CARD_MASKED = re.compile(r"[*Xx•]{2,}[\s-]*\d{4}\b")
CARD_WORDS = re.compile(r"card issuer|merchant\s*id|authori[sz]ation\s*(number|amount|code)|\bcard\s*:", re.I)
CARD_START = re.compile(r"accepted by|tran(saction)?\s*type|card\s*(holder|type)?\s*:|merchant|authori[sz]|\[CARD\]", re.I)
OUR_ACCOUNT = re.compile(r"^(\s*(?:customer|cust\.?|client)\s*(?:id|#|no\.?|number)\s*:?\s*)(\S.*)$", re.I | re.M)


# Customer POs: the customer's identity stays here too. Brand words they print that aren't in their customer record.
CUSTOMER_ALIASES = {"Hudson Products": ["CHART INDUSTRIES", "CHARTINDUSTRIES", "CHART", "HUDSON", "HPC"]}
PHONE = re.compile(r"(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s*|\b\d{3}[\s.-])\d{3}[\s.-]\d{4}\b")
URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
# "PO Issued By Andrew Stiles", "QUOTED VIA ANDY 9/17/26", "Buyer: ...", "Attn: ..." -> the person's name
# "PO Issued By Andrew Stiles", "QUOTED VIA ANDY 9/17/26", "Attn: JANE DOE  Ordered By: JANE" -> the person's name.
# Up to 3 words, single-spaced, and never a label word -- so "JANE DOE  Ordered" stops at JANE DOE.
_NAME_WORD = r"(?!(?:by|ordered|attn|attention|phone|fax|email|date|ship|bill|to|customer)\b)[A-Za-z][A-Za-z.'-]*"
PERSON = re.compile(r"((?:issued|ordered|approved|prepared)[ \t]+by[ \t]*:?[ \t]*|taken[ \t]+by[ \t]*:[ \t]*|quoted[ \t]+via[ \t]+"
                    r"|(?:buyer|attn|attention|requisitioner|contact)[ \t]*:[ \t]*)"
                    rf"({_NAME_WORD}(?: {_NAME_WORD}){{0,2}})", re.I)
TERMS_HEADING = re.compile(r"^[^\n]{0,60}\bterms\s*(?:&|and)\s*conditions\b[^\n]{0,100}$", re.I | re.M)


def redact_customer_po(text: str, db: Session) -> Tuple[str, List[str], Any]:
    """A customer's PO with both sides removed: us (as redact()) and the customer -- their names, brand words,
    addresses, phones, emails, web addresses and the people named on it. The customer is identified here,
    locally, before anything is sent; returns (safe text, what was removed, that Customer or None)."""
    from app.models import Customer
    # the T&Cs printed after the order (Chart's run 40 KB) aren't needed to read it
    heading = next((m for m in TERMS_HEADING.finditer(text) if m.start() > 1500), None)
    if heading:
        text = text[:heading.start()]
    upper = text.upper()
    customer = None
    for c in db.query(Customer).all():
        if any(w and len(w) >= 4 and w.upper() in upper for w in [c.name] + CUSTOMER_ALIASES.get(c.name, [])):
            customer = c
            break

    removed = []
    # contact details first, whole, before any name inside them is replaced
    for label, pattern, repl in (("email", EMAIL, "[EMAIL]"), ("web address", URL, "[URL]"), ("phone", PHONE, "[PHONE]")):
        text, n = pattern.subn(repl, text)
        if n:
            removed.append(f"{n} {label}(s)")
    people = {m.group(2).strip() for m in PERSON.finditer(text)} - {"[EMAIL]", "[PHONE]"}
    text, more = redact(text, db)
    removed += more

    terms = list(people)
    if customer:
        c = customer
        terms += [c.name] + CUSTOMER_ALIASES.get(c.name, []) + [c.contact_name or "", c.email or "", c.phone or ""]
        terms += [l for l in (c.address or "").splitlines() + (c.shipping_address or "").splitlines() if len(l.strip()) > 4]
        card = c.details  # every person, phone, email, address and website on their contact card
        for key in ("phones", "emails", "websites"):
            terms += [r.get("value") or "" for r in card[key]]
        terms += [l for r in card["addresses"] for l in (r.get("value") or "").splitlines() if len(l.strip()) > 4]
        terms += [r.get(k) or "" for r in card["people"] for k in ("name", "phone", "email")]
    for term in sorted({t.strip() for t in terms if t and len(t.strip()) >= 3}, key=len, reverse=True):
        label = "[PERSON]" if term in people else "[CUSTOMER]"
        # never inside a part # ("57402-HPC" stays: HPC there is our item suffix, not the customer's name)
        text, n = re.subn(rf"(?<![A-Za-z0-9-]){re.escape(term)}(?![A-Za-z0-9])", label, text, flags=re.I)
        if n:
            removed.append(f"{'person' if term in people else 'customer'} '{term}' x{n}")
    return text, removed, customer


def claude_engine() -> bool:
    """AI_ENGINE=claude: there's no local model here (the cloud server) -- the readers use Claude instead."""
    return (settings.ai_engine or "").strip().lower() == "claude"


SCAN_NOT_SENT = ("This is a scan or photo. Here only text PDFs are read by AI -- scans are never sent out, because their "
                 "contents can't be cleaned first. Type the details in (or ask the sender for a text PDF).")


def claude_engine() -> bool:
    """AI_ENGINE=claude: there's no local model here (the cloud server) -- the readers use Claude instead."""
    return (settings.ai_engine or "").strip().lower() == "claude"


SCAN_NOT_SENT = ("This is a scan or photo. Here only text PDFs are read by AI -- scans are never sent out, because their "
                 "contents can't be cleaned first. Type the details in (or ask the sender for a text PDF).")


def ask_claude(prompt: str, text: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    """One request, structured JSON back. The caller has already redacted `text`."""
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=400, detail="Ask Claude isn't set up: add ANTHROPIC_API_KEY to AT-HUB's .env")
    import anthropic
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # if a safety check declines, the API retries on a suitable model in the same call
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt + text + "\n---"}],
        )
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=502, detail="Anthropic rejected the API key")
    except anthropic.RateLimitError:
        raise HTTPException(status_code=503, detail="Claude is busy (rate limit) -- try again in a minute")
    except anthropic.APIStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Claude error {exc.status_code}: {str(exc)[:200]}")
    except anthropic.APIConnectionError:
        raise HTTPException(status_code=503, detail="Couldn't reach Claude (no internet?)")
    if response.stop_reason == "refusal":
        raise HTTPException(status_code=502, detail="Claude declined to read this document")
    if response.stop_reason == "max_tokens":
        raise HTTPException(status_code=502, detail="Claude's answer was cut off -- the document may be too long")
    raw = next(b.text for b in response.content if b.type == "text")
    usage = response.usage
    return {"data": json.loads(raw), "model": response.model,
            "tokens": {"input": usage.input_tokens, "output": usage.output_tokens}}


# JSON schemas matching the local prompts' shapes (structured outputs guarantee valid JSON)
_n = {"type": ["number", "null"]}
_s = {"type": ["string", "null"]}
_LINE = {"type": "object", "additionalProperties": False,
         "required": ["vendor_item_code", "description", "quantity", "unit_price"],
         "properties": {"vendor_item_code": _s, "description": _s, "quantity": _n, "unit_price": _n}}
SCHEMAS = {
    "vendor_invoice_one": {"type": "object", "additionalProperties": False,
                       "required": ["vendor_name", "invoice_number", "invoice_date", "due_date", "po_number", "lines",
                                    "subtotal", "shipping_handling", "tax", "total", "notes"],
                       "properties": {"vendor_name": _s, "invoice_number": _s, "invoice_date": _s, "due_date": _s, "po_number": _s,
                                      "lines": {"type": "array", "items": _LINE}, "subtotal": _n, "shipping_handling": _n,
                                      "tax": _n, "total": _n, "notes": _s}},
    "vendor_order": {"type": "object", "additionalProperties": False,
                     "required": ["vendor_name", "document_number", "document_date", "expected_date", "lines",
                                  "shipping_handling", "total", "notes"],
                     "properties": {"vendor_name": _s, "document_number": _s, "document_date": _s, "expected_date": _s,
                                    "lines": {"type": "array", "items": _LINE}, "shipping_handling": _n, "total": _n, "notes": _s}},
}
SCHEMAS["customer_po"] = {
    "type": "object", "additionalProperties": False,
    "required": ["po_number", "order_date", "delivery_date", "job_number", "notes", "lines"],
    "properties": {"po_number": _s, "order_date": _s, "delivery_date": _s, "job_number": _s, "notes": _s,
                   "lines": {"type": "array", "items": {
                       "type": "object", "additionalProperties": False,
                       "required": ["item_code", "customer_item_code", "description", "quantity", "unit", "unit_price", "delivery_date"],
                       "properties": {"item_code": _s, "customer_item_code": _s, "description": _s, "quantity": _n,
                                      "unit": _s, "unit_price": _n, "delivery_date": _s}}}},
}
# a file can hold several invoices (one per shipment): always a list
SCHEMAS["vendor_invoice"] = {"type": "object", "additionalProperties": False, "required": ["invoices"],
                             "properties": {"invoices": {"type": "array", "items": SCHEMAS.pop("vendor_invoice_one")}}}
