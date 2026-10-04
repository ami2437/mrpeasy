"""CSV import for stock items, customers and vendors: preview first, then apply.

Columns are recognised by name (many spellings: "Code", "Item code", "Part no"...). A row updates the
record with the same item code / name, otherwise creates one. Only the columns present are changed.
"""
import csv
import io
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import require_role
from app.models import Customer, StockItem, Vendor
from app.services.crud import ProductGroupService, generate_code

router = APIRouter(prefix="/api/import", tags=["import"], dependencies=[Depends(require_role("manager"))])

FIELDS = {
    "items": {
        "code": ["code", "item code", "item", "part no", "part number", "part #", "sku", "number"],
        "title": ["title", "description", "name", "item title", "part description"],
        "category": ["group", "product group", "category"],
        "unit": ["unit", "uom"],
        "selling_price": ["selling price", "price", "sale price", "unit price"],
        "cost_price": ["cost", "cost price", "unit cost"],
        "reorder_point": ["reorder point", "reorder", "min stock", "minimum"],
        "default_pack_size": ["pack size", "default pack size", "box qty", "units per box"],
        "barcode": ["barcode", "upc", "ean"],
    },
    "customers": {
        "name": ["name", "customer", "company", "customer name"],
        "contact_name": ["contact", "contact name", "buyer"],
        "email": ["email", "e-mail"],
        "phone": ["phone", "telephone", "tel"],
        "address": ["address", "billing address", "bill to"],
        "shipping_address": ["shipping address", "ship to", "delivery address"],
    },
}
FIELDS["vendors"] = {**FIELDS["customers"], "name": ["name", "vendor", "supplier", "company", "vendor name"]}
NUMERIC = {"selling_price", "cost_price", "reorder_point", "default_pack_size"}
KEY = {"items": "code", "customers": "name", "vendors": "name"}
MODEL = {"items": StockItem, "customers": Customer, "vendors": Vendor}


def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9#]+", " ", (h or "").lower()).strip()


def _parse(kind: str, data: bytes) -> Dict[str, Any]:
    if kind not in FIELDS:
        raise HTTPException(status_code=404, detail="Can import items, customers or vendors")
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise HTTPException(status_code=400, detail="The file is empty")
    header = [_norm(h) for h in rows[0]]
    mapping = {}
    for field, names in FIELDS[kind].items():
        for i, h in enumerate(header):
            if h in names and i not in mapping.values():
                mapping[field] = i
                break
    if KEY[kind] not in mapping:
        raise HTTPException(status_code=400, detail=f"No '{FIELDS[kind][KEY[kind]][0]}' column found. Columns seen: {', '.join(rows[0])}")
    return {"mapping": mapping, "header": rows[0], "rows": rows[1:]}


def _plan(kind: str, parsed: Dict[str, Any], db: Session) -> List[Dict[str, Any]]:
    model, key = MODEL[kind], KEY[kind]
    existing = {(getattr(r, key) or "").strip().lower(): r for r in db.query(model).all()}
    out, seen = [], set()
    for n, row in enumerate(parsed["rows"], start=2):
        vals, errors = {}, []
        for field, i in parsed["mapping"].items():
            v = row[i].strip() if i < len(row) else ""
            if v == "":
                continue
            if field in NUMERIC:
                try:
                    v = float(v.replace("$", "").replace(",", ""))
                    if field == "default_pack_size":
                        v = int(v)
                except ValueError:
                    errors.append(f"{field.replace('_', ' ')} '{v}' isn't a number")
                    continue
            if field == "category":
                group = ProductGroupService.canonical(db, v)
                if not group:
                    errors.append(f"'{v}' isn't one of the product groups")
                    continue
                v = group
            if kind == "items" and field == "code":
                from app.services.item_naming import normalize_code
                v = normalize_code(v)  # 15420-NUTS -> 15420-NUT, same as typing it
            vals[field] = v
        k = (vals.get(key) or "").strip().lower()
        if not k:
            errors.append(f"no {key}")
        elif k in seen:
            errors.append(f"{vals[key]} appears twice in the file")
        seen.add(k)
        rec = existing.get(k)
        if kind == "items" and not rec and "category" not in vals and not errors:
            errors.append("a new item needs a product group")
        changes = {f: v for f, v in vals.items() if f != key and (not rec or getattr(rec, f) != v)} if not errors else {}
        out.append({"row": n, "key": vals.get(key) or "", "action": "error" if errors else ("update" if rec else "create") if (changes or not rec) else "same",
                    "changes": {f: v for f, v in changes.items()}, "errors": errors})
    return out


@router.post("/{kind}/preview")
def preview(kind: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    parsed = _parse(kind, file.file.read())
    plan = _plan(kind, parsed, db)
    return {"columns": {f: parsed["header"][i] for f, i in parsed["mapping"].items()},
            "ignored": [h for i, h in enumerate(parsed["header"]) if i not in parsed["mapping"].values()],
            "rows": plan, "counts": {a: sum(1 for r in plan if r["action"] == a) for a in ("create", "update", "same", "error")}}


@router.post("/{kind}/apply")
def apply(kind: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    parsed = _parse(kind, file.file.read())
    plan = _plan(kind, parsed, db)
    model, key = MODEL[kind], KEY[kind]
    existing = {(getattr(r, key) or "").strip().lower(): r for r in db.query(model).all()}
    done = {"created": 0, "updated": 0, "skipped": 0}
    for p in plan:
        if p["action"] in ("error", "same"):
            done["skipped"] += 1
            continue
        rec = existing.get(p["key"].strip().lower())
        if rec:
            for f, v in p["changes"].items():
                setattr(rec, f, v)
            done["updated"] += 1
        else:
            rec = model(**{key: p["key"], **p["changes"]})
            if kind == "items" and not rec.title:
                rec.title = p["key"]
            if model is Vendor:
                rec.code = generate_code(db, Vendor, "V")
            db.add(rec)
            db.flush()
            done["created"] += 1
    db.commit()
    return done
