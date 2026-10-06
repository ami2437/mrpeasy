"""Template Designer (admin and above): templates for invoices, packing lists, POs, quotes and labels --
start from a ready-made design or blank, customize, set the default (overall or per customer), preview
with a real record. Printing anywhere uses the default automatically."""
import json
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_perm, require_any
from app.models import Customer, CustomerOrder, DocTemplate, Invoice, PurchaseOrder, Quote, Shipment, User
from app.services import doc_context, template_engine, template_starters
from app.services.templates import default_for

router = APIRouter(prefix="/api/templates", tags=["templates"])
admin = [Depends(require_perm("templates"))]
CUSTOMER_DOCS = {"invoice", "packing_list", "quote", "box_label"}
RECORD_MODEL = {"invoice": Invoice, "packing_list": Shipment, "purchase_order": PurchaseOrder, "quote": Quote}


def _out(t: DocTemplate, with_spec=True):
    d = {"id": t.id, "doc_type": t.doc_type, "name": t.name, "is_default": bool(t.is_default), "customer_id": t.customer_id,
         "starter": t.starter, "updated_by": t.updated_by or t.created_by, "updated_at": t.updated_at.isoformat() + "Z" if t.updated_at else None}
    if with_spec:
        d["spec"] = json.loads(t.spec)
    return d


def _get(db, tid) -> DocTemplate:
    t = db.get(DocTemplate, tid)
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    return t


@router.get("/types", dependencies=[Depends(get_current_active_user)])
def types():
    """Document types, the fields their text can show and the columns their table can have."""
    return [{"key": k, "label": v, "label_kind": k in doc_context.LABEL_TYPES, "per_customer": k in CUSTOMER_DOCS,
             "fields": [{"key": f, "label": l} for f, l in doc_context.FIELDS[k]],
             "columns": [{"key": c, "label": l} for c, l in doc_context.COLUMNS.get(k, [])]} for k, v in doc_context.DOC_TYPES.items()]


@router.get("/defaults", dependencies=[Depends(get_current_active_user)])
def defaults(db: Session = Depends(get_db)):
    """Which document types have a designed default (for everyone or for some customer) -- the label buttons ask
    the server only then (it answers 404 when none applies, and the built-in label prints)."""
    return {t.doc_type: True for t in db.query(DocTemplate).filter(DocTemplate.is_default == True).all()}  # noqa: E712


@router.get("/", dependencies=admin)
def list_templates(db: Session = Depends(get_db)):
    return [_out(t, with_spec=False) for t in db.query(DocTemplate).order_by(DocTemplate.doc_type, DocTemplate.name).all()]


@router.get("/starters/{doc_type}", dependencies=admin)
def starters(doc_type: str):
    if doc_type not in doc_context.DOC_TYPES:
        raise HTTPException(status_code=404, detail="Unknown document type")
    return [{"key": k, "name": s["name"], "spec": s} for k, s in template_starters.starters(doc_type)]


@router.get("/{tid}", dependencies=admin)
def get_template(tid: int, db: Session = Depends(get_db)):
    return _out(_get(db, tid))


class TemplateIn(BaseModel):
    doc_type: str
    name: str
    spec: Optional[Any] = None
    starter: Optional[str] = None  # make it from this ready-made design
    customer_id: Optional[int] = None


@router.post("/", dependencies=admin)
def create(data: TemplateIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    if data.doc_type not in doc_context.DOC_TYPES:
        raise HTTPException(status_code=400, detail="Unknown document type")
    spec = data.spec
    if spec is None:
        spec = next((s for k, s in template_starters.starters(data.doc_type) if k == (data.starter or "blank")), None)
        if spec is None:
            raise HTTPException(status_code=400, detail="Unknown starting design")
    t = DocTemplate(doc_type=data.doc_type, name=(data.name or "").strip() or spec.get("name") or "Untitled", spec=json.dumps(spec),
                    starter=data.starter, customer_id=data.customer_id if data.doc_type in CUSTOMER_DOCS else None,
                    created_by=user.username, updated_by=user.username)
    db.add(t)
    db.commit()
    return _out(t)


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    spec: Optional[Any] = None
    customer_id: Optional[int] = None
    clear_customer: bool = False


@router.put("/{tid}", dependencies=admin)
def update(tid: int, data: TemplateUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    t = _get(db, tid)
    if data.name is not None:
        if not data.name.strip():
            raise HTTPException(status_code=400, detail="Give the template a name")
        t.name = data.name.strip()
    if data.spec is not None:
        t.spec = json.dumps(data.spec)
    if data.clear_customer:
        t.customer_id = None
    elif data.customer_id is not None and t.doc_type in CUSTOMER_DOCS:
        if not db.get(Customer, data.customer_id):
            raise HTTPException(status_code=400, detail="Customer not found")
        t.customer_id = data.customer_id
    t.updated_by = user.username
    db.commit()
    return _out(t)


@router.delete("/{tid}", status_code=204, dependencies=admin)
def delete(tid: int, db: Session = Depends(get_db)):
    db.delete(_get(db, tid))
    db.commit()
    return Response(status_code=204)


class DefaultIn(BaseModel):
    on: bool = True


@router.post("/{tid}/default", dependencies=admin)
def set_default(tid: int, data: DefaultIn, db: Session = Depends(get_db)):
    """Make it the one that prints (for its customer, or for everyone); off = back to the built-in layout."""
    t = _get(db, tid)
    if data.on:
        same_scope = db.query(DocTemplate).filter(DocTemplate.doc_type == t.doc_type, DocTemplate.id != t.id,
                                                  DocTemplate.customer_id == t.customer_id if t.customer_id else DocTemplate.customer_id.is_(None))
        for other in same_scope.all():
            other.is_default = False
    t.is_default = data.on
    db.commit()
    return _out(t, with_spec=False)


@router.get("/records/{doc_type}", dependencies=admin)
def records(doc_type: str, db: Session = Depends(get_db)):
    """Recent records to preview a template with."""
    model = RECORD_MODEL.get(doc_type)
    if not model:
        return []
    rows = db.query(model).order_by(model.id.desc()).limit(25).all()
    out = []
    for r in rows:
        who = ""
        cid = getattr(r, "customer_id", None)
        if doc_type == "packing_list":
            o = db.get(CustomerOrder, r.order_id)
            cid = o.customer_id if o else None
        if cid:
            c = db.get(Customer, cid)
            who = c.name if c else ""
        out.append({"id": r.id, "label": f"{r.code}" + (f" · {who}" if who else ""), "customer_id": cid})
    return out


def _context(db, doc_type, record_id):
    if doc_type in doc_context.LABEL_TYPES:
        return doc_context.label_context(db, doc_type, doc_context.SAMPLE_LABEL[doc_type]), []
    model = RECORD_MODEL[doc_type]
    rec = db.get(model, record_id) if record_id else db.query(model).order_by(model.id.desc()).first()
    if not rec:
        raise HTTPException(status_code=404, detail="No record to preview with yet")
    return doc_context.build(db, doc_type, rec)


@router.get("/sample/{doc_type}", dependencies=admin)
def sample(doc_type: str, record_id: Optional[int] = None, db: Session = Depends(get_db)):
    """The values a template would show for a record (the designer's live canvas)."""
    if doc_type not in doc_context.DOC_TYPES:
        raise HTTPException(status_code=404, detail="Unknown document type")
    ctx, rows = _context(db, doc_type, record_id)
    pallet_rows = ctx.get("_pallet_rows") or []
    ctx = {k: v for k, v in ctx.items() if not k.startswith("_")}
    ctx.setdefault("page", "1")
    ctx.setdefault("pages", "1")
    return {"context": ctx, "rows": [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows[:30]], "pallet_rows": pallet_rows[:12]}


class PreviewIn(BaseModel):
    doc_type: str
    spec: Any
    record_id: Optional[int] = None


@router.post("/preview", dependencies=admin)
def preview(data: PreviewIn, db: Session = Depends(get_db)):
    """The template as a real PDF, with a record's data (labels: sample data)."""
    if data.doc_type not in doc_context.DOC_TYPES:
        raise HTTPException(status_code=404, detail="Unknown document type")
    ctx, rows = _context(db, data.doc_type, data.record_id)
    pdf = (template_engine.render_labels(data.spec, [ctx]) if data.doc_type in doc_context.LABEL_TYPES
           else template_engine.render(data.spec, ctx, rows))
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="preview.pdf"'})


class LabelsIn(BaseModel):
    doc_type: str = "box_label"
    labels: List[dict]
    template_id: Optional[int] = None
    customer_id: Optional[int] = None


@router.post("/render-labels", dependencies=[Depends(get_current_active_user)])
def render_labels(data: LabelsIn, db: Session = Depends(get_db)):
    """Labels as the label screens build them -> one PDF page each, with the chosen / default template."""
    t = db.get(DocTemplate, data.template_id) if data.template_id else default_for(db, data.doc_type, data.customer_id)
    if not t or t.doc_type != data.doc_type:
        raise HTTPException(status_code=404, detail="No label template is set as the default")
    ctxs = [doc_context.label_context(db, data.doc_type, l) for l in data.labels]
    pdf = template_engine.render_labels(json.loads(t.spec), ctxs)
    from app.services.filenames import disposition, doc_name
    ships = {(l.get("shipment") or "", l.get("po") or "") for l in data.labels}
    name = doc_name(*ships.pop(), "Labels") if len(ships) == 1 else "Labels.pdf"  # one shipment: SH...-PO-Labels.pdf
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": disposition(name)})
