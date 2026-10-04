"""File Matcher (super admin): point it at a folder of PDFs and
  1. attach each file to the customer order / PO whose number is in its file name -- only when that is
     certain (the number is a whole word of the name and points at exactly one record), and
  2. read the files nothing matched with the AI and create draft orders / POs from them, each with its
     PDF attached, when the read is complete (customer or vendor known, every line's item known).

Files are read straight from the server's disk, so the path is one this machine can see."""
import mimetypes
import re
from pathlib import Path
from typing import Dict, List, Optional

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_perm, require_any
from app.models import Attachment, CustomerOrder, PurchaseOrder, User
from app.routes.attachments import MAX_BYTES, store_file

router = APIRouter(prefix="/api/file-matcher", tags=["file-matcher"], dependencies=[Depends(require_perm("file_matcher"))])

KINDS = {"customer": ("customer_order", CustomerOrder), "vendor": ("purchase_order", PurchaseOrder)}
VENDOR_CATEGORIES = {"vendor_invoice", "vendor_quote", "purchase_order", "packing_list", "mtr", "bol", "other"}
MAX_FILES = 3000
PATHSEP = re.compile(r"[\\/]")  # Windows or browser separators


def _norm(s: Optional[str]) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _folder(path: str) -> Path:
    p = Path((path or "").strip().strip('"'))
    if not p.is_absolute() or not p.is_dir():
        raise HTTPException(status_code=400, detail="That folder doesn't exist on the server (give the full path, e.g. C:\\...\\Purchase Orders)")
    return p.resolve()


def _file(folder: Path, rel: str) -> Path:
    f = (folder / rel).resolve()
    if folder not in f.parents or not f.is_file() or f.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail=f"{rel}: not a PDF in that folder")
    if f.stat().st_size > MAX_BYTES:
        raise HTTPException(status_code=400, detail=f"{rel} is larger than 25 MB")
    return f


def _name_keys(stem: str) -> set:
    """Every whole word of the file name, and runs of up to 4 words ("AE 010 24-25"), normalised."""
    parts = [p for p in re.split(r"[\s_]+", stem) if p]
    keys = set()
    for i in range(len(parts)):
        for j in range(i + 1, min(i + 5, len(parts) + 1)):
            keys.add(_norm("".join(parts[i:j])))
    return keys


def _record_keys(db: Session, kind: str) -> Dict[str, List[int]]:
    """number on the record -> record ids (a number on two records can't decide anything)."""
    out: Dict[str, List[int]] = {}
    if kind == "customer":
        rows = [(o.id, [o.po_number]) for o in db.query(CustomerOrder).filter(CustomerOrder.status != "cancelled").all()]
    else:
        rows = [(p.id, [p.vendor_so_number, p.code]) for p in db.query(PurchaseOrder).filter(PurchaseOrder.status != "cancelled").all()]
    for rid, nums in rows:
        for n in nums:
            k = _norm(n)
            if len(k) >= 5 and rid not in out.setdefault(k, []):
                out[k].append(rid)
    return out


def _plan(db: Session, folder: Path, kind: str) -> List[dict]:
    files = sorted(f for f in folder.rglob("*") if f.is_file() and f.suffix.lower() == ".pdf")
    return _plan_names(db, [(str(f.relative_to(folder)), f.stat().st_size) for f in files], kind)


def _plan_names(db: Session, files: List[tuple], kind: str) -> List[dict]:
    """files: [(relative path, size)] -- from a server folder or a folder picked in the browser."""
    entity_type, model = KINDS[kind]
    files = sorted((rel, size) for rel, size in files if rel.lower().endswith(".pdf"))
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"{len(files)} PDFs in that folder -- pick a smaller folder (max {MAX_FILES})")
    keys = _record_keys(db, kind)
    recs = {r.id: r for r in db.query(model).all()}
    atts: Dict[int, List[Attachment]] = {}
    for a in db.query(Attachment).filter(Attachment.entity_type == entity_type).all():
        atts.setdefault(a.entity_id, []).append(a)
    rows = []
    for rel, size in files:
        name = PATHSEP.split(rel)[-1]
        hit = {}
        for k in _name_keys(name.rsplit(".", 1)[0]):
            for rid in keys.get(k, []):
                hit.setdefault(rid, []).append(k)
            if len(keys.get(k, [])) > 1:
                hit["shared"] = True
        row = {"file": rel, "size": size, "status": "no_match", "record_id": None, "record": None, "why": ""}
        ids = [i for i in hit if i != "shared"]
        if hit.get("shared"):
            row.update(status="ambiguous", why="that number is on more than one record")
        elif len(ids) > 1:
            row.update(status="ambiguous", why="name holds numbers of " + ", ".join(recs[i].code for i in ids))
        elif ids:
            r = recs[ids[0]]
            row.update(record_id=r.id, record=r.code, status="match", why=f"{hit[ids[0]][0]} in the file name")
            have = atts.get(r.id, [])
            if any(a.filename.lower() == name.lower() for a in have):
                row.update(status="attached", why="this file is already on it")
            elif kind == "customer" and any(a.category == "customer_po" for a in have):
                row.update(status="has_one", why="already has a customer PO")
        rows.append(row)
    if kind == "customer":  # two files for one order (a revision?) -- neither is certain
        per = {}
        for r in rows:
            if r["status"] == "match":
                per.setdefault(r["record_id"], []).append(r)
        for rs in per.values():
            if len(rs) > 1:
                for r in rs:
                    r.update(status="ambiguous", why=f"{len(rs)} files point at {r['record']}")
    return rows


class ScanRequest(BaseModel):
    path: str
    kind: str = "customer"


@router.post("/scan")
def scan(req: ScanRequest, db: Session = Depends(get_db)):
    if req.kind not in KINDS:
        raise HTTPException(status_code=400, detail="kind is customer or vendor")
    folder = _folder(req.path)
    return {"folder": str(folder), "files": _plan(db, folder, req.kind)}


class AttachRequest(BaseModel):
    path: str
    kind: str = "customer"
    files: List[str]
    category: Optional[str] = None  # vendor docs: vendor_invoice / vendor_quote / ...; "auto" guesses from the name


def _vendor_category(name: str, chosen: Optional[str]) -> str:
    if chosen in VENDOR_CATEGORIES:
        return chosen
    n = name.lower()
    if re.search(r"inv", n):
        return "vendor_invoice"
    if re.search(r"packing|pack list|\bpl\b", n):
        return "packing_list"
    if re.search(r"mtr|cert|test report", n):
        return "mtr"
    return "vendor_quote" if re.search(r"\bso\b|sales order|confirm|quote|pi\b|proforma", n) else "other"


@router.post("/attach")
def attach(req: AttachRequest, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """Attach the chosen files -- each is re-checked here and only a certain match is attached."""
    folder = _folder(req.path)
    entity_type, _ = KINDS[req.kind]
    plan = {r["file"]: r for r in _plan(db, folder, req.kind)}
    done, skipped = [], []
    for rel in req.files:
        r = plan.get(rel)
        if not r or r["status"] != "match":
            skipped.append({"file": rel, "why": (r or {}).get("why") or "not a certain match"})
            continue
        f = _file(folder, rel)
        category = "customer_po" if req.kind == "customer" else _vendor_category(f.name, req.category)
        store_file(db, entity_type, r["record_id"], category, f.name, mimetypes.guess_type(f.name)[0], f.read_bytes(),
                   "Matched by number in file name (File Matcher)", user.username)
        done.append({"file": rel, "record": r["record"], "record_id": r["record_id"], "category": category})
    db.commit()
    return {"attached": done, "skipped": skipped}


class ReadRequest(BaseModel):
    path: str
    kind: str = "customer"
    file: str


@router.post("/read")
def read(req: ReadRequest, db: Session = Depends(get_db)):
    """Read one PDF into a draft (nothing saved) and say whether it is complete enough to create."""
    f = _file(_folder(req.path), req.file)
    return _read_bytes(db, req.kind, f.read_bytes(), f.name, req.file)


def _read_bytes(db: Session, kind: str, data: bytes, name: str, label: str) -> dict:
    problems = []
    if kind == "customer":
        from app.services import ai_orders
        d = ai_orders.extract_order(db, data)
        if not d["customer"].get("customer_id"):
            problems.append(f"customer \"{d.get('customer_name') or '?'}\" not certain")
        if not d.get("po_number"):
            problems.append("no PO # read")
        elif db.query(CustomerOrder).filter(CustomerOrder.status != "cancelled", CustomerOrder.customer_id == d["customer"].get("customer_id"),
                                            CustomerOrder.po_number == d["po_number"]).first():
            problems.append(f"PO {d['po_number']} is already on an order")
        problems += d.get("problems") or []
    else:
        from app.services import ai_docs
        d = ai_docs.extract(db, "vendor_order", data, name)
        vid = (d.get("vendor") or {}).get("vendor_id")
        if not vid:
            problems.append(f"vendor \"{d.get('vendor_name') or '?'}\" not certain")
        if d.get("document_number") and vid and db.query(PurchaseOrder).filter(
                PurchaseOrder.vendor_id == vid, PurchaseOrder.status != "cancelled", PurchaseOrder.vendor_so_number == d["document_number"]).first():
            problems.append(f"SO {d['document_number']} is already on a PO")
    lines = d.get("lines") or []
    if not lines:
        problems.append("no lines read")
    unsure = [l for l in lines if not l.get("item_id")]
    if unsure:
        problems.append(f"{len(unsure)} line(s) need an item picked")
    return {"file": label, "draft": d, "problems": problems, "ready": not problems}


class CreateRequest(BaseModel):
    path: str
    kind: str = "customer"
    file: str


@router.post("/create")
def create(req: CreateRequest, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """Read the file again and create a draft order / PO from it, with the PDF attached -- only when the
    read is complete. Anything unsure is refused, to be done by hand in the New Order form."""
    f = _file(_folder(req.path), req.file)
    return _create_bytes(db, req.kind, f.read_bytes(), f.name, req.file, user)


def _create_bytes(db: Session, kind: str, data: bytes, name: str, label: str, user: User) -> dict:
    r = _read_bytes(db, kind, data, name, label)
    if not r["ready"]:
        raise HTTPException(status_code=400, detail="Not certain enough to create: " + "; ".join(r["problems"]))
    d = r["draft"]
    from app import schemas
    if kind == "customer":
        from app.services.crud import CustomerOrderService
        rec = CustomerOrderService.create(db, schemas.CustomerOrderCreate(
            customer_id=d["customer"]["customer_id"], po_number=d.get("po_number"), customer_po_date=d.get("order_date"),
            delivery_date=d.get("delivery_date"), job_number=d.get("job_number"), notes=d.get("notes"),
            ship_to_address=d.get("ship_to_address"),
            lines=[schemas.CustomerOrderLineCreate(item_id=l["item_id"], quantity=l.get("quantity") or 1, unit_price=l.get("unit_price") or 0,
                                                   delivery_date=l.get("delivery_date"), notes=l.get("line_note"),
                                                   source_code=l.get("customer_item_code") or l.get("item_code"),
                                                   source_description=l.get("description")) for l in d["lines"]]), user.username)
        store_file(db, "customer_order", rec.id, "customer_po", name, "application/pdf", data,
                   "Created from this file (File Matcher)", user.username)
    else:
        from app.services.crud import PurchaseOrderService
        rec = PurchaseOrderService.create(db, schemas.PurchaseOrderCreate(
            vendor_id=d["vendor"]["vendor_id"], vendor_so_number=d.get("document_number"), expected_date=d.get("expected_date"),
            notes=d.get("notes"),
            lines=[schemas.PurchaseOrderLineCreate(item_id=l["item_id"], quantity=l.get("quantity") or 1, unit_cost=l.get("unit_price") or 0,
                                                   vendor_item_code=l.get("vendor_item_code"), vendor_description=l.get("description"))
                   for l in d["lines"]]), user.username)
        store_file(db, "purchase_order", rec.id, "vendor_quote", name, "application/pdf", data,
                   "Created from this file (File Matcher)", user.username)
    db.commit()
    return {"file": label, "record_id": rec.id, "record": rec.code}


# ---- a folder picked in the browser: names are matched first, then only the needed files are uploaded ----
class NamesRequest(BaseModel):
    kind: str = "customer"
    files: List[dict]  # [{file: "2025/CHART_PO_4065826.PDF", size: 12345}]


@router.post("/scan-names")
def scan_names(req: NamesRequest, db: Session = Depends(get_db)):
    if req.kind not in KINDS:
        raise HTTPException(status_code=400, detail="kind is customer or vendor")
    return {"folder": None, "files": _plan_names(db, [(str(f.get("file", "")), int(f.get("size") or 0)) for f in req.files], req.kind)}


def _blob(f: UploadFile) -> bytes:
    data = f.file.read()
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(status_code=400, detail=f"{f.filename}: empty or larger than 25 MB")
    return data


@router.post("/attach-upload")
def attach_upload(kind: str = Form(...), all_names: str = Form(...), rels: str = Form(...), category: Optional[str] = Form(None),
                        files: List[UploadFile] = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """all_names: every PDF in the picked folder ([{file, size}]) so "one file per order" is judged on the
    whole folder; rels: the relative path of each uploaded file, in order. Re-checked here."""
    entity_type, _ = KINDS[kind]
    plan = {r["file"]: r for r in _plan_names(db, [(str(f["file"]), int(f.get("size") or 0)) for f in json.loads(all_names)], kind)}
    rels = json.loads(rels)
    done, skipped = [], []
    for rel, up in zip(rels, files):
        r = plan.get(rel)
        if not r or r["status"] != "match":
            skipped.append({"file": rel, "why": (r or {}).get("why") or "not a certain match"})
            continue
        name = PATHSEP.split(rel)[-1]
        cat = "customer_po" if kind == "customer" else _vendor_category(name, category)
        store_file(db, entity_type, r["record_id"], cat, name, "application/pdf", _blob(up),
                   "Matched by number in file name (File Matcher)", user.username)
        done.append({"file": rel, "record": r["record"], "record_id": r["record_id"], "category": cat})
    db.commit()
    return {"attached": done, "skipped": skipped}


@router.post("/read-upload")
def read_upload(kind: str = Form(...), rel: str = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db)):
    return _read_bytes(db, kind, _blob(file), PATHSEP.split(rel)[-1], rel)


@router.post("/create-upload")
def create_upload(kind: str = Form(...), rel: str = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db),
                        user: User = Depends(get_current_active_user)):
    return _create_bytes(db, kind, _blob(file), PATHSEP.split(rel)[-1], rel, user)
