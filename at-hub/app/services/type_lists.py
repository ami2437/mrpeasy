"""Types & tags people can add to (Company Settings -> Types & Tags): document types for attached files, S&H / charge
types on purchase orders, landed cost types and payment methods. The built-in ones are seeded once and keep their keys
(customer_po, shipping, freight...), so older records and code that relies on them don't change.

New names are tidied so the lists don't sprawl: letters and spaces only, Title Case ("packing list" -> "Packing List",
short all-caps words kept: "BOL"), and a name close to one already there ("Vendor Packing List" vs "Packing List",
"Packing Lists" vs "Packing List") comes back as similar -- the person picks that one, or confirms the new one.

    normalize_label(text) -> "Packing List"           options(db, list, scope=None) -> [TypeOption]
    similar(db, list, label) -> [TypeOption]           keys(db, list, scope=None) -> {key}
    create(db, list, label, ...), update(db, id, ...)  money_keys(db) -> attachment types with prices (managers only)
"""
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Iterable, List, Optional

from fastapi import HTTPException

from app.models import TypeOption

# list -> what it is, and (for document types) the records a type can be attached to
LISTS = {
    "attachment": {"title": "Document types", "help": "The type of a file attached to an order, purchase order or shipment.",
                   "scopes": {"customer_order": "Customer orders", "purchase_order": "Purchase orders", "shipment": "Shipments"}},
    "charge": {"title": "S&H / charge types", "help": "Shipping & handling lines on a purchase order (Add S&H)."},
    "landed_cost": {"title": "Landed cost types", "help": "Costs spread over received stock (freight, tariff...)."},
    "payment_method": {"title": "Payment methods", "help": "How a payment was made, on invoices, POs and vendor payments."},
}
CO, PO, SH = "customer_order", "purchase_order", "shipment"
# (key, label, scopes, money) -- the types AT-HUB always had
BUILTINS = {
    "attachment": [("customer_po", "Customer PO", [CO], True), ("purchase_order", "Purchase Order", [PO], True),
                   ("vendor_quote", "Vendor Quote / Confirmation", [PO], True), ("vendor_invoice", "Vendor Invoice", [PO], True),
                   ("invoice", "Our Invoice", [CO], True), ("packing_list", "Packing List", [CO, PO, SH], False),
                   ("bol", "Bill of Lading", [CO, PO, SH], False), ("mtr", "Material Test Report (MTR)", [CO, PO], False),
                   ("pod", "Proof of Delivery", [SH], False), ("other", "Other", [CO, PO, SH], False)],
    "charge": [("shipping", "Shipping", None, False), ("freight", "Freight", None, False), ("handling", "Handling", None, False),
               ("other", "Other", None, False)],
    "landed_cost": [("freight", "Freight", None, False), ("tariff", "Tariff", None, False), ("customs", "Customs", None, False),
                    ("brokerage", "Brokerage", None, False), ("insurance", "Insurance", None, False), ("other", "Other", None, False)],
    "payment_method": [("check", "Check", None, False), ("ach", "ACH", None, False), ("wire", "Wire", None, False),
                       ("credit_card", "Credit Card", None, False), ("cash", "Cash", None, False), ("other", "Other", None, False)],
}
# words that only say whose / which copy -- sharing one of these alone doesn't make two names alike
QUALIFIERS = {"vendor", "customer", "our", "their", "copy", "signed", "original", "final", "new", "old", "other"}
MINOR = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "via", "with"}


def normalize_label(text: str) -> str:
    """'  packing   LIST ' -> 'Packing List'; letters and spaces only (no digits or symbols); short words typed in capitals
    stay capitals (BOL, ACH); small joining words stay lower case after the first word ('Bill of Lading')."""
    words = re.sub(r"[^A-Za-z ]+", " ", text or "").split()
    out = []
    for i, w in enumerate(words):
        if w.isupper() and 2 <= len(w) <= 4:
            out.append(w)
        elif i and w.lower() in MINOR:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return " ".join(out)


def slug(label: str) -> str:
    return re.sub(r"[^a-z]+", "_", label.lower()).strip("_")


def _stem(word: str) -> str:
    w = word.lower()
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _words(label: str) -> set:
    return {_stem(w) for w in re.findall(r"[A-Za-z]+", label or "") if w.lower() not in MINOR}


def is_similar(a: str, b: str) -> bool:
    """Same words (plural or not), one name inside the other ('Packing List' / 'Vendor Packing List'), or nearly the
    same spelling ('Packing Slip' / 'Packing Slips', 'Frieght' / 'Freight')."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    if wa == wb or wa <= wb or wb <= wa:
        return True
    if (wa - QUALIFIERS) & (wb - QUALIFIERS):  # a word that says what it is: Packing Slips / Packing List, Our Invoice / Vendor Invoice
        return True
    return SequenceMatcher(None, " ".join(sorted(wa)), " ".join(sorted(wb))).ratio() >= 0.82


def seed(db) -> None:
    """Built-in types, once (startup). Never re-adds one somebody hid; never overwrites a label somebody changed."""
    have = {(t.list, t.key) for t in db.query(TypeOption).all()}
    n = 0
    for lst, rows in BUILTINS.items():
        for pos, (key, label, scopes, money) in enumerate(rows):
            if (lst, key) not in have:
                db.add(TypeOption(list=lst, key=key, label=label, scopes=",".join(scopes) if scopes else None, money=money,
                                  builtin=True, active=True, position=pos, created_by="AT-HUB"))
                n += 1
    if n:
        db.commit()


def _check_list(lst: str) -> None:
    if lst not in LISTS:
        raise HTTPException(status_code=400, detail=f"Unknown list '{lst}'")


def options(db, lst: str, scope: Optional[str] = None, include_inactive: bool = False) -> List[TypeOption]:
    _check_list(lst)
    q = db.query(TypeOption).filter(TypeOption.list == lst)
    if not include_inactive:
        q = q.filter(TypeOption.active.is_(True))
    rows = q.order_by(TypeOption.builtin.desc(), TypeOption.position, TypeOption.label).all()
    if scope:
        rows = [r for r in rows if not r.scopes or scope in r.scopes.split(",")]
    return rows


def keys(db, lst: str, scope: Optional[str] = None) -> set:
    return {r.key for r in options(db, lst, scope)}


def all_keys(db, lst: str) -> set:
    """Every key, hidden ones too -- an old record keeps a type somebody hid since."""
    return {r.key for r in options(db, lst, include_inactive=True)}


def money_keys(db) -> set:
    """Document types that show prices: hidden from people who can't see money."""
    return {r.key for r in db.query(TypeOption).filter(TypeOption.list == "attachment", TypeOption.money.is_(True)).all()}


def similar(db, lst: str, label: str, exclude_id: Optional[int] = None) -> List[TypeOption]:
    return [r for r in options(db, lst, include_inactive=True) if r.id != exclude_id and is_similar(r.label, label)]


def _scopes(lst: str, scopes: Optional[Iterable[str]]) -> Optional[str]:
    if lst != "attachment":
        return None
    allowed = LISTS["attachment"]["scopes"]
    picked = [s for s in (scopes or []) if s in allowed]
    if not picked:
        raise HTTPException(status_code=400, detail="Pick at least one kind of record this document type is for")
    return ",".join(dict.fromkeys(picked))


def create(db, lst: str, label: str, scopes=None, money: bool = False, force: bool = False, by: str = None) -> TypeOption:
    """force: create it although a similar one exists (the person saw the suggestion and said no)."""
    _check_list(lst)
    clean = normalize_label(label)
    if len(clean) < 2:
        raise HTTPException(status_code=400, detail="Give it a name -- letters and spaces only")
    if len(clean) > 40:
        raise HTTPException(status_code=400, detail="Keep the name under 40 characters")
    key = slug(clean)
    same = db.query(TypeOption).filter(TypeOption.list == lst, TypeOption.key == key).first() or next(
        (r for r in options(db, lst, include_inactive=True) if r.label.lower() == clean.lower()), None)
    if same:
        raise HTTPException(status_code=409, detail={"code": "exists", "message": f"{same.label} is already there" +
                                                     ("" if same.active else " (hidden -- show it again instead)"),
                                                     "match": {"id": same.id, "key": same.key, "label": same.label, "active": same.active}})
    near = similar(db, lst, clean)
    if near and not force:
        raise HTTPException(status_code=409, detail={"code": "similar", "label": clean,
                                                     "message": f"Similar: {', '.join(r.label for r in near)}",
                                                     "similar": [{"id": r.id, "key": r.key, "label": r.label, "active": r.active} for r in near]})
    row = TypeOption(list=lst, key=key, label=clean, scopes=_scopes(lst, scopes), money=bool(money) if lst == "attachment" else False,
                     builtin=False, active=True, position=1000, created_by=by, created_at=datetime.utcnow())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update(db, type_id: int, label: Optional[str] = None, scopes=None, money: Optional[bool] = None,
           active: Optional[bool] = None, force: bool = False) -> TypeOption:
    row = db.get(TypeOption, type_id)
    if not row:
        raise HTTPException(status_code=404, detail="That type isn't there any more")
    if label is not None:
        clean = normalize_label(label)
        if len(clean) < 2 or len(clean) > 40:
            raise HTTPException(status_code=400, detail="Use 2 to 40 letters and spaces")
        if clean.lower() != row.label.lower():
            dup = next((r for r in options(db, row.list, include_inactive=True) if r.id != row.id and r.label.lower() == clean.lower()), None)
            if dup:
                raise HTTPException(status_code=409, detail={"code": "exists", "message": f"{dup.label} is already there"})
            near = similar(db, row.list, clean, exclude_id=row.id)
            if near and not force:
                raise HTTPException(status_code=409, detail={"code": "similar", "label": clean, "message": f"Similar: {', '.join(r.label for r in near)}",
                                                             "similar": [{"id": r.id, "key": r.key, "label": r.label, "active": r.active} for r in near]})
        row.label = clean  # the key stays: records keep pointing at it
    if scopes is not None and row.list == "attachment":
        row.scopes = _scopes(row.list, scopes)
    if money is not None and row.list == "attachment" and not row.builtin:
        row.money = bool(money)
    if active is not None:
        if not active and row.key == "other":
            raise HTTPException(status_code=400, detail="Other is always there -- it's what everything falls back to")
        row.active = bool(active)
    db.commit()
    db.refresh(row)
    return row


def label_of(db, lst: str, key: Optional[str]) -> str:
    if not key:
        return ""
    row = db.query(TypeOption).filter(TypeOption.list == lst, TypeOption.key == key).first()
    return row.label if row else key.replace("_", " ").title()
