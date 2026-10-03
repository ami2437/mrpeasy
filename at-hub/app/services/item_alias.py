"""Learned matches: what a vendor/customer printed for an item -> the item the user chose.

learn() is called whenever an order or PO is saved; for_party() feeds ItemMatcher so the next scan
of the same code or description is matched straight away."""
import re
from datetime import datetime
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.models import ItemAlias


def norm(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def learn(db: Session, party_type: str, party_id: Optional[int], item_id: Optional[int],
          code: Optional[str] = None, desc: Optional[str] = None) -> None:
    if not party_id or not item_id:
        return
    for kind, text in (("code", code), ("desc", desc)):
        key = norm(text)
        if len(key) < (3 if kind == "code" else 8):  # "1", "EA", "bolt" identify nothing
            continue
        row = db.query(ItemAlias).filter_by(party_type=party_type, party_id=party_id, kind=kind, key=key).first()
        if not row:
            db.add(ItemAlias(party_type=party_type, party_id=party_id, kind=kind, key=key,
                             text=text.strip()[:300], item_id=item_id))
        elif row.item_id == item_id:
            row.hits = (row.hits or 0) + 1
            row.last_used_at = datetime.utcnow()
        else:  # the user picked a different item for this wording: the latest pick wins
            row.item_id, row.hits, row.last_used_at, row.text = item_id, 1, datetime.utcnow(), text.strip()[:300]
    db.flush()


def for_party(db: Session, party_type: str, party_id: Optional[int]) -> Dict[str, Dict[str, tuple]]:
    """{"code": {key: (item_id, hits)}, "desc": {...}} for ItemMatcher."""
    out = {"code": {}, "desc": {}}
    if party_id:
        for a in db.query(ItemAlias).filter_by(party_type=party_type, party_id=party_id).all():
            out[a.kind][a.key] = (a.item_id, a.hits or 1)
    return out
