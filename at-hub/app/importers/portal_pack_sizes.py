"""Default pack sizes from the old portal (backend-fastapi's pack_sizes table) into AT-HUB.

Fills items that have no pack size yet; a size already set in AT-HUB is kept (it's newer) and only reported.
Codes match by the standard nut spelling (15420-NUTS == 15420-NUT). Every change is logged in pack size history
with source "portal". Read-only on the portal's database.

    python -m app.importers.portal_pack_sizes           # dry run: what would change
    python -m app.importers.portal_pack_sizes --apply   # write it
"""
import sqlite3
import sys
from pathlib import Path

from app.config.database import SessionLocal
from app.models import StockItem
from app.services.item_naming import normalize_code

PORTAL_DB = Path(__file__).resolve().parents[3] / "backend-fastapi" / "mrpeasy.db"


def portal_pack_sizes(path: Path = PORTAL_DB) -> dict:
    """{item code: pack size} as the portal has them."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {code.strip(): int(size) for code, size in con.execute("select item_code, pack_size from pack_sizes") if code and size}
    finally:
        con.close()


def apply(db, sizes: dict, by: str = "portal-import", write: bool = False) -> dict:
    from app.services.crud import StockItemService
    items = {i.code: i for i in db.query(StockItem).all()}
    out = {"filled": [], "same": [], "kept_at_hub": [], "not_found": []}
    for code, size in sorted(sizes.items()):
        it = items.get(code) or items.get(normalize_code(code))
        if not it:
            out["not_found"].append(code)
        elif it.default_pack_size == size:
            out["same"].append(it.code)
        elif it.default_pack_size:
            out["kept_at_hub"].append(f"{it.code}: AT-HUB {it.default_pack_size}, portal {size}")
        else:
            out["filled"].append(f"{it.code}: {size}")
            if write:
                StockItemService.set_pack_size(db, it, size, by, "portal", "backend-fastapi pack_sizes")
    if write:
        db.commit()
    return out


if __name__ == "__main__":
    write = "--apply" in sys.argv
    db = SessionLocal()
    try:
        r = apply(db, portal_pack_sizes(), write=write)
    finally:
        db.close()
    print(("APPLIED" if write else "DRY RUN") + f" -- portal pack sizes: {sum(len(v) for v in r.values())}")
    for k, v in r.items():
        print(f"  {k}: {len(v)}" + "".join(f"\n    {x}" for x in v if k != "same"))
