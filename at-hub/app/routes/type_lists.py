"""Types & tags people can add to: document types, S&H / charge types, landed cost types, payment methods
(app/services/type_lists.py). Anyone signed in reads them; adding / renaming / hiding needs "types.manage"."""
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_perm
from app.models import Attachment, LandedCost, PurchaseOrderCharge, TypeOption, User
from app.services import type_lists

router = APIRouter(prefix="/api/types", tags=["types"], dependencies=[Depends(get_current_active_user)])


class TypeIn(BaseModel):
    list: str
    label: str
    scopes: Optional[List[str]] = None  # document types: customer_order / purchase_order / shipment
    money: bool = False  # document types with prices (managers only)
    force: bool = False  # create although a similar one exists


class TypeUpdate(BaseModel):
    label: Optional[str] = None
    scopes: Optional[List[str]] = None
    money: Optional[bool] = None
    active: Optional[bool] = None
    force: bool = False


def _out(r: TypeOption, used: int = None) -> dict:
    return {"id": r.id, "list": r.list, "key": r.key, "label": r.label, "scopes": r.scopes.split(",") if r.scopes else None,
            "money": r.money, "builtin": r.builtin, "active": r.active, "created_by": r.created_by, "used": used}


def _usage(db: Session, lst: str) -> dict:
    from sqlalchemy import func
    from app.models import InvoicePayment, PurchaseOrderPayment, VendorPayment
    if lst == "attachment":
        return dict(db.query(Attachment.category, func.count(Attachment.id)).group_by(Attachment.category).all())
    if lst == "charge":
        return dict(db.query(PurchaseOrderCharge.charge_type, func.count(PurchaseOrderCharge.id)).group_by(PurchaseOrderCharge.charge_type).all())
    if lst == "landed_cost":
        return dict(db.query(LandedCost.cost_type, func.count(LandedCost.id)).group_by(LandedCost.cost_type).all())
    out = {}
    for model in (InvoicePayment, PurchaseOrderPayment, VendorPayment):
        for k, n in db.query(model.method, func.count(model.id)).group_by(model.method).all():
            out[k] = out.get(k, 0) + n
    return out


@router.get("/")
def list_types(list: Optional[str] = None, scope: Optional[str] = None, all: bool = False, usage: bool = False,
               db: Session = Depends(get_db)):
    """One list (or every list): the active entries, or all with all=true (Company Settings); usage counts on request."""
    names = [list] if list else type_lists.LISTS.keys()
    out = {}
    for name in names:
        used = _usage(db, name) if usage else {}
        out[name] = {**type_lists.LISTS[name], "options": [_out(r, used.get(r.key, 0) if usage else None)
                                                         for r in type_lists.options(db, name, scope, include_inactive=all)]}
    return out[list] if list else out


@router.get("/check")
def check(list: str, label: str, db: Session = Depends(get_db)):
    """As someone types a new name: how it will be saved, and anything already there that looks like it."""
    clean = type_lists.normalize_label(label)
    return {"label": clean, "key": type_lists.slug(clean),
            "similar": [_out(r) for r in type_lists.similar(db, list, clean)] if len(clean) >= 2 else []}


@router.post("/", dependencies=[Depends(require_perm("types.manage"))])
def create(data: TypeIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    return _out(type_lists.create(db, data.list, data.label, data.scopes, data.money, data.force, user.username))


@router.put("/{type_id}", dependencies=[Depends(require_perm("types.manage"))])
def update(type_id: int, data: TypeUpdate, db: Session = Depends(get_db)):
    return _out(type_lists.update(db, type_id, data.label, data.scopes, data.money, data.active, data.force))
