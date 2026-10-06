"""Pack-size memory: what each item was actually packed at, and the size the packing screens pre-fill.

Every accepted packing is the history -- each shipment's boxes per order line, with its order, customer and date.
Nothing extra is stored: accepting a packing makes its sizes the newest "use" automatically.

    usage(db, item_ids)                  -> {item_id: [uses, newest first]}
    suggest(db, item_ids, customer_id)   -> {item_id: {"size", "source", "label", ...}}
    suggestions_for_shipments(db, ids)   -> {shipment_id: {order_line_id: suggestion}}

The rule (Company profile -> pack_size_rule, switchable on Bulk Operations):
    smart     the customer's last size, else the last size anyone packed; an item default changed more recently wins
    customer  the customer's last size, else the item default
    last      the last size anyone packed, else the item default
    default   the item default only (the old behaviour)
"""
from collections import defaultdict
from datetime import datetime
from typing import Dict, Iterable, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import CompanyProfile, Customer, CustomerOrder, PackSizeHistory, Shipment, ShipmentBox, ShipmentCombo, StockItem

RULES = {  # key: (short name for the menu, what it does)
    "smart": ("Smart", "The customer's last size, else the last size anyone packed; an item default changed since then wins"),
    "customer": ("Customer's last", "The customer's last size, else the item default"),
    "last": ("Last packed", "The last size anyone packed, else the item default"),
    "default": ("Item default", "The item's default pack size only"),
}


def current_rule(db: Session) -> str:
    company = db.get(CompanyProfile, 1)
    rule = getattr(company, "pack_size_rule", None) if company else None
    return rule if rule in RULES else "smart"


def _when(sh: Shipment) -> Optional[datetime]:
    return sh.packed_at or sh.ship_date or sh.created_at


def usage(db: Session, item_ids: Optional[Iterable[int]] = None, limit: int = 10) -> Dict[int, List[dict]]:
    """Each accepted packing of each item, newest first. A line packed as one box doesn't show its pack size
    unless it was recorded (ShipmentBox.pack_size) -- those are listed but flagged unsure and never suggested."""
    q = db.query(ShipmentBox.shipment_id, ShipmentBox.order_line_id, ShipmentBox.item_id,
                 func.max(ShipmentBox.quantity_in_box), func.count(ShipmentBox.id), func.max(ShipmentBox.pack_size),
                 func.sum(ShipmentBox.quantity_in_box))
    ids = set(item_ids) if item_ids is not None else None
    if ids is not None:
        if not ids:
            return {}
        q = q.filter(ShipmentBox.item_id.in_(ids))
    rows = q.group_by(ShipmentBox.shipment_id, ShipmentBox.order_line_id, ShipmentBox.item_id).all()
    ships = {s.id: s for s in db.query(Shipment).filter(Shipment.id.in_({r[0] for r in rows})).all()} if rows else {}
    orders = {o.id: o for o in db.query(CustomerOrder).filter(CustomerOrder.id.in_({s.order_id for s in ships.values()})).all()} if ships else {}
    custs = {c.id: c.name for c in db.query(Customer).all()} if orders else {}
    # bolts boxed with their nuts (assembled units) pack differently from bare bolts: kept apart
    assembled = set(db.query(ShipmentCombo.shipment_id, ShipmentCombo.lead_line_id)
                    .filter(ShipmentCombo.shipment_id.in_(list(ships))).all()) if ships else set()
    out = defaultdict(list)
    for sid, line_id, item_id, biggest, n, recorded, total in rows:
        sh = ships.get(sid)
        # a packing counts once it's accepted (or the shipment has gone); a half-edited open one doesn't
        if not sh or sh.status == "cancelled" or (sh.status in ("new", "ready") and not sh.packed_at):
            continue
        o = orders.get(sh.order_id)
        size = int(recorded or round(biggest or 0))
        if not size:
            continue
        when = _when(sh)
        out[item_id].append({
            "item_id": item_id, "pack_size": size, "sure": bool(recorded) or n > 1, "boxes": n, "quantity": total,
            "shipment_id": sid, "shipment": sh.code, "order_line_id": line_id, "assembled": (sid, line_id) in assembled,
            "order": o.code if o else "", "po": (o.po_number if o else "") or "", "customer_id": o.customer_id if o else None,
            "customer": custs.get(o.customer_id, "") if o else "", "date": when.isoformat() if when else None,
            "_when": when or datetime.min,
        })
    for item_id, uses in out.items():
        uses.sort(key=lambda u: (u["_when"], u["shipment_id"]), reverse=True)
        del uses[limit:]
        for u in uses:
            u.pop("_when")
    return dict(out)


def _default_changed(db: Session, item_ids) -> Dict[int, datetime]:
    """When someone last set each item's default by hand -- a bulk load from the old portal isn't a decision, so it doesn't count."""
    return dict(db.query(PackSizeHistory.item_id, func.max(PackSizeHistory.changed_at))
                .filter(PackSizeHistory.item_id.in_(set(item_ids)), func.coalesce(PackSizeHistory.source, "") != "portal")
                .group_by(PackSizeHistory.item_id).all())


def _fmt_day(iso: Optional[str]) -> str:
    try:
        d = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    return f"{d:%b} {d.day}"


def _pick(item: StockItem, uses: List[dict], customer_id: Optional[int], rule: str, default_at: Optional[datetime]) -> dict:
    sure = [u for u in uses if u["sure"]]
    mine = next((u for u in sure if customer_id and u["customer_id"] == customer_id), None)
    anyone = sure[0] if sure else None
    dflt = item.default_pack_size or None

    def from_use(u, source):
        day = _fmt_day(u["date"])
        who = "This customer's last" if source == "customer" else f"Last packed ({u['customer'] or 'no customer'})"
        return {"size": u["pack_size"], "source": source, "shipment": u["shipment"], "order": u["order"], "date": u["date"],
                "label": f"{who} · {u['shipment']}{f' · {day}' if day else ''}"}
    as_default = {"size": dflt, "source": "default", "label": "Item default"} if dflt else None

    if rule == "default":
        choice = as_default
    elif rule == "customer":
        choice = (mine and from_use(mine, "customer")) or as_default
    elif rule == "last":
        choice = (anyone and from_use(anyone, "customer" if mine is anyone else "last")) or as_default
    else:  # smart
        newest = mine or anyone
        try:
            newest_at = datetime.fromisoformat(newest["date"]) if newest and newest["date"] else None
        except ValueError:
            newest_at = None
        if as_default and default_at and (not newest_at or default_at > newest_at):
            choice = dict(as_default, label="Item default (changed since last packed)" if newest else "Item default")
        else:
            choice = (mine and from_use(mine, "customer")) or (anyone and from_use(anyone, "last")) or as_default
    return choice or {"size": None, "source": "none", "label": "No pack size yet — one box per line"}


def suggest(db: Session, item_ids: Iterable[int], customer_id: Optional[int] = None, rule: Optional[str] = None,
            _uses: Optional[dict] = None, assembled: bool = False) -> Dict[int, dict]:
    """assembled: the bolt goes out with its nuts (app/services/nut_combos.py) -- earlier assembled packings of it are
    used first; with none, its ordinary packing. Ordinary lines never learn from assembled ones."""
    ids = set(item_ids)
    if not ids:
        return {}
    rule = rule if rule in RULES else current_rule(db)
    uses = _uses if _uses is not None else usage(db, ids, limit=50)
    changed = _default_changed(db, ids)
    items = {i.id: i for i in db.query(StockItem).filter(StockItem.id.in_(ids)).all()}
    out = {}
    for iid in ids:
        if iid not in items:
            continue
        mine = [u for u in uses.get(iid, []) if bool(u.get("assembled")) == assembled]
        if assembled and any(u["sure"] for u in mine):
            pick = _pick(items[iid], mine, customer_id, rule, None)
            out[iid] = dict(pick, label=f"Assembled · {pick['label']}")
            continue
        if assembled:
            mine = [u for u in uses.get(iid, []) if not u.get("assembled")]
        out[iid] = _pick(items[iid], mine, customer_id, rule, changed.get(iid))
    return out


def suggestions_for_shipments(db: Session, shipment_ids: Iterable[int], rule: Optional[str] = None) -> Dict[int, dict]:
    """{shipment_id: {order_line_id: suggestion}} -- what each line of each shipment would pre-fill."""
    ships = db.query(Shipment).filter(Shipment.id.in_(set(shipment_ids))).all()
    if not ships:
        return {}
    rule = rule if rule in RULES else current_rule(db)
    orders = {o.id: o for o in db.query(CustomerOrder).filter(CustomerOrder.id.in_({s.order_id for s in ships})).all()}
    all_items = {l.item_id for s in ships for l in s.lines}
    uses = usage(db, all_items, limit=50)
    out = {}
    by_customer = {}
    for s in ships:
        cid = orders[s.order_id].customer_id if s.order_id in orders else None
        if cid not in by_customer:
            by_customer[cid] = suggest(db, all_items, cid, rule, _uses=uses)
        sug = by_customer[cid]
        out[s.id] = {l.order_line_id: sug.get(l.item_id) for l in s.lines if l.order_line_id}
        for c in s.combos:  # bolts going out assembled: their own packing memory
            lead = next((l for l in s.lines if l.order_line_id == c.lead_line_id), None)
            if lead:
                out[s.id][c.lead_line_id] = suggest(db, [lead.item_id], cid, rule, _uses=uses, assembled=True).get(lead.item_id)
    return out


def size_for_line(db: Session, shipment: Shipment, item_id: int, order_line_id: Optional[int] = None) -> Optional[int]:
    """The pre-filled size for one line, server side (accepting packing with no boxes, labels for old shipments)."""
    order = db.get(CustomerOrder, shipment.order_id)
    assembled = bool(order_line_id) and any(c.lead_line_id == order_line_id for c in shipment.combos)
    return (suggest(db, [item_id], order.customer_id if order else None, assembled=assembled).get(item_id) or {}).get("size")
