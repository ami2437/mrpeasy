"""The MRPeasy import, end to end, against the latest snapshot -- run before every wipe / re-import:

    venv\\Scripts\\python -m pytest -m import_

Imports into a scratch database (never at_hub.db), then checks the reconcile report passes and the
numbers we can't afford to get wrong: invoice totals, stock, groups, and that the app starts on it.
"""
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.import_

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def imported(tmp_path_factory):
    from app.importers.mrpeasy.extract import latest_snapshot
    try:
        snap = latest_snapshot()
    except Exception:
        snap = None
    if not snap or not Path(snap).exists():
        pytest.skip("no MRPeasy snapshot in import-data/ -- run: python -m app.importers.mrpeasy extract")
    from app.importers.mrpeasy.load import load
    target = tmp_path_factory.mktemp("import") / "at_hub_import.db"
    load(Path(snap), target)
    return Path(snap), target


@pytest.mark.import_
def test_reconcile_passes(imported):
    from app.importers.mrpeasy.reconcile import reconcile
    snap, target = imported
    assert reconcile(snap, target), "reconcile report has failures -- see the newest reconcile-*.md in the snapshot folder"


@pytest.mark.import_
def test_every_invoice_total_matches_mrpeasy(imported):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Invoice
    snap, target = imported
    raw = {i["code"]: float(i["total_price"] or 0) for i in json.loads((snap / "invoices.json").read_text(encoding="utf-8"))}
    db = sessionmaker(bind=create_engine(f"sqlite:///{target.as_posix()}"))()
    off = [(i.code, i.total, raw[i.code]) for i in db.query(Invoice).all() if i.code in raw and abs(i.total - raw[i.code]) > 0.005]
    assert not off, f"invoice totals differ from MRPeasy: {off[:10]}"


@pytest.mark.import_
def test_stock_groups_and_startup_on_the_imported_db(imported):
    from sqlalchemy import create_engine, func
    from sqlalchemy.orm import sessionmaker
    from app.models import Lot, ProductGroup, StockItem
    from app.services.crud import ProductGroupService, ShipmentService, _group_key
    snap, target = imported
    db = sessionmaker(bind=create_engine(f"sqlite:///{target.as_posix()}"))()
    # the app's own start-up housekeeping must run cleanly on an imported database
    ProductGroupService.ensure_defaults(db)
    ShipmentService.reconcile_bookings(db)
    groups = [g.name for g in db.query(ProductGroup).all()]
    assert len({_group_key(g) for g in groups}) == len(groups), f"duplicate groups: {groups}"
    assert not db.query(StockItem).filter(~StockItem.category.in_(groups)).count(), "items in a group that isn't on the list"
    lots = dict(db.query(Lot.item_id, func.sum(Lot.quantity)).group_by(Lot.item_id).all())
    off = [(i.code, i.on_hand, lots.get(i.id, 0)) for i in db.query(StockItem).all() if abs((i.on_hand or 0) - (lots.get(i.id) or 0)) > 1e-6]
    assert not off, f"on-hand doesn't equal the lots it's made of: {off[:10]}"
