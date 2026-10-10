"""AI Desk: drop files, get one obvious next step for each -- and it always works.

    identify(db, text, name, instruction)   names first (no AI, instant): which of OUR vendors / customers / records the
                                            document names, which side it's on (we buy / we sell) and what kind it is
    read(db, row, instruction, kind)        the AI read for that kind (customer PO, vendor SO, invoice, POD...), once
    plan(db, user, row)                     what to offer: one sentence, the facts, ONE primary action, other options,
                                            to-dos (never blockers) -- recomputed whenever the desk is shown
    act(db, user, row, a)                   do it: drafts are made in Validation (check them later), files attached

Rules: the desk never refuses. Anything unsure becomes a to-do on the record it creates ("pick the item for line 2"),
not a reason to stop; the only thing it may need from you is who the document is from.
"""
import json
import re
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import (CompanyProfile, Customer, CustomerOrder, DeskFile, Invoice, PurchaseOrder, Quote, QuoteLine, Shipment,
                        StockItem, User, Vendor, VendorBill)
from app.services import doc_text

KINDS = {"customer_po": "Customer PO", "rfq": "Customer RFQ / quote request", "vendor_order": "Vendor sales order / quote / confirmation",
         "vendor_invoice": "Vendor invoice", "mtr": "Material test report (MTR)", "pod": "Proof of delivery", "other": "Other document"}
SHORT = {"customer_po": "Customer PO", "rfq": "Customer RFQ", "vendor_order": "Vendor SO / Quote", "vendor_invoice": "Vendor Invoice",
         "mtr": "MTR", "pod": "Proof Of Delivery", "other": "Document"}
SIDE = {"customer_po": "customer", "rfq": "customer", "pod": "customer", "vendor_order": "vendor", "vendor_invoice": "vendor", "mtr": "vendor"}
ATTACH_CATEGORY = {"customer_po": "customer_po", "rfq": "customer_rfq", "vendor_invoice": "vendor_invoice", "vendor_order": "vendor_quote",
                   "mtr": "mtr", "pod": "pod", "other": "other"}
RECORD_WORD = {"customer_order": "order", "purchase_order": "PO", "shipment": "shipment", "quote": "quote"}

# words that say what a document is (counted in its first page and its file name)
SIGNALS = {
    "invoice": [r"\binvoice\s*(no|#|number|num|date)\b", r"\b(amount|balance|total)\s*due\b", r"\bremit\s*(to|payment)\b", r"^\s*invoice\b",
                r"\binv\b", r"\binvoice\b"],
    "order": [r"\bsales\s*order\b", r"\border\s*(acknowledg|confirmat)", r"\bquotation\b", r"\bquote\s*(no|#|number)?\b", r"\bpro\s*-?\s*forma\b",
              r"\bestimated\s*ship", r"\bso\s*(no|#|number)\b", r"\bsales_order\b"],
    "customer_po": [r"\bpurchase\s*order\b", r"\bp\.?\s*o\.?\s*(no|#|number)\b"],
    "rfq": [r"\brequest\s*for\s*(a\s*)?(quote|quotation|pricing)\b", r"\brfq\b", r"\bplease\s*quote\b"],
    "mtr": [r"mill\s*test", r"test\s*report", r"material\s*cert", r"certificate\s*of\s*(conformance|compliance|analysis)", r"heat\s*(no|number|#)",
            r"\bmtr\b", r"\bcmtr\b"],
    "pod": [r"proof\s*of\s*delivery", r"delivery\s*receipt", r"received\s*in\s*good\s*(order|condition)", r"consignee\s*signature",
            r"\bpod\b", r"bill\s*of\s*lading"],
}
HINTS = [("customer_po", r"customer\s*po|customer\s*order|create\s+(the\s+)?(sales\s+)?orders?\b"),
         ("rfq", r"\brfq\b|request\s*for\s*quote|quote\s*request|make\s+(a\s+)?quotes?"),
         ("vendor_invoice", r"vendor\s*invoice|supplier\s*invoice|\bbills?\b|invoices?"),
         ("vendor_order", r"vendor\s*(so|order|quote|confirmation)|order\s*confirmation|\bproforma\b|create\s+(the\s+)?(purchase\s+orders?|pos?)\b"),
         ("mtr", r"\bmtrs?\b|mill\s*test|test\s*reports?|certs?\b"), ("pod", r"\bpods?\b|proof\s*of\s*delivery|delivery\s*receipts?")]


def _norm(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _now():
    return datetime.utcnow()


# ---------------------------------------------------------------------------------------------------- names first
def _ours(db: Session) -> set:
    from app.services.ai_cloud import REDACT_TERMS
    company = db.query(CompanyProfile).first()
    return {_norm(t) for t in REDACT_TERMS + [company.name if company else ""] if t and len(_norm(t)) >= 5}


def _distinct_words(name: str):
    return [w for w in re.split(r"[^a-z0-9]+", (name or "").lower())
            if len(w) >= 3 and w not in ("inc", "llc", "ltd", "corp", "company", "the", "and", "co", "supply", "supplies", "industrial",
                                         "industries", "group", "usa", "intl", "international", "services", "of")]


def _party_score(p, flat: str, low: str, digits: str, fname: str = ""):
    """How sure we are this customer / vendor is named on the document: (score, why). fname: the file name in words --
    someone named the file, so the first word of a name there is enough ("Ziegler_Sales_Order_1980403.pdf")."""
    s, why = 0.0, ""
    pn = _norm(p.name)
    words = _distinct_words(p.name)
    if pn and len(pn) >= 5 and pn in flat:
        s, why = 0.95, "its name is on the document"
    elif words and len(words[0]) >= 5 and re.search(rf"\b{re.escape(words[0])}\b", low) \
            and (len(words) == 1 or re.search(rf"\b{re.escape(words[1])}\b", low)):
        s, why = 0.9, "its name is on the document"
    elif words and len(words[0]) >= 6 and re.search(rf"\b{re.escape(words[0])}\b", fname):
        s, why = 0.88, "its name is in the file name"
    try:
        card = p.details
    except Exception:
        card = {"phones": [], "emails": [], "websites": [], "people": []}
    phones = re.split(r"[,;/]", p.phone or "") + [r.get("value") or "" for r in card.get("phones", [])] + [x.get("phone") or "" for x in card.get("people", [])]
    emails = re.split(r"[,;\s]+", p.email or "") + [r.get("value") or "" for r in card.get("emails", [])] + [x.get("email") or "" for x in card.get("people", [])]
    sites = [r.get("value") or "" for r in card.get("websites", [])]
    for ph in phones:
        d = re.sub(r"\D", "", ph)[-10:]
        if len(d) >= 10 and d in digits:
            s, why = max(s, 0.97), "its phone number is on the document"
    for em in emails:
        dom = em.split("@")[-1].lower() if "@" in em else ""
        if dom and "." in dom and dom not in ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "aol.com", "icloud.com") and dom in low:
            s, why = max(s, 0.97), "its email domain is on the document"
    for site in sites:
        site = re.sub(r"^(https?://)?(www\.)?", "", site.lower()).split("/")[0]
        if site and "." in site and site in low:
            s, why = max(s, 0.97), "its website is on the document"
    return s, why


def _part_hits(db: Session, text: str) -> dict:
    """vendor id -> their own part #s printed on the document (learned from past POs). A vendor whose name is only in
    its logo (a picture -- Ziegler's sales orders and quotes) is still known by its part #s."""
    from app.models import VendorItem
    tokens = {t.upper() for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9/.\-]{4,}", text or "")}
    hits = {}
    for vi in db.query(VendorItem).all():
        code = (vi.vendor_item_code or "").strip().upper()
        if len(code) >= 5 and not code.isdigit() and code in tokens:
            hits.setdefault(vi.vendor_id, set()).add(code)
    return hits


def _rank(db: Session, model, text: str, name: str, ours: set):
    fname = re.sub(r"[_\-.]+", " ", name or "")  # "Ziegler_Sales_Order" -> "Ziegler Sales Order": words, not one blob
    low, flat, digits = f"{text} {fname}".lower(), _norm(f"{text} {fname}"), re.sub(r"\D", "", text or "")
    parts = _part_hits(db, text) if model is Vendor else {}
    ranked = []
    for p in db.query(model).filter(model.is_active == True).all():  # noqa: E712
        if _norm(p.name) in ours:
            continue  # ourselves
        s, why = _party_score(p, flat, low, digits, fname.lower())
        if parts.get(p.id):
            hits = sorted(parts[p.id])
            s2 = 0.9 if len(hits) == 1 else 0.95
            if s2 > s:
                s, why = s2, f"its part # {', '.join(hits[:3])} {'is' if len(hits) == 1 else 'are'} on the document"
        if s >= 0.85:
            ranked.append({"id": p.id, "name": p.name, "score": round(s, 2), "why": why})
    ranked.sort(key=lambda r: -r["score"])
    return ranked


def _sure(ranked):
    if not ranked:
        return None
    top = ranked[0]
    if len(ranked) == 1 or top["score"] - ranked[1]["score"] >= 0.05:
        return top
    return None


def _refs(db: Session, text: str, name: str) -> dict:
    """Numbers of OUR records printed on the document: our PO / order / shipment / invoice codes, a customer PO # we
    have on an order, a vendor SO # we have on a PO."""
    from app.routes.file_matcher import _name_keys
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9/\-]*", f"{name} {(text or '')[:30000]}")
    keys = _name_keys(" ".join(words)) | {_norm(w) for w in words}
    out = {"po": [], "order": [], "shipment": [], "invoice": [], "cust_po": [], "vendor_so": []}

    def hit(val):
        k = _norm(val)
        return len(k) >= 5 and k in keys

    for p in db.query(PurchaseOrder).filter(PurchaseOrder.status != "cancelled").all():
        if hit(p.code):
            out["po"].append({"id": p.id, "code": p.code, "party": p.vendor_id})
        elif p.vendor_so_number and hit(p.vendor_so_number):
            out["vendor_so"].append({"id": p.id, "code": p.code, "party": p.vendor_id, "number": p.vendor_so_number})
    for o in db.query(CustomerOrder).filter(CustomerOrder.status != "cancelled").all():
        if hit(o.code):
            out["order"].append({"id": o.id, "code": o.code, "party": o.customer_id})
        elif o.po_number and hit(o.po_number):
            out["cust_po"].append({"id": o.id, "code": o.code, "party": o.customer_id, "number": o.po_number})
    for s in db.query(Shipment).filter(Shipment.status != "cancelled").all():
        if hit(s.code):
            out["shipment"].append({"id": s.id, "code": s.code})
    for i in db.query(Invoice).all():
        if hit(i.code):
            out["invoice"].append({"id": i.id, "code": i.code})
    return out


def _signals(text: str, name: str) -> dict:
    head = (text or "")[:3500].lower()
    fname = re.sub(r"[_\-.]+", " ", (name or "").lower())
    out = {}
    for k, pats in SIGNALS.items():
        n = 0
        for p in pats:
            n += 2 * len(re.findall(p, head, re.M)[:3]) + 3 * bool(re.search(p, fname))
        out[k] = n
    return out


def identify(db: Session, text: str, name: str, instruction: str = "") -> dict:
    """Who and what, from the words alone (no AI). side: "vendor" (we buy) | "customer" (we sell) | None."""
    ours = _ours(db)
    vendors, customers = _rank(db, Vendor, text, name, ours), _rank(db, Customer, text, name, ours)
    v, c = _sure(vendors), _sure(customers)
    refs = _refs(db, text, name)
    sig = _signals(text, name)
    side, why = None, ""
    kind = next((k for k, pat in HINTS if instruction and re.search(pat, instruction, re.I)), None)
    if kind:
        side, why = SIDE.get(kind), "you said so"
    elif refs["po"] or refs["vendor_so"]:
        r = (refs["po"] or refs["vendor_so"])[0]
        side, why = "vendor", f"our {r['code']} is on it"
        if not v:
            vv = db.get(Vendor, r["party"])
            v = {"id": vv.id, "name": vv.name, "score": 0.9, "why": f"{r['code']} is theirs"} if vv else None
    elif v and not c:
        side, why = "vendor", f"{v['name']} is one of our vendors ({v['why']})"
    elif c and not v:
        side, why = "customer", f"{c['name']} is one of our customers ({c['why']})"
    elif v and c:
        side = "vendor" if v["score"] > c["score"] else "customer" if c["score"] > v["score"] else None
        why = "both a vendor and a customer are named on it" + (f" -- {(v if side == 'vendor' else c)['name']} more clearly" if side else "")
    elif refs["shipment"] or refs["order"] or refs["cust_po"]:
        side, why = "customer", "one of our orders / shipments is on it"
    if not kind:
        if sig["mtr"] >= 4 and sig["mtr"] > max(sig["invoice"], sig["order"], sig["customer_po"]):
            kind = "mtr"
        elif side == "vendor":
            kind = "vendor_invoice" if sig["invoice"] > sig["order"] + 1 else "vendor_order"
        elif side == "customer":
            if sig["pod"] >= 4 and (refs["shipment"] or refs["order"] or sig["pod"] > sig["customer_po"]):
                kind = "pod"
            elif sig["rfq"] >= 3 and sig["rfq"] >= sig["customer_po"]:
                kind = "rfq"
            else:
                kind = "customer_po"
        elif sig["pod"] >= 4 and (refs["shipment"] or refs["order"]):
            kind, side = "pod", "customer"
    return {"side": side, "why": why, "kind": kind, "vendor": v, "customer": c, "vendors": vendors[:5], "customers": customers[:5],
            "refs": refs, "signals": sig}


def _classify_ai(db: Session, data: bytes, name: str, text: str) -> Optional[str]:
    """Last resort: ask the model what kind of document it is."""
    try:
        from app.routes.ai_desk import CLASSIFY
        from app.services.ai_orders import _ask_model
        from app.services import ai_cloud
        if ai_cloud.claude_engine():
            if not text.strip():
                return None
            safe, _r = ai_cloud.redact(text[:6000], db)
            k = ai_cloud.ask_claude(CLASSIFY, safe, {"type": "object", "additionalProperties": False, "required": ["kind"],
                                                     "properties": {"kind": {"type": "string", "enum": sorted(KINDS)}}})["data"].get("kind")
        else:
            images = None
            if not text.strip() and doc_text.family(name) in ("pdf", "image"):
                from app.services.ai_docs import _read
                images = (_read(data, name).get("images") or [])[:1] or None
            k = (_ask_model(text[:6000] or name, prompt=CLASSIFY, images=images) or {}).get("kind")
        return k if k in KINDS else None
    except Exception:
        return None


# ---------------------------------------------------------------------------------------------------- the read
LABEL_WORDS = re.compile(r"^(?:(?:sales|purchase|customer|vendor|order|orders|so|po|p\.o\.?|quote|quotation|invoice|inv|confirmation|"
                         r"acknowledg(?:e)?ment|number|num|no\.?|nbr|ref|reference)(?=[\s.:#]|$)[\s.:#]*|[#:.\s-]+)+", re.I)


def clean_number(v):
    """ "SALES ORDER 1964913" / "PO #: 4512998" -> the number itself (what the AI sometimes reads with its label)."""
    if not isinstance(v, str) or not v.strip():
        return v
    out = LABEL_WORDS.sub("", v.strip()).strip(" .:#-")
    return out if out and re.search(r"\d", out) else v.strip()


def _jsonable(d):
    return json.loads(json.dumps(d, default=str))


def read(db: Session, row: DeskFile, data: bytes, instruction: str = "", kind: str = "") -> dict:
    """Names first, then the AI read for that kind. Returns the result JSON stored on the row."""
    name = row.filename
    text = doc_text.text_of(data, name)
    ident = identify(db, text, name, instruction)
    how = ident["why"]
    if kind in KINDS:
        how = "you picked it"
    else:
        kind = ident["kind"]
        sig = ident.get("signals") or {}
        nothing = not ident.get("side") and not any((ident.get("refs") or {}).values()) and not any(sig.values())
        if not kind and text.strip() and nothing:
            kind, how = "other", "no names, numbers or document words of ours in it"  # a note, not a business document
        if not kind:
            from app.services import customer_po_templates
            if text and customer_po_templates.parse(text):
                kind, how = "customer_po", "a customer PO layout we know"
            else:
                kind = _classify_ai(db, data, name, text) or "other"
                how = "AI" if kind != "other" else "couldn't tell"
    result = {"kind": kind, "how": how, "ident": ident, "draft": None, "read_error": None, "family": doc_text.family(name),
              "has_text": bool(text.strip())}
    fam = doc_text.family(name)
    use_text = None if fam in ("pdf", "image") else text
    try:
        if kind in ("customer_po", "rfq"):
            if fam not in ("pdf",) and not text.strip():
                raise HTTPException(status_code=400, detail="This file has no text to read (a scan or photo): create it and fill the lines in by hand")
            from app.services import ai_orders
            d = ai_orders.extract_order(db, data, text=use_text if fam != "pdf" else None)
            result["draft"] = d
        elif kind in ("vendor_order", "vendor_invoice"):
            from app.services import ai_docs
            if fam not in ("pdf", "image") and not text.strip():
                raise HTTPException(status_code=400, detail="This file has no text to read")
            d = ai_docs.extract(db, kind, data, name, text=use_text)
            result["draft"] = d
        elif kind == "pod":
            from app.services import ai_docs
            if fam in ("pdf", "image"):
                result["draft"] = ai_docs.extract(db, "pod", data, name)
    except HTTPException as e:
        result["read_error"] = str(e.detail)
    except Exception as e:  # the AI being offline etc. must never lose the file
        result["read_error"] = f"Couldn't read it ({type(e).__name__}) -- you can still create it and fill it in"
    if result["draft"]:
        result["draft"].pop("text_preview", None)
        for k in ("document_number", "invoice_number", "po_number"):
            if result["draft"].get(k):
                result["draft"][k] = clean_number(result["draft"][k])
        # the number in the file name ("Ziegler_Sales_Order_1980403.pdf") beats a read that's missing, a page # ("1")
        # or the same number with letters stuck on ("ZD1980403")
        key = "invoice_number" if kind == "vendor_invoice" else "po_number" if kind in ("customer_po", "rfq") else "document_number"
        m = re.findall(r"(?<!\d)(\d{5,})(?!\d)", re.sub(r"[_\-.]+", " ", name))
        if m and kind != "pod":
            fn, got = m[-1], str(result["draft"].get(key) or "")
            if not got or len(re.sub(r"\W", "", got)) < 4 or (fn in got and got != fn and not re.search(rf"\b{fn}\b", got)):
                result["draft"][key] = fn
    return _jsonable(result)


# ---------------------------------------------------------------------------------------------------- the plan
def _can(user: User, perm: str) -> bool:
    from app.services.permissions import has
    return has(user, perm)


def _party(db: Session, res: dict, side: str):
    """The customer / vendor: the read's, else the names-first one. {id, name, why} or None."""
    d, ident = res.get("draft") or {}, res.get("ident") or {}
    if side == "customer":
        cid = (d.get("customer") or {}).get("customer_id")
        if cid and _norm((db.get(Customer, cid) or Customer(name="")).name) not in _ours(db):
            c = db.get(Customer, cid)
            return {"id": c.id, "name": c.name, "why": "read on the document"}
        return ident.get("customer")
    vid = (d.get("vendor") or {}).get("vendor_id")
    if vid:
        v = db.get(Vendor, vid)
        return {"id": v.id, "name": v.name, "why": (d.get("vendor") or {}).get("why") or "read on the document"}
    return ident.get("vendor")


def _money(v):
    return f"${v:,.2f}" if isinstance(v, (int, float)) else None


def _open_pos(db: Session, vendor_id: int):
    return [p for p in db.query(PurchaseOrder).filter(PurchaseOrder.vendor_id == vendor_id,
                                                      PurchaseOrder.status.notin_(("cancelled",))).order_by(PurchaseOrder.id.desc()).all()]


def _find_po(db: Session, res: dict, vendor_id: Optional[int]):
    """The PO a vendor document belongs to: our PO # on it, else its SO # on one of our POs. (po, why) or (None, None)."""
    ident, d = res.get("ident") or {}, res.get("draft") or {}
    refs = ident.get("refs") or {}
    cands = [r for r in refs.get("po", []) if not vendor_id or r["party"] == vendor_id] or refs.get("po", [])
    if len(cands) == 1:
        return db.get(PurchaseOrder, cands[0]["id"]), f"our {cands[0]['code']} is printed on it"
    num = d.get("po_number")
    if num:
        po = db.query(PurchaseOrder).filter(PurchaseOrder.code == num.strip()).first()
        if po:
            return po, f"it says it's for {po.code}"
    so = d.get("document_number")
    if so and vendor_id:
        from app.services.crud import PurchaseOrderService
        po = PurchaseOrderService.duplicate_so(db, vendor_id, so)
        if po:
            return po, f"SO {so} is already on {po.code}"
    sos = [r for r in refs.get("vendor_so", []) if not vendor_id or r["party"] == vendor_id]
    if len(sos) == 1:
        return db.get(PurchaseOrder, sos[0]["id"]), f"SO {sos[0]['number']} on it is on {sos[0]['code']}"
    return None, None


def _lines_todo(d: dict, where: str):
    lines = [l for l in (d.get("lines") or []) if not l.get("companion_of")]
    unsure = [l for l in lines if not l.get("item_id")]
    out = []
    if d is not None and not lines:
        out.append(f"No lines read — add them on the {where}")
    elif unsure:
        out.append(f"{len(unsure)} of {len(lines)} line{'s' if len(lines) != 1 else ''} need their item picked — you'll do that on the {where}")
    return out


def plan(db: Session, user: User, row: DeskFile) -> dict:
    res = json.loads(row.result or "{}")
    kind = res.get("kind") or "other"
    d = res.get("draft") or {}
    ident = res.get("ident") or {}
    side = SIDE.get(kind) or ident.get("side")
    out = {"kind": kind, "kind_label": KINDS.get(kind, "Document"), "kind_short": SHORT.get(kind, "Document"), "how": res.get("how") or "",
           "side": side, "party": None, "sentence": "", "facts": [], "primary": None, "options": [], "todo": [], "warnings": [],
           "bill": None, "read_error": res.get("read_error"), "family": res.get("family")}
    if res.get("read_error"):
        out["todo"].append(res["read_error"])
    # what was picked by hand vs what the names say
    if res.get("how") == "you picked it":
        other = ident.get("vendor") if SIDE.get(kind) == "customer" else ident.get("customer") if SIDE.get(kind) == "vendor" else None
        mine = ident.get("customer") if SIDE.get(kind) == "customer" else ident.get("vendor") if SIDE.get(kind) == "vendor" else None
        if other and not mine:
            word = "vendors" if SIDE.get(kind) == "customer" else "customers"
            out["warnings"].append(f"You picked {SHORT[kind]}, but {other['name']} is one of our {word} — is it really a {SHORT[kind].lower()}?")
    party = _party(db, res, side) if side else None
    out["party"] = party
    pname = party["name"] if party else None
    can = {k: _can(user, p) for k, p in (("orders", "orders.edit"), ("purchasing", "purchasing"), ("quotes", "quotes"),
                                         ("ship", "shipments.work"), ("money", "money.view"))}

    def act(action, label, **kw):
        return {"action": action, "label": label, **kw}

    primary, options = None, []
    if kind == "customer_po":
        po = d.get("po_number")
        found = (ident.get("refs") or {}).get("cust_po") or []
        if not party and len(found) == 1:  # a customer PO # we already have on an order: that order's customer
            c = db.get(Customer, found[0]["party"])
            party = out["party"] = {"id": c.id, "name": c.name, "why": f"PO {found[0]['number']} is on {found[0]['code']}"}
            pname = c.name
            po = po or found[0]["number"]
        lines = [l for l in d.get("lines") or [] if not l.get("companion_of")]
        total = sum((l.get("quantity") or 0) * (l.get("unit_price") or 0) for l in d.get("lines") or [])
        out["sentence"] = f"Customer PO from {pname}" if pname else "A customer PO — from a customer we don't have yet"
        out["facts"] = [x for x in [f"PO {po}" if po else None, f"{len(lines)} line{'s' if len(lines) != 1 else ''}" if d else None,
                                    _money(total) if total and can["money"] else None, f"Job {d['job_number']}" if d.get("job_number") else None,
                                    f"Wanted {d['delivery_date'][:10]}" if d.get("delivery_date") else None] if x]
        dup = None
        if po and party:
            dup = next((o for o in db.query(CustomerOrder).filter(CustomerOrder.customer_id == party["id"], CustomerOrder.status != "cancelled").all()
                        if (o.po_number or "").strip().lower() == po.strip().lower()), None)
        if dup:
            out["todo"].append(f"PO {po} is already on order {dup.code}")
            primary = act("attach", f"Attach To {dup.code}", record={"type": "customer_order", "id": dup.id, "code": dup.code},
                          why=f"PO {po} is already on it", perm=can["orders"])
            options.append(act("create_order", "Create Another Order Anyway", party=party, allow_duplicate=True, perm=can["orders"]))
        else:
            out["todo"] += _lines_todo(d, "order") if d else []
            primary = act("create_order", f"Create Draft Order For {pname}" if pname else "Create Draft Order…", party=party,
                          needs=None if party else "customer", perm=can["orders"])
        if not d.get("po_number") and d:
            out["todo"].append("No PO # read — add it on the order")
        options += [act("create_quote", "Create A Draft Quote Instead", party=party, needs=None if party else "customer", perm=can["quotes"])]
    elif kind == "rfq":
        lines = [l for l in d.get("lines") or [] if not l.get("companion_of")]
        out["sentence"] = f"Request for a quote from {pname}" if pname else "A request for a quote — from a customer we don't have yet"
        out["facts"] = [x for x in [f"Ref {d['po_number']}" if d.get("po_number") else None, f"{len(lines)} line{'s' if len(lines) != 1 else ''}" if d else None] if x]
        out["todo"] += ([f"{sum(1 for l in lines if not l.get('item_id'))} line(s) aren't items we know — they go on the quote as text"]
                        if any(not l.get("item_id") for l in lines) else [])
        primary = act("create_quote", f"Create Draft Quote For {pname}" if pname else "Create Draft Quote…", party=party,
                      needs=None if party else "customer", perm=can["quotes"])
        options.append(act("create_order", "Create A Draft Order Instead", party=party, needs=None if party else "customer", perm=can["orders"]))
    elif kind in ("vendor_order", "vendor_invoice", "mtr"):
        po, po_why = _find_po(db, res, party["id"] if party else None)
        if po and not party:
            v = db.get(Vendor, po.vendor_id)
            party = out["party"] = {"id": v.id, "name": v.name, "why": po_why}
            pname = v.name
        if kind == "vendor_order":
            so, lines = d.get("document_number"), d.get("lines") or []
            out["sentence"] = f"{'Sales order / quote' if not d.get('total') else 'Sales order'} from {pname}" if pname else "A vendor's sales order / quote — from a vendor we don't have yet"
            out["facts"] = [x for x in [f"SO {so}" if so else None, f"{len(lines)} line{'s' if len(lines) != 1 else ''}" if d else None,
                                        _money(d.get("total")) if can["money"] else None, f"Ships {d['expected_date'][:10]}" if d.get("expected_date") else None] if x]
            if po:
                lbl = f"Attach To {po.code}" + (f" And Save SO # {so}" if so and not po.vendor_so_number else "")
                primary = act("attach", lbl, record={"type": "purchase_order", "id": po.id, "code": po.code}, why=po_why, perm=can["purchasing"])
                options.append(act("create_po", "Create A New Draft PO Instead", party=party, allow_duplicate=True, perm=can["purchasing"]))
            else:
                out["todo"] += _lines_todo(d, "PO") if d else []
                primary = act("create_po", f"Create Draft PO For {pname}" if pname else "Create Draft PO…", party=party,
                              needs=None if party else "vendor", perm=can["purchasing"])
                if party and _open_pos(db, party["id"]):
                    options.append(act("attach", f"Attach To One Of {pname}'s POs…", needs="po", party=party, perm=can["purchasing"]))
        elif kind == "vendor_invoice":
            num, total = d.get("invoice_number"), d.get("total")
            out["bill"] = {"bill_number": num, "amount": total, "bill_date": d.get("invoice_date"), "due_date": d.get("due_date")}
            out["sentence"] = f"Invoice from {pname}" if pname else "A vendor invoice — from a vendor we don't have yet"
            out["facts"] = [x for x in [f"Invoice {num}" if num else None, _money(total) if can["money"] else None,
                                        f"Due {d['due_date'][:10]}" if d.get("due_date") else None, f"For {d['po_number']}" if d.get("po_number") else None] if x]
            have = None
            if num and party:
                have = (db.query(VendorBill, PurchaseOrder).join(PurchaseOrder, PurchaseOrder.id == VendorBill.po_id)
                        .filter(PurchaseOrder.vendor_id == party["id"]).all())
                have = next(((b, p) for b, p in have if (b.bill_number or "").strip().lower() == num.strip().lower()), None)
            if have:
                out["todo"].append(f"Invoice {num} is already recorded on {have[1].code}")
                primary = act("attach", f"Attach The File To {have[1].code}", record={"type": "purchase_order", "id": have[1].id, "code": have[1].code},
                              why="that invoice is already on it", perm=can["purchasing"])
            elif po:
                primary = act("add_bill", f"Add Invoice {num or ''} To {po.code}".replace("  ", " "), record={"type": "purchase_order", "id": po.id, "code": po.code},
                              why=po_why, perm=can["purchasing"])
            else:
                cands = _open_pos(db, party["id"]) if party else []
                guess = cands[0] if len(cands) == 1 else None
                if guess:
                    primary = act("add_bill", f"Add Invoice {num or ''} To {guess.code}".replace("  ", " "), record={"type": "purchase_order", "id": guess.id, "code": guess.code},
                                  why=f"{pname}'s only open PO", perm=can["purchasing"])
                else:
                    primary = act("add_bill", f"Add Invoice {num or ''} To A PO…".replace("  ", " "), needs="po", party=party, perm=can["purchasing"])
                    out["todo"].append("No PO # of ours on it — pick the PO it's for")
                options.append(act("create_po", f"Create A Draft PO From It{f' For {pname}' if pname else '…'}", party=party,
                                   needs=None if party else "vendor", perm=can["purchasing"]))
            if not num or not total:
                out["todo"].append("Check the invoice # and total (Edit Details)")
        else:  # mtr
            out["sentence"] = f"Material test report{f' from {pname}' if pname else ''}"
            if po:
                primary = act("attach", f"Attach To {po.code} As An MTR", record={"type": "purchase_order", "id": po.id, "code": po.code}, why=po_why, perm=can["purchasing"])
            else:
                primary = act("attach", "Attach To A PO As An MTR…", needs="po", party=party, perm=can["purchasing"])
        if kind != "mtr":
            options.append(act("attach", "Attach To Another PO…", needs="po", perm=can["purchasing"]))
    elif kind == "pod":
        m = d.get("shipment_matches") or []
        refs = (ident.get("refs") or {}).get("shipment") or []
        sh = db.get(Shipment, m[0]["shipment_id"]) if len(m) == 1 else db.get(Shipment, refs[0]["id"]) if len(refs) == 1 else None
        out["sentence"] = "Signed proof of delivery" + (f" for {sh.code}" if sh else "")
        out["facts"] = [x for x in [f"Signed by {d['received_by']}" if d.get("received_by") else None,
                                    f"Delivered {d['delivery_date'][:10]}" if d.get("delivery_date") else None] if x]
        if sh:
            primary = act("attach", f"Attach To {sh.code} (Marks It Delivered)", record={"type": "shipment", "id": sh.id, "code": sh.code},
                          why="its references match", perm=can["ship"])
        else:
            primary = act("attach", "Attach To A Shipment…", needs="shipment", perm=can["ship"])
    else:
        out["sentence"] = "Couldn't tell what this is — pick what to do with it"
        primary = None
    # the rest, always on offer (only what this user may do)
    std = [act("create_order", "Create Draft Customer Order…", needs="customer", perm=can["orders"]),
           act("create_po", "Create Draft Purchase Order…", needs="vendor", perm=can["purchasing"]),
           act("create_quote", "Create Draft Quote…", needs="customer", perm=can["quotes"]),
           act("add_bill", "Add As A Vendor Invoice On A PO…", needs="po", perm=can["purchasing"]),
           act("attach", "Attach To A Customer Order…", needs="order", perm=can["orders"]),
           act("attach", "Attach To A PO…", needs="po", perm=can["purchasing"]),
           act("attach", "Attach To A Shipment…", needs="shipment", perm=can["ship"])]
    seen = {(o["action"], o.get("needs")) for o in options} | ({(primary["action"], primary.get("needs"))} if primary else set())
    options += [o for o in std if (o["action"], o.get("needs")) not in seen]
    out["primary"] = primary if primary and primary.get("perm", True) else None
    out["options"] = [o for o in options if o.get("perm", True)]
    if primary and not primary.get("perm", True):
        out["todo"].append("You don't have permission to do the usual thing with this — someone with access can, from the desk")
    if not out["primary"] and out["options"]:
        out["primary"] = None  # the screen shows the options straight away
    return out


# ---------------------------------------------------------------------------------------------------- doing it
def _need(user: User, perm: str):
    if not _can(user, perm):
        raise HTTPException(status_code=403, detail="You don't have permission for that")


def _store(db: Session, rec_type: str, rec_id: int, category: str, row: DeskFile, data: bytes, user: User):
    from app.routes.attachments import store_file
    from app.services import type_lists
    try:
        if category not in type_lists.keys(db, "attachment", rec_type):
            category = "other"
    except Exception:
        pass
    return store_file(db, rec_type, rec_id, category, row.filename, row.content_type, data, "From the AI Desk", user.username)


def act(db: Session, user: User, row: DeskFile, data: bytes, a: dict) -> dict:
    """a: {action, party_id, record_type, record_id, bill: {...}, allow_duplicate}"""
    res = json.loads(row.result or "{}")
    kind = res.get("kind") or "other"
    d = res.get("draft") or {}
    action = a.get("action")
    allow = bool(a.get("allow_duplicate"))
    out = {}
    if action == "create_order":
        _need(user, "orders.edit")
        cid = a.get("party_id") or ((_party(db, res, "customer") or {}).get("id"))
        if not cid or not db.get(Customer, cid):
            raise HTTPException(status_code=400, detail="Pick the customer")
        draft = dict(d) if kind in ("customer_po", "rfq") else {}
        draft["customer"] = {"customer_id": cid}
        draft.setdefault("lines", [])
        from app.services import ai_pending
        rec = ai_pending.create_for_validation(db, "customer", draft, row.filename, user.username, allow_duplicate=allow)
        rec.status = "validation"
        _store(db, "customer_order", rec.id, "customer_po", row, data, user)
        out = {"record_type": "customer_order", "record": rec, "label": f"Created order {rec.code} — check it and Validate it"}
    elif action == "create_po":
        _need(user, "purchasing")
        vid = a.get("party_id") or ((_party(db, res, "vendor") or {}).get("id"))
        if not vid or not db.get(Vendor, vid):
            raise HTTPException(status_code=400, detail="Pick the vendor")
        draft = dict(d) if kind in ("vendor_order", "vendor_invoice") else {}
        draft["vendor"] = {"vendor_id": vid}
        draft.setdefault("lines", [])
        if kind == "vendor_invoice":
            draft["document_number"] = None  # an invoice # isn't their SO #
        from app.services import ai_pending
        rec = ai_pending.create_for_validation(db, "vendor", draft, row.filename, user.username, allow_duplicate=allow)
        rec.status = "validation"
        _store(db, "purchase_order", rec.id, "vendor_invoice" if kind == "vendor_invoice" else "vendor_quote", row, data, user)
        out = {"record_type": "purchase_order", "record": rec, "label": f"Created PO {rec.code} — check it and Validate it"}
    elif action == "create_quote":
        _need(user, "quotes")
        cid = a.get("party_id") or ((_party(db, res, "customer") or {}).get("id"))
        if not cid or not db.get(Customer, cid):
            raise HTTPException(status_code=400, detail="Pick the customer")
        from datetime import timedelta
        from app.services.crud import generate_code
        from app.services import quotes as quote_svc
        q = Quote(code=generate_code(db, Quote, "Q"), customer_id=cid, customer_ref=d.get("po_number"), notes=None,
                  valid_until=datetime.utcnow() + timedelta(days=30), created_by=user.username)
        db.add(q)
        db.flush()
        for pos, l in enumerate([l for l in d.get("lines") or [] if not l.get("companion_of")]):
            said = " ".join(x for x in [l.get("item_code") or l.get("customer_item_code"), l.get("description")] if x)
            item = db.get(StockItem, l["item_id"]) if l.get("item_id") else None
            price = quote_svc.suggest_price(db, item, cid).get("price") if item else 0
            q.lines.append(QuoteLine(position=pos, item_id=item.id if item else None, description=None if item else (said or "Line from the request"),
                                     quantity=l.get("quantity") or 1, unit_price=price or 0, source_text=said or None))
        _store(db, "quote", q.id, "customer_rfq", row, data, user)
        out = {"record_type": "quote", "record": q, "label": f"Created quote {q.code} — price it and send it"}
    elif action in ("attach", "add_bill"):
        rt, rid = a.get("record_type"), a.get("record_id")
        model = {"customer_order": CustomerOrder, "purchase_order": PurchaseOrder, "shipment": Shipment, "quote": Quote}.get(rt)
        rec = db.get(model, rid) if model and rid else None
        if not rec:
            raise HTTPException(status_code=400, detail="Pick the record")
        _need(user, {"customer_order": "orders.edit", "purchase_order": "purchasing", "shipment": "shipments.work", "quote": "quotes"}[rt])
        if action == "add_bill":
            if rt != "purchase_order":
                raise HTTPException(status_code=400, detail="A vendor invoice goes on a PO")
            b = a.get("bill") or {}
            num = (b.get("bill_number") or d.get("invoice_number") or "").strip()
            amount = b.get("amount") if b.get("amount") not in (None, "") else d.get("total")
            if not num or not amount:
                raise HTTPException(status_code=400, detail="The invoice # and total are needed — Edit Details")
            att = _store(db, "purchase_order", rec.id, "vendor_invoice", row, data, user)
            db.flush()
            from app import schemas
            from app.services.crud import VendorBillService
            VendorBillService.create(db, rec.id, schemas.VendorBillInput(
                bill_number=num, amount=float(amount), bill_date=b.get("bill_date") or d.get("invoice_date"), due_date=b.get("due_date") or d.get("due_date"),
                attachment_id=att.id, note="From the AI Desk", allow_duplicate=allow), created_by=user.username)
            out = {"record_type": rt, "record": rec, "label": f"Invoice {num} added to {rec.code}, file attached"}
        else:
            cat = ATTACH_CATEGORY.get(kind, "other")
            _store(db, rt, rec.id, cat, row, data, user)
            extra = ""
            if rt == "purchase_order" and kind == "vendor_order" and d.get("document_number") and not rec.vendor_so_number:
                from app.services.crud import PurchaseOrderService
                if allow or not PurchaseOrderService.duplicate_so(db, rec.vendor_id, d["document_number"], rec.id):
                    rec.vendor_so_number = d["document_number"].strip()
                    extra = f", SO # {rec.vendor_so_number} saved"
            if rt == "shipment" and kind == "pod" and not rec.delivered_at:
                from app.services.crud import ShipmentService
                if rec.status in ShipmentService.SHIPPED_STATUSES:
                    ShipmentService.mark_delivered(db, rec, None, user.username, commit=False)
                    extra = ", marked delivered"
            out = {"record_type": rt, "record": rec, "label": f"Attached to {rec.code}{extra}"}
    else:
        raise HTTPException(status_code=400, detail="Unknown action")
    rec = out["record"]
    row.status, row.record_type, row.record_id, row.record_code = "done", out["record_type"], rec.id, rec.code
    row.done_label, row.done_by, row.done_at = out["label"], user.username, _now()
    db.commit()
    db.refresh(rec)
    la = []
    if out["record_type"] in ("customer_order", "purchase_order") and action.startswith("create"):
        from app.services import lookalike
        la = lookalike.pending(db, "customer" if out["record_type"] == "customer_order" else "vendor", rec, full=False)
    return {"lookalikes": la}
