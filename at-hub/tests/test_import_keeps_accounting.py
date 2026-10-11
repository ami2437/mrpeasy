"""A fresh MRPeasy import keeps Accounting exactly as it is (importers/mrpeasy/keep.py) -- on the PC (load.py) and on the
cloud (deploy/server/athub-load-data.sh runs the same code with the site's database as "live")."""
import json
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (AcctAccount, AcctOpenItem, AcctRule, AcctSource, AcctSplit, AcctTxn, AppSetting, Base, Invoice,
                        PurchaseOrder)


def _db(path):
    eng = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


def test_fresh_import_keeps_every_accounting_row_and_setting(tmp_path):
    from app.importers.mrpeasy.keep import counts, keep_independent
    live_p, new_p = tmp_path / "live.db", tmp_path / "import.db"
    e1, live = _db(live_p)
    e2, new = _db(new_p)
    # live: AT-HUB records + Accounting work
    live.add_all([Invoice(id=5, code="Inv-9601", customer_id=1), Invoice(id=6, code="Inv-GONE", customer_id=1),
                  PurchaseOrder(id=7, code="PO-77", vendor_id=1)])
    labor = AcctAccount(id=3, name="Labor", kind="expense")
    fau = AcctAccount(id=4, name="Faustino", kind="expense", parent_id=3)
    src = AcctSource(id=2, name="WF Checking", kind="bank")
    t1 = AcctTxn(id=10, source_id=2, date=datetime(2025, 5, 1), amount=-300, description="ZELLE TO FAUSTINO", account_id=4, how="person")
    t2 = AcctTxn(id=11, source_id=2, date=datetime(2025, 5, 2), amount=-100, description="SPLIT ONE", how="person")
    live.add_all([labor, fau, src, t1, t2, AcctSplit(txn_id=11, account_id=3, amount=-60), AcctSplit(txn_id=11, account_id=4, amount=-40),
                  AcctRule(id=1, contains="FAUSTINO", account_id=4, conditions=json.dumps([{"field": "text", "op": "contains", "value": "FAUSTINO"}])),
                  AcctOpenItem(id=1, side="ar", source="invoice", source_id=5, status="paid", note="paid by wire"),
                  AcctOpenItem(id=2, side="ar", source="invoice", source_id=6, status="paid"),
                  AcctOpenItem(id=3, side="ap", source="po", source_id=7, amount=10),
                  AcctOpenItem(id=4, side="ap", source="manual", party="Jill", amount=1800),
                  AppSetting(key="acct_settings", value=json.dumps({"fy_start_month": 4, "years": {"FY2024-25": {"bank_balance": 61000}}})),
                  AppSetting(key="acct_rule_dismissed", value="[]"), AppSetting(key="golive", value="LIVE")])
    live.commit()
    # the fresh import: the same records under new ids, no Accounting, its own non-Accounting settings
    new.add_all([Invoice(id=50, code="Inv-9601", customer_id=1), PurchaseOrder(id=70, code="PO-77", vendor_id=1),
                 AppSetting(key="golive", value="IMPORT")])
    new.commit()
    live.close(); new.close(); e1.dispose(); e2.dispose()

    kept = keep_independent(new_p, live_p)
    assert kept["acct_txns"] == 2 and kept["acct_accounts"] == 2 and kept["acct_splits"] == 2 and kept["acct_rules"] == 1
    c_live, c_new = counts(live_p), counts(new_p)
    assert {t: n for t, n in c_live.items() if t != "acct_open_items"} == {t: n for t, n in c_new.items() if t != "acct_open_items"}
    e3, chk = _db(new_p)
    assert chk.get(AcctAccount, 4).parent_id == 3 and chk.get(AcctTxn, 10).account_id == 4 and chk.get(AcctTxn, 10).how == "person"
    assert json.loads(chk.get(AppSetting, "acct_settings").value)["years"]["FY2024-25"]["bank_balance"] == 61000
    assert chk.get(AppSetting, "golive").value == "IMPORT"          # only Accounting's settings come from live
    items = {o.id: o for o in chk.query(AcctOpenItem).all()}
    assert items[1].source_id == 50 and items[1].note == "paid by wire"   # same invoice, its new id
    assert 2 not in items                                                  # its invoice is gone from the import
    assert items[3].source_id == 70 and items[4].party == "Jill"
    chk.close(); e3.dispose()
    # running it again changes nothing (idempotent)
    keep_independent(new_p, live_p)
    assert counts(new_p) == c_new


def test_load_keeps_accounting_when_live_exists():
    """load() calls the keep step after everything else (so nothing later in the import can undo it)."""
    import inspect
    from app.importers.mrpeasy import load
    src = inspect.getsource(load.load)
    assert src.index("keep_independent") > src.index("normalize_import(db)")
