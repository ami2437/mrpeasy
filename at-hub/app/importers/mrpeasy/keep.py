"""Modules that have nothing to do with MRPeasy survive a fresh import untouched (user, 2026-10-11: "accounting module is
independent of MRP ... make sure every time we do a fresh import, accounting doesn't get wiped out").

keep_independent() copies, from the live at_hub.db into the freshly built import, every row of:
  - every acct_* table (Accounting: accounts, bank accounts, statement lines, splits, rules, imports, AP / AR notes,
    comments, ignored lines, projections, reconciles ... -- any acct_* table added later is kept automatically)
  - app_settings rows whose key starts with "acct_" (partner split, year settings, dismissed rule suggestions ...)
  - every vacct_* table (Vendor Accounts: wire + cash vendors -- bills, payments, what each paid; keyed by codes)
  - vendor payments typed in AT-HUB (vendor_payments) and what they were applied to, re-pointed by vendor / PO code
    (user, 2026-10-16: a payment recorded on the site was lost by an import -- never again)
Same ids, so nothing inside Accounting changes. The only Accounting rows that point at MRPeasy records are AP / AR notes
on an invoice / PO / customer order (acct_open_items.source_id): they're re-pointed by the record's code, and the ones
whose record is gone are dropped and reported.

The cloud does the same with the SITE's Accounting when this PC's data is pushed (deploy/server/athub-load-data.sh,
KEEP_SQL below is the shared re-pointing step)."""
import sqlite3
from pathlib import Path
from typing import Dict, List

KEEP_PREFIX = "acct_"
SETTING_PREFIX = "acct_"
# acct_open_items.source -> the table its source_id points at
OPEN_ITEM_SOURCES = {"invoice": "invoices", "po": "purchase_orders", "order": "customer_orders"}


def kept_tables(con: sqlite3.Connection, schema: str = "main") -> List[str]:
    return [r[0] for r in con.execute(f"select name from {schema}.sqlite_master where type = 'table' and (name like 'acct\\_%' escape '\\' "
                                      f"or name like 'vacct\\_%' escape '\\') order by name")]


def _cols(con, schema, table) -> List[str]:
    return [r[1] for r in con.execute(f"pragma {schema}.table_info({table})")]


def keep_independent(target: Path, live: Path, rep=None) -> Dict[str, int]:
    """Copy the live database's Accounting into `target` (the new import). Returns {table: rows kept}."""
    out: Dict[str, int] = {}
    if not Path(live).exists():
        return out
    con = sqlite3.connect(target)
    try:
        con.execute("attach database ? as live", (str(live),))
        live_tables = set(kept_tables(con, "live"))
        new_tables = set(kept_tables(con, "main"))
        con.execute("begin")
        for t in sorted(live_tables):
            if t not in new_tables:
                if rep:
                    rep.add("kept", f"{t}: in the live database but not in this version of AT-HUB -- not kept")
                continue
            cols = [c for c in _cols(con, "live", t) if c in set(_cols(con, "main", t))]
            cl = ", ".join(f'"{c}"' for c in cols)
            con.execute(f'delete from main."{t}"')
            con.execute(f'insert into main."{t}" ({cl}) select {cl} from live."{t}"')
            out[t] = con.execute(f'select count(*) from main."{t}"').fetchone()[0]
        # Accounting's settings
        con.execute("delete from main.app_settings where key like ?", (SETTING_PREFIX + "%",))
        scols = [c for c in _cols(con, "live", "app_settings") if c in set(_cols(con, "main", "app_settings"))]
        sl = ", ".join(scols)
        con.execute(f"insert into main.app_settings ({sl}) select {sl} from live.app_settings where key like ?", (SETTING_PREFIX + "%",))
        out["app_settings (acct_*)"] = con.execute("select count(*) from main.app_settings where key like ?", (SETTING_PREFIX + "%",)).fetchone()[0]
        dropped = repoint_open_items(con)
        vp, vp_dropped = keep_vendor_payments(con)
        out.update(vp)
        dropped += vp_dropped
        con.execute("commit")
    except Exception:
        con.execute("rollback") if con.in_transaction else None
        raise
    finally:
        con.close()
    _resync(target)
    if rep:
        rep.add("kept", "Accounting, Vendor Accounts and vendor payments kept from the live database: " + ", ".join(f"{t} {n}" for t, n in out.items()))
        for d in dropped:
            what = "Vendor payment" if d[0] in ("vendor", "po") else "AP / AR note"
            rep.add("kept", f"{what} dropped -- its {d[0]} {d[1]} isn't in the new import")
    return out


def _resync(target: Path) -> None:
    """Vendor Accounts own what was paid on their vendors' POs: put those PO payments back over the import's."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.services import vendor_accounts
    eng = create_engine(f"sqlite:///{Path(target).as_posix()}")
    try:
        db = sessionmaker(bind=eng)()
        try:
            vendor_accounts.sync_all(db)
            db.commit()
        finally:
            db.close()
    finally:
        eng.dispose()


def _dicts(con, sql, args=()):
    cur = con.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def keep_vendor_payments(con: sqlite3.Connection):
    """Vendor payments typed in AT-HUB (not from MRPeasy) and the parts applied to POs. `live` must be attached.
    Vendor and PO are found again by code; a payment whose vendor is gone is dropped (reported), a part whose PO is
    gone goes back to unapplied."""
    out, dropped = {}, []
    if not con.execute("select 1 from live.sqlite_master where name = 'vendor_payments'").fetchone():
        return out, dropped
    main_vcols = set(_cols(con, "main", "vendor_payments"))
    vcols = [c for c in _cols(con, "live", "vendor_payments") if c in main_vcols]
    con.execute("delete from main.purchase_order_payments where vendor_payment_id is not null")
    con.execute("delete from main.vendor_payments")
    for r in _dicts(con, "select p.*, v.code as _vcode from live.vendor_payments p left join live.vendors v on v.id = p.vendor_id"):
        new_v = con.execute("select id from main.vendors where code = ?", (r["_vcode"],)).fetchone() if r["_vcode"] else None
        if not new_v:
            dropped.append(("vendor", f"{r['_vcode'] or '#' + str(r['vendor_id'])} (payment {r['code']})"))
            continue
        r["vendor_id"] = new_v[0]
        con.execute(f"insert into main.vendor_payments ({', '.join(vcols)}) values ({', '.join('?' for _ in vcols)})", [r[c] for c in vcols])
    main_pcols = set(_cols(con, "main", "purchase_order_payments"))
    pcols = [c for c in _cols(con, "live", "purchase_order_payments") if c in main_pcols and c != "id"]
    for r in _dicts(con, "select p.*, po.code as _po, b.bill_number as _bill from live.purchase_order_payments p "
                         "join live.purchase_orders po on po.id = p.po_id left join live.vendor_bills b on b.id = p.vendor_bill_id "
                         "where p.vendor_payment_id is not null"):
        if not con.execute("select 1 from main.vendor_payments where id = ?", (r["vendor_payment_id"],)).fetchone():
            continue
        po = con.execute("select id from main.purchase_orders where code = ?", (r["_po"],)).fetchone()
        if not po:
            dropped.append(("po", f"{r['_po']} (part of a vendor payment -- back to unapplied)"))
            continue
        r["po_id"] = po[0]
        bill = con.execute("select id from main.vendor_bills where po_id = ? and bill_number = ?", (po[0], r["_bill"])).fetchone() if r["_bill"] else None
        r["vendor_bill_id"] = bill[0] if bill else None
        con.execute(f"insert into main.purchase_order_payments ({', '.join(pcols)}) values ({', '.join('?' for _ in pcols)})", [r[c] for c in pcols])
    out["vendor_payments"] = con.execute("select count(*) from main.vendor_payments").fetchone()[0]
    out["vendor payment parts on POs"] = con.execute("select count(*) from main.purchase_order_payments where vendor_payment_id is not null").fetchone()[0]
    return out, dropped


def repoint_open_items(con: sqlite3.Connection) -> List[tuple]:
    """AP / AR notes on AT-HUB records: same record (by code) in the new data, else dropped. `live` must be attached."""
    dropped = []
    if "acct_open_items" not in kept_tables(con, "main"):
        return dropped
    for source, table in OPEN_ITEM_SOURCES.items():
        rows = con.execute("select o.id, o.source_id, l.code from main.acct_open_items o left join live." + table +
                           " l on l.id = o.source_id where o.source = ?", (source,)).fetchall()
        for oid, sid, code in rows:
            new = con.execute(f"select id from main.{table} where code = ?", (code,)).fetchone() if code else None
            if new:
                con.execute("update main.acct_open_items set source_id = ? where id = ?", (new[0], oid))
            else:
                con.execute("delete from main.acct_open_items where id = ?", (oid,))
                dropped.append((source, code or f"#{sid}"))
    return dropped


def counts(path: Path) -> Dict[str, int]:
    """Rows per kept table -- Accounting, Vendor Accounts, vendor payments (to check nothing was lost)."""
    con = sqlite3.connect(path)
    try:
        out = {t: con.execute(f'select count(*) from "{t}"').fetchone()[0] for t in kept_tables(con)}
        if con.execute("select 1 from sqlite_master where name = 'vendor_payments'").fetchone():
            out["vendor_payments"] = con.execute("select count(*) from vendor_payments").fetchone()[0]
        return out
    finally:
        con.close()
