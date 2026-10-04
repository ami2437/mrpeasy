"""Which designed template prints a document, and printing with it."""
import json
from typing import Optional

from sqlalchemy.orm import Session

from app.models import DocTemplate


def default_for(db: Session, doc_type: str, customer_id: Optional[int] = None) -> Optional[DocTemplate]:
    """The customer's own default first, then the general default; None = the built-in layout."""
    q = db.query(DocTemplate).filter(DocTemplate.doc_type == doc_type, DocTemplate.is_default == True)  # noqa: E712
    if customer_id:
        own = q.filter(DocTemplate.customer_id == customer_id).first()
        if own:
            return own
    return q.filter(DocTemplate.customer_id.is_(None)).first()


def render_record(db: Session, tpl: DocTemplate, doc_type: str, record, options: Optional[dict] = None) -> bytes:
    from app.services import doc_context, template_engine
    ctx, rows = doc_context.build(db, doc_type, record, options or {})
    spec = json.loads(tpl.spec)
    opt = options or {}
    if doc_type == "packing_list" and spec.get("table"):  # print-time choices still apply
        hide = {k for k, on in (("boxes", opt.get("include_boxes", True)), ("lot", opt.get("include_lots", True)),
                                ("pallet", opt.get("include_pallets", True))) if not on}
        spec["table"]["columns"] = [c for c in spec["table"].get("columns", []) if c.get("key") not in hide]
    return template_engine.render(spec, ctx, rows, title=f"{ctx['doc']['title'].title()} {ctx['doc']['number']}")
