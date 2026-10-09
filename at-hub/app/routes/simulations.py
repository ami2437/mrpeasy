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
    from app.models import PurchaseOrder, PurchaseOrderLine
    from app.services.crud import price_history
    from app.services.quotes import suggest_price
    ids = set(data.item_ids[:300]) or {0}
    # still coming in: open PO lines (ordered / partly received), per PO so the page can leave out POs it already counts
    incoming = {}
    for line, po in (db.query(PurchaseOrderLine, PurchaseOrder).join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
                     .filter(PurchaseOrderLine.item_id.in_(ids), PurchaseOrder.status.in_(("ordered", "shipped", "partially_received"))).all()):
        left = (line.quantity or 0) - (line.received_quantity or 0)
        if left > 1e-9:
            incoming.setdefault(line.item_id, []).append({"po_id": po.id, "code": po.code, "vendor_id": po.vendor_id, "qty": left,
                                                          "expected": po.expected_date})
    out = {}
    for it in db.query(StockItem).filter(StockItem.id.in_(ids)).all():
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
                           "sales": [row(h) for h in sales], "purchases": [row(h) for h in buys],
                           "incoming": incoming.get(it.id, [])}
        # the most recent purchase, with its vendor (the order tracker suggests it), or the item card's cost
        if buys:
            po = db.get(PurchaseOrder, buys[0]["doc_id"])
            out[str(it.id)]["last_buy"] = {"price": buys[0]["unit_price"], "date": buys[0]["date"], "doc": buys[0]["doc_code"],
                                           "doc_id": buys[0]["doc_id"], "party": buys[0]["party"], "vendor_id": po.vendor_id if po else None}
        elif it.cost_price:
            out[str(it.id)]["last_buy"] = {"price": it.cost_price, "date": None, "doc": None, "doc_id": None, "party": "item cost", "vendor_id": None}
        else:
            out[str(it.id)]["last_buy"] = None
    return out


# ---------- what changed since the simulation was last worked on ----------
OPEN_PO = ("draft", "ordered", "shipped", "partially_received")
OPEN_QUOTE = ("draft", "sent", "accepted")


@router.post("/{sim_id}/changes")
def changes(sim_id: int, db: Session = Depends(get_db)):
    """For the pop-up on opening a simulation:
    linked -- the current state of every order / quote / PO the simulation was built from, and of the draft POs its
              order tracker created (status, lines, received), so the page can say what changed and follow its POs;
    open   -- open records that touch the simulation's items: POs (draft / ordered / partly received), CONFIRMED
              customer orders, and quotes (asked about separately). The page leaves out what it already holds or has
              seen, and asks about the rest."""
    from app.models import Customer, CustomerOrder, CustomerOrderLine, PurchaseOrder, PurchaseOrderLine, Quote, QuoteLine, Vendor
    s = _get(db, sim_id)
    try:
        d = json.loads(s.doc or "{}")
    except ValueError:
        d = {}
    lines_of = lambda side: [l for b in d.get(side) or [] for l in b.get("lines") or []]  # noqa: E731
    item_ids = {l.get("item_id") for l in lines_of("demand") + lines_of("sources") if isinstance(l.get("item_id"), int)}
    refs = {"order": set(), "quote": set(), "po": set()}
    for b in d.get("demand") or []:
        if isinstance(b.get("ref_id"), int):
            refs["quote" if b.get("kind") == "quote" else "order"].add(b["ref_id"])
    for b in d.get("sources") or []:
        if isinstance(b.get("ref_id"), int) and "purchase-orders" in (b.get("ref_link") or ""):
            refs["po"].add(b["ref_id"])
    for t in (d.get("tracker") or {}).values():
        if isinstance(t, dict) and isinstance(t.get("po_id"), int):
            refs["po"].add(t["po_id"])
    codes = {i.id: i.code for i in db.query(StockItem.id, StockItem.code).filter(StockItem.id.in_(item_ids or {0})).all()}
    cust = lambda cid: (db.get(Customer, cid).name if cid and db.get(Customer, cid) else "")  # noqa: E731
    vend = lambda vid: (db.get(Vendor, vid).name if vid and db.get(Vendor, vid) else "")  # noqa: E731
    when = lambda dt: dt.isoformat() + "Z" if dt else None  # noqa: E731

    def po_out(po, only=None):
        ls = [l for l in po.lines if only is None or l.item_id in only]
        return {"id": po.id, "code": po.code, "status": po.status, "party_id": po.vendor_id, "party": vend(po.vendor_id),
                "created_by": po.created_by, "created_at": when(po.created_at),
                "lines": [{"item_id": l.item_id, "code": codes.get(l.item_id) or (db.get(StockItem, l.item_id).code if db.get(StockItem, l.item_id) else ""),
                           "qty": l.quantity, "price": l.unit_cost, "received": l.received_quantity or 0} for l in ls]}

    def co_out(o, only=None):
        ls = [l for l in o.lines if only is None or l.item_id in only]
        return {"id": o.id, "code": o.code, "status": o.status, "party_id": o.customer_id, "party": cust(o.customer_id), "po_number": o.po_number,
                "created_by": o.created_by, "created_at": when(o.created_at),
                "lines": [{"item_id": l.item_id, "code": codes.get(l.item_id, ""), "qty": l.quantity, "price": l.unit_price} for l in ls]}

    def q_out(q, only=None):
        ls = [l for l in q.lines if only is None or l.item_id in only]
        return {"id": q.id, "code": q.code, "status": q.status, "party_id": q.customer_id, "party": cust(q.customer_id),
                "created_by": q.created_by, "created_at": when(q.created_at),
                "lines": [{"item_id": l.item_id, "code": codes.get(l.item_id, ""), "qty": l.quantity, "price": l.unit_price} for l in ls]}

    linked = {}
    for kind, model, out in (("order", CustomerOrder, co_out), ("quote", Quote, q_out), ("po", PurchaseOrder, po_out)):
        found = {r.id: r for r in db.query(model).filter(model.id.in_(refs[kind] or {0})).all()}
        for rid in refs[kind]:
            linked[f"{kind}:{rid}"] = out(found[rid]) if rid in found else {"id": rid, "missing": True}
    ids = item_ids or {0}
    pos = (db.query(PurchaseOrder).join(PurchaseOrderLine, PurchaseOrderLine.po_id == PurchaseOrder.id)
           .filter(PurchaseOrder.status.in_(OPEN_PO), PurchaseOrderLine.item_id.in_(ids)).distinct().all())
    orders = (db.query(CustomerOrder).join(CustomerOrderLine, CustomerOrderLine.order_id == CustomerOrder.id)
              .filter(CustomerOrder.status == "confirmed", CustomerOrderLine.item_id.in_(ids)).distinct().all())
    quotes = (db.query(Quote).join(QuoteLine, QuoteLine.quote_id == Quote.id)
              .filter(Quote.status.in_(OPEN_QUOTE), QuoteLine.item_id.in_(ids)).distinct().all())
    return {"linked": linked, "open": {"pos": [po_out(p, item_ids) for p in pos], "orders": [co_out(o, item_ids) for o in orders],
                                       "quotes": [q_out(q, item_ids) for q in quotes]}}


# ---------- export: everything on the page as Excel / PDF / CSV ----------
class ExportIn(BaseModel):
    name: str
    fmt: str = "xlsx"
    facts: List[List[Any]] = []
    sheets: List[dict]


@router.post("/export")
def export(data: ExportIn, user: User = Depends(get_current_active_user)):
    """The page sends its tables (summary, sell, buy, extra costs, item by item, profit per order, order tracker)
    exactly as shown; this only turns them into a file."""
    if not has(user, "money.view"):
        raise HTTPException(status_code=403, detail="Exporting a simulation needs the role that sees prices")
    from app.services import filenames, sheet_export
    name = re.sub(r"[^\w .,()&-]+", "", data.name or "Simulation").strip() or "Simulation"
    if sum(len(s.get("rows") or []) for s in data.sheets) > 20000:
        raise HTTPException(status_code=400, detail="Too many rows to export")
    if data.fmt == "pdf":
        body, media, ext = sheet_export.to_pdf(name, data.sheets, data.facts), "application/pdf", "pdf"
    elif data.fmt == "csv":
        body, media, ext = sheet_export.to_csv(name, data.sheets, data.facts), "text/csv", "csv"
    elif data.fmt == "xlsx":
        body, media, ext = sheet_export.to_xlsx(name, data.sheets, data.facts), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    else:
        raise HTTPException(status_code=400, detail="Export as xlsx, pdf or csv")
    return Response(body, media_type=media, headers={"Content-Disposition": filenames.disposition(f"{name}.{ext}", inline=False)})


# ---------- generic nuts ----------
_GENERIC_CODE = re.compile(r"^(\d{1,3})-NUT$", re.I)  # a size code (34, 58, 114), not a 5-digit bolt #


def looks_generic(it: StockItem, codes: set, parents: set) -> Optional[str]:
    """Why this item is bulk stock that specific nuts draw from -- or None. Flagged generic, drawn from already,
    or coded <size>-NUT (58-NUT, 1-3 digits) where the front isn't one of our bolts (15420-NUT is 15420's own nut)."""
    if it.is_generic:
        return "marked generic"
    if it.id in parents:
        return "other items draw from it"
    m = _GENERIC_CODE.match(it.code or "")
    if m and m.group(1).upper() not in codes:
        return "treated as generic (not marked on the item)"
    return None


@router.post("/generic")
def generic(data: ItemsIn, db: Session = Depends(get_db)):
    """Which items in the simulation are generic bulk nuts, and which specific nuts each one can stand in for
    (same size, thread and grade as booking uses: "exact", or "check" when a finish / heavy is unstated)."""
    from app.services import stock_transfer
    ids = set(data.item_ids[:500])
    items = db.query(StockItem).filter(StockItem.id.in_(ids or {0})).all()
    codes = {c.upper() for (c,) in db.query(StockItem.code).all()}
    parents = {p for (p,) in db.query(StockItem.parent_item_id).filter(StockItem.parent_item_id.isnot(None)).distinct().all()}
    gens = {it.id: (it, why) for it in items for why in [looks_generic(it, codes, parents)] if why}
    gspec = {gid: stock_transfer.spec(g.title) for gid, (g, _) in gens.items()}
    serves = {}
    for it in items:
        if it.id in gens:
            continue
        mine = stock_transfer.spec(it.title)
        opts = []
        if it.parent_item_id in gens:
            opts.append({"generic_id": it.parent_item_id, "match": "linked"})
        if mine["dia"]:
            opts += [{"generic_id": gid, "match": how} for gid, gs in gspec.items() if gid != it.parent_item_id
                     for how in [stock_transfer.match(mine, gs)] if how]
        if opts:
            serves[str(it.id)] = opts
    return {"generics": {str(gid): {"code": g.code, "title": g.title, "why": why} for gid, (g, why) in gens.items()}, "serves": serves}


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
    from app.services import ai_cloud as _cloud
    if _cloud.claude_engine():
        data.engine = "claude"  # no local model on this server
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
