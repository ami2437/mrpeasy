"""Look-alike orders: the same customer (or vendor) with the same items and quantities as another live order.

Customers really do send the same items, quantities and prices on several POs, so a look-alike is never refused --
but someone has to look and say "this is a separate order, not a duplicate" before the order can be confirmed /
validated (a PO: validated or marked ordered). Who said it, when, and about which orders is kept on the record
(lookalike_ok); a new look-alike that turns up later needs its own OK.

How two orders are compared: each is a bag of (item, quantity) lines. They look alike when
  * every line matches (same items, same quantities -- prices are compared and reported, not required), or
  * at least 2 lines match and the matched lines are >= 75% of the bigger order.
Only orders that are still live (not cancelled; open, or created within WINDOW_DAYS of each other) are compared, and
only records still in play (customer: validation / draft / confirmed; PO: validation / draft / ordered / shipped) are
checked -- old history never gets flagged.

    find(db, kind, rec)          -> every look-alike of the record, with the lines side by side
    pending(db, kind, rec)       -> those not OK'd yet
    annotate(db, kind, records)  -> sets rec.lookalikes (pending ones, short form) on a list, in one pass
    acknowledge(db, kind, rec, codes, by)
    require_ok(db, kind, rec)    -> 409 LOOKALIKE|... while any are pending
"""
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import CustomerOrder, PurchaseOrder, StockItem

WINDOW_DAYS = 60
IN_PLAY = {"customer": ("validation", "draft", "confirmed"), "vendor": ("validation", "draft", "ordered", "shipped")}
OPEN = {"customer": ("validation", "draft", "confirmed"), "vendor": ("validation", "draft", "ordered", "shipped", "partially_received")}


def _model(kind):
    return CustomerOrder if kind == "customer" else PurchaseOrder


def _party(kind, rec):
    return rec.customer_id if kind == "customer" else rec.vendor_id


def _price(kind, line):
    return (line.unit_price if kind == "customer" else line.unit_cost) or 0


def _lines(kind, rec):
    return [l for l in rec.lines if l.item_id and (l.quantity or 0) > 0]


def _bag(kind, rec) -> Counter:
    return Counter((l.item_id, round(l.quantity, 4)) for l in _lines(kind, rec))


def _when(rec):
    return rec.created_at or datetime.utcnow()


def _compare(kind, a, b):
    """None, or how b looks like a."""
    A, B = _bag(kind, a), _bag(kind, b)
    if not A or not B:
        return None
    common = sum((A & B).values())
    if not common:
        return None
    exact = A == B
    score = common / max(sum(A.values()), sum(B.values()))
    if not exact and not (common >= 2 and score >= 0.75):
        return None
    # prices: on the matched lines, are they the same too?
    pa = defaultdict(list)
    for l in _lines(kind, a):
        pa[(l.item_id, round(l.quantity, 4))].append(round(_price(kind, l), 4))
    same_price = all(round(_price(kind, l), 4) in pa.get((l.item_id, round(l.quantity, 4)), [])
                     for l in _lines(kind, b) if (l.item_id, round(l.quantity, 4)) in A)
    n = sum(B.values())
    if exact:
        what = f"same {n} item{'s' if n != 1 else ''} and quantit{'ies' if n != 1 else 'y'}" + (" and prices" if same_price else " (prices differ)")
    else:
        what = f"{common} of {max(sum(A.values()), n)} lines the same (items and quantities)" + ("" if same_price else ", prices differ")
    return {"exact": exact, "score": round(score, 2), "same_price": same_price, "what": what}


def _candidates(db: Session, kind, rec, pool=None):
    model = _model(kind)
    party = _party(kind, rec)
    if pool is None:
        col = model.customer_id if kind == "customer" else model.vendor_id
        pool = db.query(model).filter(col == party, model.id != rec.id, model.status != "cancelled").all()
    lo, hi = _when(rec) - timedelta(days=WINDOW_DAYS), _when(rec) + timedelta(days=WINDOW_DAYS)
    return [o for o in pool if o.id != rec.id and _party(kind, o) == party and o.status != "cancelled"
            and (o.status in OPEN[kind] or lo <= _when(o) <= hi)]


def _codes(db: Session, ids):
    return {i.id: i.code for i in db.query(StockItem).filter(StockItem.id.in_(set(ids))).all()} if ids else {}


def _side(kind, rec, other_bag, codes):
    left = Counter(other_bag)
    out = []
    for l in _lines(kind, rec):
        k = (l.item_id, round(l.quantity, 4))
        hit = left[k] > 0
        if hit:
            left[k] -= 1
        out.append({"item_code": codes.get(l.item_id, ""), "quantity": l.quantity, "price": _price(kind, l), "match": hit})
    return out


def _ref(kind, rec):
    return (rec.po_number if kind == "customer" else rec.vendor_so_number) or ""


def find(db: Session, kind: str, rec, pool=None, full: bool = True) -> list:
    if rec.status not in IN_PLAY[kind]:
        return []
    out = []
    for o in _candidates(db, kind, rec, pool):
        m = _compare(kind, rec, o)
        if m:
            out.append((o, m))
    out.sort(key=lambda t: (-t[1]["score"], -t[0].id))
    if not full:
        return [{"id": o.id, "code": o.code, "what": m["what"], "exact": m["exact"]} for o, m in out]
    codes = _codes(db, [l.item_id for o, _ in out for l in o.lines] + [l.item_id for l in rec.lines])
    return [{"id": o.id, "code": o.code, "ref": _ref(kind, o), "status": o.status,
             "date": (o.created_at.isoformat() if o.created_at else None), "created_by": o.created_by, **m,
             "lines": _side(kind, o, _bag(kind, rec), codes), "mine": _side(kind, rec, _bag(kind, o), codes)} for o, m in out]


def _acked(rec) -> set:
    try:
        return set(json.loads(rec.lookalike_ok or "{}").get("codes") or [])
    except (TypeError, ValueError):
        return set()


def pending(db: Session, kind: str, rec, pool=None, full: bool = True) -> list:
    ok = _acked(rec)
    return [x for x in find(db, kind, rec, pool, full) if x["code"] not in ok]


def annotate(db: Session, kind: str, records) -> None:
    """rec.lookalikes on every record of a list (short form), comparing within each party only."""
    by_party = defaultdict(list)
    for r in records:
        by_party[_party(kind, r)].append(r)
    for r in records:
        r.lookalikes = pending(db, kind, r, pool=by_party[_party(kind, r)], full=False) if r.status in IN_PLAY[kind] else []


def acknowledge(db: Session, kind: str, rec, codes, by: str):
    have = {x["code"] for x in find(db, kind, rec)}
    codes = sorted(set(codes or []) & have) or sorted(have)
    prev = []
    try:
        prev = json.loads(rec.lookalike_ok or "{}").get("history") or []
    except (TypeError, ValueError):
        pass
    entry = {"codes": codes, "by": by, "at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
    rec.lookalike_ok = json.dumps({"codes": sorted(_acked(rec) | set(codes)), "by": by, "at": entry["at"], "history": prev + [entry]})
    db.commit()
    db.refresh(rec)
    return rec


def require_ok(db: Session, kind: str, rec) -> None:
    left = pending(db, kind, rec, full=False)
    if left:
        word = "order" if kind == "customer" else "PO"
        raise HTTPException(status_code=409, detail="LOOKALIKE|" + json.dumps(
            {"kind": kind, "id": rec.id, "codes": [x["code"] for x in left],
             "message": f"{rec.code} looks like {', '.join(x['code'] for x in left)} ({left[0]['what']}) -- check it isn't a duplicate "
                        f"and OK it on the {word} first"}))
