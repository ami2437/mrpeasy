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
    return text, removed


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
    "vendor_invoice": {"type": "object", "additionalProperties": False,
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
