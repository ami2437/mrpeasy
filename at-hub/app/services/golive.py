"""Go-live cleanup (MRP Migrate): keep Needs Attention about work done in AT-HUB, not history carried over from MRPeasy.

- a cut-off date: rows dated before it drop out of the chosen sections (old shipments without a POD, MRPeasy
  Dummy invoices, unpaid vendor bills whose payments were never exported...);
- dismissals: single rows hidden until someone restores them.
Once MRPeasy is long gone, clearing the cut-off brings everything back; nothing is deleted."""
import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import AppSetting, AttentionDismissal

KEY = "golive_cutoff"
# The sections that are mostly MRPeasy history before go-live (the cut-off's default). Orders still open are real work.
HISTORY_SECTIONS = ["not_delivered", "missing_pod", "no_invoice", "draft_invoices", "vendor_shipped", "po_overdue",
                    "bills_due", "unapplied_payments", "late_orders", "mtr_unlinked"]


def get_cutoff(db: Session) -> dict:
    row = db.get(AppSetting, KEY)
    data = json.loads(row.value) if row and row.value else {}
    return {"date": data.get("date"), "sections": data.get("sections", HISTORY_SECTIONS),
            "updated_by": row.updated_by if row else None, "updated_at": row.updated_at if row else None}


def set_cutoff(db: Session, date, sections, by: str) -> dict:
    row = db.get(AppSetting, KEY) or AppSetting(key=KEY)
    row.value = json.dumps({"date": date.strftime("%Y-%m-%d") if date else None, "sections": list(sections or [])})
    row.updated_by = by
    db.merge(row)
    db.commit()
    return get_cutoff(db)


def _rid(row) -> int:
    return row.get("_rid", row.get("id"))


def _dismissed(db: Session) -> set:
    return {(d.section_key, d.record_id) for d in db.query(AttentionDismissal).all()}


def _before(row, cutoff_date) -> bool:
    d = row.get("_date")
    return bool(cutoff_date and d and d < cutoff_date)


def classify(db: Session, sections: list) -> list:
    """Every row marked hidden_by: None | "cutoff" | "dismissed" (the cleanup page's preview)."""
    cut = get_cutoff(db)
    cutoff_date = datetime.strptime(cut["date"], "%Y-%m-%d") if cut["date"] else None
    gone = _dismissed(db)
    for sec in sections:
        for row in sec["rows"]:
            row["hidden_by"] = ("dismissed" if (sec["key"], _rid(row)) in gone
                                else "cutoff" if sec["key"] in cut["sections"] and _before(row, cutoff_date) else None)
    return sections


def strip(sections: list) -> list:
    for sec in sections:
        for row in sec["rows"]:
            for k in ("_date", "_rid", "_label"):
                row.pop(k, None)
    return sections


def apply(db: Session, sections: list) -> list:
    """What Needs Attention shows: hidden rows taken out, with how many were hidden."""
    classify(db, sections)
    for sec in sections:
        shown = [r for r in sec["rows"] if not r["hidden_by"]]
        sec["hidden"] = len(sec["rows"]) - len(shown)
        sec["rows"] = shown
        for r in shown:
            r.pop("hidden_by", None)
    return strip(sections)


def dismiss(db: Session, items, by: str, sections_by_key: dict) -> int:
    have = _dismissed(db)
    n = 0
    for it in items:
        key, rid = it["key"], int(it["id"])
        if (key, rid) in have:
            continue
        row = next((r for r in (sections_by_key.get(key) or []) if _rid(r) == rid), None)
        db.add(AttentionDismissal(section_key=key, record_id=rid, label=(row or {}).get("_label"), dismissed_by=by))
        n += 1
    db.commit()
    return n


def restore(db: Session, ids) -> int:
    n = db.query(AttentionDismissal).filter(AttentionDismissal.id.in_(list(ids))).delete(synchronize_session=False)
    db.commit()
    return n
