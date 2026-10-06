"""Simulate: profit / loss before committing. A simulation is one working document (JSON) holding
- demand (left page): customer orders, quotes or pasted lines, each repeatable (order A x 8);
- sources (right page): POs, vendor quotes or pasted lines, each with its own extra costs (tariff, freight...),
  plus costs shared by every source;
- estimates for items nobody sources yet, and the spreadsheet view's extra columns.
The comparison is worked out on the page (simulate.js) as you type; the server reads pasted text, matches items, gives
price history, lets an AI edit the sheet, and turns a side into a draft PO / customer order."""
import json
import re
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_perm
from app.models import Simulation, StockItem, User, VendorItem
from app.services.permissions import has

router = APIRouter(prefix="/api/simulations", tags=["simulations"], dependencies=[Depends(require_perm("simulate"))])


def _get(db: Session, sim_id: int) -> Simulation:
    s = db.get(Simulation, sim_id)
    if not s:
        raise HTTPException(status_code=404, detail="Simulation not found")
    return s


def _out(s: Simulation, with_doc=True) -> dict:
    d = {"id": s.id, "name": s.name, "created_by": s.created_by, "created_at": s.created_at, "updated_by": s.updated_by, "updated_at": s.updated_at}
    if with_doc:
        d["doc"] = json.loads(s.doc or "{}")
    else:
        doc = json.loads(s.doc or "{}")
        d["summary"] = doc.get("summary") or {}
    return d


# ---------- the documents ----------
@router.get("/")
def list_sims(db: Session = Depends(get_db)):
    return [_out(s, with_doc=False) for s in db.query(Simulation).order_by(Simulation.updated_at.desc()).all()]


class SimIn(BaseModel):
    name: Optional[str] = None
    doc: Optional[Any] = None


@router.post("/")
def create_sim(data: SimIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    s = Simulation(name=(data.name or "").strip() or f"Simulation {datetime.utcnow():%b %d}", doc=json.dumps(data.doc or {}),
                   created_by=user.username, updated_by=user.username)
    db.add(s)
    db.commit()
    return _out(s)


@router.get("/{sim_id}")
def get_sim(sim_id: int, db: Session = Depends(get_db)):
    return _out(_get(db, sim_id))


@router.put("/{sim_id}")
def save_sim(sim_id: int, data: SimIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    s = _get(db, sim_id)
    if data.name is not None:
        if not data.name.strip():
            raise HTTPException(status_code=400, detail="Name the simulation")
        s.name = data.name.strip()
    if data.doc is not None:
        raw = json.dumps(data.doc)
        if len(raw) > 5_000_000:
            raise HTTPException(status_code=400, detail="This simulation is too big to save (over 5 MB)")
        s.doc = raw
    s.updated_by = user.username
    db.commit()
    return _out(s, with_doc=False)


@router.post("/{sim_id}/copy")
def copy_sim(sim_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    s = _get(db, sim_id)
    c = Simulation(name=f"{s.name} (copy)", doc=s.doc, created_by=user.username, updated_by=user.username)
    db.add(c)
    db.commit()
    return _out(c)


@router.delete("/{sim_id}", status_code=204)
def delete_sim(sim_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, sim_id))
    db.commit()
    return Response(status_code=204)


# ---------- pasted lines -> items ----------
PRICE_CELL = re.compile(r"^\$?\s*\d[\d,]*\.\d+$|^\$\s*\d[\d,]*$")
PRICE_TAIL = re.compile(r"(?:@\s*\$?|\$\s*)(\d[\d,]*(?:\.\d+)?)\s*(?:/?\s*(?:ea|each|pc|pcs|unit))?\s*$|(\d[\d,]*\.\d{2,5})\s*(?:/?\s*(?:ea|each))\s*$", re.I)


def _price_from(line: str):
    """(price, line without it): a cell like $1.25 / 0.4875 in a pasted sheet row, or '@ 1.25' / '$1.25 ea' at the end."""
    cells = [c.strip() for c in re.split(r"\t|\s{3,}|\|", line.strip()) if c.strip()]
    if len(cells) > 1:
        idx = [i for i, c in enumerate(cells) if PRICE_CELL.match(c)]
        if idx:
            i = idx[-1]
            return float(cells[i].replace("$", "").replace(",", "")), "\t".join(c for j, c in enumerate(cells) if j != i)
    m = PRICE_TAIL.search(line)
    if m:
        v = m.group(1) or m.group(2)
        return float(v.replace(",", "")), line[:m.start()].strip(" -,:")
    return None, line


class ParseIn(BaseModel):
    text: str
    side: str = "demand"  # demand (customer) | source (vendor)
    party_id: Optional[int] = None


@router.post("/parse")
def parse(data: ParseIn, db: Session = Depends(get_db)):
    """Pasted lines (an RFQ, a vendor quote, a spreadsheet) -> [{qty, description, code, price, item_id, candidates}].
    Our items are matched by code / the party's learned names / description; no match = not in our database."""
    from app.services import item_alias
    from app.services.item_match import ItemMatcher, pick
    from app.services.quotes import SKIP, split_qty
    items = db.query(StockItem).filter(StockItem.is_active.isnot(False)).all()
    if data.side == "source":
        xref = {m.vendor_item_code: m.item_id for m in db.query(VendorItem).filter(VendorItem.vendor_id == data.party_id).all()} if data.party_id else {}
        matcher = ItemMatcher(items, xref, item_alias.for_party(db, "vendor", data.party_id))
    else:
        matcher = ItemMatcher(items, learned=item_alias.for_party(db, "customer", data.party_id))
    by_id = {i.id: i for i in items}
    out = []
    for raw in (data.text or "").splitlines()[:500]:
        line = raw.strip()
        if not line or SKIP.match(line) or not re.search(r"\d", line):
            continue
        price, rest = _price_from(line)
        qty, desc = split_qty(rest)
        codes = [t for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-/.]{2,}", desc) if re.search(r"\d", t)]
        cands = matcher.rank(codes, desc)
        item_id = pick(cands)
        it = by_id.get(item_id)
        out.append({"source": line, "qty": qty or 1, "qty_found": qty is not None, "description": desc.strip(),
                    "code": it.code if it else (codes[0] if codes else ""), "price": price, "item_id": item_id,
                    "candidates": [{"item_id": c["item_id"], "code": by_id[c["item_id"]].code if c["item_id"] in by_id else "",
                                    "title": by_id[c["item_id"]].title if c["item_id"] in by_id else "", "why": c.get("why")} for c in cands[:3]]})
    return out


# ---------- price history / suggestions ----------
class ItemsIn(BaseModel):
    item_ids: List[int]
    customer_id: Optional[int] = None


@router.post("/insights")
def insights(data: ItemsIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """Per item: who bought it from us and at what, who we bought it from and at what (newest first), stock, the
    suggested selling price and the last cost."""
    if not has(user, "money.view"):
        raise HTTPException(status_code=403, detail="Simulating profit needs the role that sees prices")
    from app.services.crud import price_history
    from app.services.quotes import suggest_price
    out = {}
    for it in db.query(StockItem).filter(StockItem.id.in_(set(data.item_ids[:300]) or {0})).all():
        hist = price_history(db, it.id)
        sales = [h for h in hist if h["kind"] == "sale" and h["status"] != "cancelled"][:8]
        buys = [h for h in hist if h["kind"] == "purchase" and h["status"] != "cancelled" and h["unit_price"]][:8]
        row = lambda h: {"date": h["date"], "doc_id": h["doc_id"], "doc": h["doc_code"], "party": h["party"], "qty": h["quantity"], "price": h["unit_price"]}
        sugg = suggest_price(db, it, data.customer_id or 0) if data.customer_id else None
        out[str(it.id)] = {"code": it.code, "title": it.title, "on_hand": it.on_hand, "available": (it.on_hand or 0) - (it.booked or 0),
                           "pack_size": it.default_pack_size, "selling_price": it.selling_price, "cost_price": it.cost_price,
                           "last_cost": buys[0]["unit_price"] if buys else (it.cost_price or None),
                           "last_sale": sales[0]["unit_price"] if sales else None,
                           "suggested_price": sugg["price"] if sugg else (sales[0]["unit_price"] if sales else it.selling_price),
                           "sales": [row(h) for h in sales], "purchases": [row(h) for h in buys]}
    return out


# ---------- the spreadsheet's AI assistant ----------
class SheetIn(BaseModel):
    instruction: str
    columns: List[str]
    rows: List[dict]
    engine: str = "local"  # local (private, this network) | claude (cloud, on an explicit click)


SHEET_PROMPT = """You edit a pricing spreadsheet for a fastener distributor. The user gives an instruction and the sheet as JSON
(columns, and rows that each keep their "_id"). Do exactly what the instruction asks and nothing else. Return JSON:
{"columns": [...all column names, in order, including any you add...],
 "rows": [...every row after the change; keep each existing row's "_id"; a new row has "_id": null; leave out rows you delete...],
 "note": "one short sentence saying what you changed"}
Keep numbers as numbers. Do not invent items. Instruction and sheet:
"""


@router.post("/ai-sheet")
def ai_sheet(data: SheetIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    if not data.instruction.strip():
        raise HTTPException(status_code=400, detail="Tell the assistant what to do")
    if len(data.rows) > 400:
        raise HTTPException(status_code=400, detail="The assistant works on up to 400 rows at a time -- filter the sheet first")
    payload = json.dumps({"instruction": data.instruction.strip(), "columns": data.columns, "rows": data.rows}, default=str)
    if data.engine == "claude":
        if not has(user, "ai"):
            raise HTTPException(status_code=403, detail="Ask Claude (cloud) needs the AI permission")
        from app.services import ai_cloud
        safe, removed = ai_cloud.redact(payload, db)  # our / customer / vendor names never leave
        schema = {"type": "object", "additionalProperties": False, "required": ["columns", "rows", "note"],
                  "properties": {"columns": {"type": "array", "items": {"type": "string"}},
                                 "rows": {"type": "array", "items": {"type": "object"}}, "note": {"type": "string"}}}
        result = ai_cloud.ask_claude(SHEET_PROMPT, safe, schema)
        out = result["data"]
        out["engine"] = f"Claude ({result['model']})"
        out["redacted"] = removed
    else:
        from app.services.ai_orders import _ask_model
        out = _ask_model(payload, prompt=SHEET_PROMPT)
        out["engine"] = "Local AI"
    if not isinstance(out.get("rows"), list) or not isinstance(out.get("columns"), list):
        raise HTTPException(status_code=502, detail="The assistant didn't return a sheet -- try saying it differently")
    return out


# ---------- turn a side into real documents ----------
class LineIn(BaseModel):
    item_id: int
    quantity: float
    price: float = 0


class MakePoIn(BaseModel):
    vendor_id: int
    lines: List[LineIn]
    notes: Optional[str] = None


@router.post("/create-po", dependencies=[Depends(require_perm("purchasing"))])
def create_po(data: MakePoIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """A draft PO from the simulation (nothing is sent: check it, mark it ordered and send it as usual)."""
    from types import SimpleNamespace
    from app.services.crud import PurchaseOrderService
    lines = [l for l in data.lines if l.quantity > 0]
    if not lines:
        raise HTTPException(status_code=400, detail="No quantities to order")
    po = PurchaseOrderService.create(db, SimpleNamespace(
        vendor_id=data.vendor_id, expected_date=None, vendor_so_number=None, notes=data.notes or "Drafted from a simulation",
        lines=[SimpleNamespace(item_id=l.item_id, quantity=round(l.quantity), unit_cost=round(l.price or 0, 5), vendor_item_code=None,
                               vendor_description=None, notes=None, print_notes=True) for l in lines]), user.username)
    return {"id": po.id, "code": po.code}


class MakeOrderIn(BaseModel):
    customer_id: int
    po_number: Optional[str] = None
    job_number: Optional[str] = None
    lines: List[LineIn]


@router.post("/create-order", dependencies=[Depends(require_perm("orders.edit"))])
def create_order(data: MakeOrderIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """A draft customer order from the simulation's demand (confirm it on the order as usual)."""
    from types import SimpleNamespace
    from app.services.crud import CustomerOrderService
    lines = [l for l in data.lines if l.quantity > 0]
    if not lines:
        raise HTTPException(status_code=400, detail="No quantities on these lines")
    o = CustomerOrderService.create(db, SimpleNamespace(
        allow_duplicate=True, customer_id=data.customer_id, delivery_date=None, po_number=(data.po_number or "").strip() or None,
        customer_po_date=None, job_number=(data.job_number or "").strip() or None, ship_to_address=None, notes="Drafted from a simulation",
        lines=[SimpleNamespace(item_id=l.item_id, quantity=round(l.quantity), unit_price=round(l.price or 0, 5), delivery_date=None,
                               source_code=None, source_description=None, notes=None, print_notes=True) for l in lines]), user.username)
    return {"id": o.id, "code": o.code}
