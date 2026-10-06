"""MRP Migrate > Go-Live Cleanup: the Needs Attention cut-off date and dismissed rows (app/services/golive.py)."""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_perm
from app.models import AttentionDismissal, User
from app.services import golive

router = APIRouter(prefix="/api/golive", tags=["golive"], dependencies=[Depends(require_perm("golive"))])


@router.get("/")
def overview(db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """Every Needs Attention section with each row marked hidden_by (None / cutoff / dismissed), the cut-off, and the
    dismissed list."""
    from app.routes.reports import all_sections
    sections = golive.classify(db, all_sections(db, user))
    for sec in sections:
        for r in sec["rows"]:
            r["record_id"] = golive._rid(r)
            r["dated"] = r["_date"].strftime("%Y-%m-%d") if r.get("_date") else None
    dismissed = [{"id": d.id, "key": d.section_key, "record_id": d.record_id, "label": d.label, "by": d.dismissed_by,
                  "at": d.dismissed_at.isoformat() + "Z" if d.dismissed_at else None}
                 for d in db.query(AttentionDismissal).order_by(AttentionDismissal.dismissed_at.desc()).all()]
    return {"cutoff": golive.get_cutoff(db), "default_sections": golive.HISTORY_SECTIONS,
            "sections": golive.strip(sections), "dismissed": dismissed}


class CutoffIn(BaseModel):
    date: Optional[datetime] = None  # None = no cut-off: everything shows again
    sections: List[str] = []


@router.put("/cutoff")
def set_cutoff(data: CutoffIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    return golive.set_cutoff(db, data.date, data.sections, user.username)


class DismissIn(BaseModel):
    items: List[dict]  # [{key, id}]


@router.post("/dismiss")
def dismiss(data: DismissIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    from app.routes.reports import all_sections
    if not data.items:
        raise HTTPException(status_code=400, detail="Tick the rows to dismiss")
    by_key = {s["key"]: s["rows"] for s in all_sections(db, user)}
    unknown = [it for it in data.items if it.get("key") not in by_key
               or not any(golive._rid(r) == int(it.get("id") or 0) for r in by_key[it["key"]])]
    if unknown:
        raise HTTPException(status_code=400, detail=f"{len(unknown)} of the ticked rows aren't on Needs Attention any more -- reload the page")
    return {"dismissed": golive.dismiss(db, data.items, user.username, by_key)}


class RestoreIn(BaseModel):
    ids: List[int]


@router.post("/restore")
def restore(data: RestoreIn, db: Session = Depends(get_db)):
    return {"restored": golive.restore(db, data.ids)}
