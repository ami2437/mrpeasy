"""Quotes: read pasted RFQ text into lines, match our items, suggest a price from price history."""
import re
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models import Customer, CustomerOrder, CustomerOrderLine, Quote, QuoteLine, StockItem
from app.services import item_alias
from app.services.crud import price_history
from app.services.item_match import ItemMatcher, pick

QTY_WORDS = r"(?:pcs?|pieces?|ea|each|nos?|units?|qty|quantity|pc\.?|ct)"
SKIP = re.compile(r"^(hi|hello|dear|thanks|thank you|regards|best|please|pls|kindly|can you|could you|we need|sent from|--)\b", re.I)
FASTENER = re.compile(r"\d+/\d+|\d+-\d+|\bM\d+|bolt|nut|washer|screw|stud|rod|anchor|pin|rivet|a325|a490|a307|f436|a193|a563|hdg|galv", re.I)


def _num(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def split_qty(line: str):
    """(quantity, description) from one pasted line. Handles '500 pcs ...', 'qty 500', '... x 500',
    '1,000 - 5/8 F436', tab/'|' separated sheet rows. Sizes like 5/8-11 x 2 are never taken as qty."""
    t = line.strip().strip("-•*·").strip()
    cells = [c.strip() for c in re.split(r"\t|\s{3,}|\|", t) if c.strip()]
    if len(cells) > 1:  # a pasted sheet row: the qty is a cell that is just a whole number
        nums = [(i, _num(c)) for i, c in enumerate(cells) if re.fullmatch(r"\d[\d,]*(\.0+)?", c)]
        if nums:
            i, q = nums[-1]  # code / description first, quantity after it (a part # can be all digits too)
            return q, " ".join(c for j, c in enumerate(cells) if j != i)
    for pat in (rf"\b(?:qty|quantity)\s*[:=#]?\s*(\d[\d,]*)", rf"(\d[\d,]*)\s*{QTY_WORDS}\b", r"\(\s*(\d[\d,]*)\s*\)\s*$", r"[x×]\s*(\d[\d,]*)\s*$"):
        m = re.search(pat, t, re.I)
        if m and _num(m.group(1)):
            return _num(m.group(1)), (t[:m.start()] + " " + t[m.end():]).strip(" -:,")
    m = re.match(r"(\d[\d,]*)\s*(?:[-–:x×]\s*|\s+)(?![/\-])(.+)", t)
    if m and "/" not in m.group(1) and FASTENER.search(m.group(2)):
        return _num(m.group(1)), m.group(2).strip()
    return None, t


def _when(d) -> str:
    return f", {d:%m/%d/%Y}" if d else ""


def suggest_price(db: Session, item: StockItem, customer_id: int, exclude_quote_id: Optional[int] = None) -> dict:
    """Last price this customer paid / was quoted, else the last sale to anyone, else the item's
    selling price -- with the latest cost so the margin can be seen. `others` lists the other
    recent prices that differ from the one picked, so a disagreement is never hidden."""
    hist = price_history(db, item.id)
    sales = [h for h in hist if h["kind"] == "sale" and h["status"] != "cancelled"]
    mine = []
    for h in sales:
        o = db.query(CustomerOrder).filter(CustomerOrder.id == h["doc_id"]).first()
        if o and o.customer_id == customer_id:
            mine.append(h)
    qq = (db.query(QuoteLine, Quote).join(Quote, Quote.id == QuoteLine.quote_id)
          .filter(QuoteLine.item_id == item.id, Quote.customer_id == customer_id, QuoteLine.unit_price > 0))
    if exclude_quote_id:
        qq = qq.filter(Quote.id != exclude_quote_id)
    quoted = qq.order_by(Quote.quote_date.desc()).first()
    cost = next((h["unit_price"] for h in hist if h["kind"] == "purchase" and h["unit_price"] > 0), None) or item.cost_price or 0
    # Every candidate in priority order; the first one is the suggestion.
    cands = []
    if mine:
        h = mine[0]
        cands.append((h["unit_price"], f"last sold to this customer ({h['doc_code']}{_when(h['date'])})"))
    if quoted:
        cands.append((quoted[0].unit_price, f"last quoted to this customer ({quoted[1].code}{_when(quoted[1].quote_date)})"))
    if sales and not (mine and sales[0] is mine[0]):
        h = sales[0]
        cands.append((h["unit_price"], f"last sold to {h['party']} ({h['doc_code']}{_when(h['date'])})"))
    if item.selling_price:
        cands.append((item.selling_price, "item's selling price"))
    price, basis = cands[0] if cands else (0, "no price history")
    others, seen = [], {round(price, 5)}
    for p, b in cands[1:]:
        if round(p, 5) not in seen and b != "item's selling price":
            seen.add(round(p, 5))
            others.append({"price": p, "basis": b})
    return {"price": price, "basis": basis, "cost": cost, "others": others,
            "margin_pct": round((price - cost) / price * 100, 1) if price and cost else None,
            "history": [{"date": h["date"].isoformat() if h["date"] else None, "doc": h["doc_code"], "party": h["party"],
                         "qty": h["quantity"], "price": h["unit_price"], "kind": h["kind"]} for h in hist[:6]]}


def quoted_history(db: Session, item_id: int, customer_id: Optional[int] = None, exclude_quote_id: Optional[int] = None) -> List[dict]:
    """Every price this item was quoted at, newest first; this customer's quotes are flagged."""
    q = (db.query(QuoteLine, Quote, Customer).join(Quote, Quote.id == QuoteLine.quote_id)
         .join(Customer, Customer.id == Quote.customer_id).filter(QuoteLine.item_id == item_id))
    if exclude_quote_id:
        q = q.filter(Quote.id != exclude_quote_id)
    return [{"quote_id": qt.id, "code": qt.code, "date": qt.quote_date.isoformat() if qt.quote_date else None,
             "customer_id": c.id, "customer": c.name, "mine": c.id == customer_id, "status": qt.status,
             "qty": l.quantity, "price": l.unit_price}
            for l, qt, c in q.order_by(Quote.quote_date.desc(), Quote.id.desc()).limit(200).all()]


def parse_text(db: Session, customer_id: int, text: str) -> List[dict]:
    items = db.query(StockItem).filter(StockItem.is_active == True, StockItem.is_generic == False).all()  # noqa: E712 (generic stock isn't sold)
    matcher = ItemMatcher(items, learned=item_alias.for_party(db, "customer", customer_id))
    by_id = {i.id: i for i in items}
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or SKIP.match(line) or not re.search(r"\d", line):
            continue
        qty, desc = split_qty(line)
        codes = [t for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-/.]{2,}", desc) if re.search(r"\d", t)]
        cands = matcher.rank(codes, desc)
        item_id = pick(cands)
        row = {"source": line, "quantity": qty or 1, "qty_found": qty is not None, "description": desc,
               "item_id": item_id, "candidates": cands[:4], "match": cands[0]["why"] if item_id and cands else None}
        if item_id:
            row["price"] = suggest_price(db, by_id[item_id], customer_id)
        out.append(row)
    return out
