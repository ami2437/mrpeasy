"""Print Options: what each document prints, chosen in the pop-up behind every PDF / Print button and remembered
per user per document type (User.print_prefs, JSON). The same saved choices drive emailed PDFs and Bulk Operations,
so a document looks the same however it's produced.

    CATALOG                       -> per document type: [(key, label, default, hint)]
    for_user(user, doc_type)      -> {key: bool} -- the user's saved choices over the defaults
    save(db, user, doc_type, d)   -> store them (unknown keys dropped)
    from_query(user, doc_type, q) -> the user's choices, overridden by whatever the request names (?notes=false)
"""
import json
from contextvars import ContextVar
from typing import Dict, Optional

_user: ContextVar = ContextVar("print_options_user", default=None)


def use(user) -> None:
    """Whose saved choices documents made in this request follow (emails and Bulk Operations render server side)."""
    _user.set(user)


def current(doc_type: str) -> Dict[str, bool]:
    return for_user(_user.get(), doc_type)


CATALOG = {
    "invoice": [
        ("due_date", "Due Date", True, "The date payment is due (invoice date + the customer's terms)"),
        ("payments", "Previous Payments", True, "List the customer's payments so far and show only the balance as due"),
        ("zero_lines", "$0 Lines", False, "Lines priced at $0 (free samples, the nut of a bolt + nut kit)"),
        ("notes", "Line Notes", True, "A note marked 'don't print' never prints"),
    ],
    "packing_list": [
        ("boxes", "Box Details", True, "The box breakdown of each line"),
        ("pallets", "Pallet Info", True, "Pallet weights and dimensions (only when the shipment has pallets)"),
        ("pallet_boxes", "Boxes Per Pallet", False, "How many boxes ride on each pallet, in the pallet table"),
        ("lots", "Lot #", False, "The lot each line was shipped from"),
        ("notes", "Line Notes", True, "Line notes from the order -- a note marked 'don't print' never prints"),
    ],
    "purchase_order": [
        ("notes", "Line Notes", True, "A note marked 'don't print' never prints"),
    ],
    "quote": [
        ("notes", "Line Notes", True, "A note marked 'don't print' never prints"),
    ],
}


def defaults(doc_type: str) -> Dict[str, bool]:
    return {k: d for k, _, d, _ in CATALOG.get(doc_type, [])}


def _saved(user) -> dict:
    try:
        return json.loads(user.print_prefs or "{}") if user is not None else {}
    except (TypeError, ValueError):
        return {}


def for_user(user, doc_type: str) -> Dict[str, bool]:
    out = defaults(doc_type)
    mine = _saved(user).get(doc_type) or {}
    out.update({k: bool(v) for k, v in mine.items() if k in out})
    return out


def save(db, user, doc_type: str, choices: dict) -> Dict[str, bool]:
    """Remember these choices. A document type outside the catalog (the on-screen record print) keeps whatever the
    page sends, as long as it's small."""
    allp = _saved(user)
    if doc_type in CATALOG:
        allp[doc_type] = {k: bool(v) for k, v in (choices or {}).items() if k in defaults(doc_type)}
    else:
        blob = json.dumps(choices or {})
        if len(blob) > 4000:
            raise ValueError("Too many print options to remember")
        allp[doc_type] = choices or {}
    user.print_prefs = json.dumps(allp)
    db.commit()
    return allp[doc_type]


def from_query(user, doc_type: str, query: Dict[str, Optional[bool]]) -> Dict[str, bool]:
    out = for_user(user, doc_type)
    out.update({k: v for k, v in query.items() if v is not None and k in out})
    return out


def all_for_user(user) -> dict:
    """The pop-up's data: each document type's options with labels, hints and this user's current choice."""
    saved = _saved(user)
    docs = {t: [{"key": k, "label": label, "hint": hint, "value": for_user(user, t)[k], "default": d}
                for k, label, d, hint in rows] for t, rows in CATALOG.items()}
    return {"docs": docs, "other": {k: v for k, v in saved.items() if k not in CATALOG}}
