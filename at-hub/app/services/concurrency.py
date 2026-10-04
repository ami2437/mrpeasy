"""Several people on the same records -- the ERP way:

1. Changes that move stock or money never interleave. Every change request (POST/PUT/DELETE) opens its
   database transaction with BEGIN IMMEDIATE, so a second booking waits a moment and then sees the real
   free stock; document numbers (SH-, C-, PO, Inv-) can't be handed out twice. WAL mode lets reads carry
   on meanwhile, and busy_timeout makes a writer wait instead of failing.
2. Optimistic locking for edits (as NetSuite / SAP / Odoo do): orders, POs, shipments, invoices, quotes,
   customers and vendors carry row_version, bumped whenever the record or one of its lines changes. A
   screen sends the version it loaded (X-Row-Version); if someone saved in between, the change is refused
   with 409 CONFLICT naming who changed it and when -- nothing is overwritten silently.
3. Presence: record screens report who has them open, so "Maria is also viewing" can be shown, and a
   screen learns when someone else saved the record it shows.
"""
import contextvars
import re
import threading
import time
from datetime import datetime

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

WRITE_REQUEST = contextvars.ContextVar("write_request", default=None)  # {"committed": bool} during a change request
READ_ONLY = contextvars.ContextVar("read_only", default=False)  # a lookup that must never take the write lock (the signed-in user)
CURRENT_USER = contextvars.ContextVar("current_user", default=None)


# ---------- 1. SQLite: WAL, wait instead of failing, write transactions take the lock up front ----------
def setup_sqlite(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _connect(dbapi_conn, _rec):
        dbapi_conn.isolation_level = None  # SQLAlchemy's "begin" hook below decides how a transaction starts
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=15000")
        cur.close()

    @event.listens_for(engine, "begin")
    def _begin(conn):
        # A change request's transaction takes the write lock up front -- until its first commit; reads after
        # that (building the response) don't hold it again.
        w = WRITE_REQUEST.get()
        conn.exec_driver_sql("BEGIN IMMEDIATE" if w is not None and not w["committed"] and not READ_ONLY.get() else "BEGIN")

    @event.listens_for(engine, "commit")
    def _commit(conn):
        w = WRITE_REQUEST.get()
        if w is not None:
            w["committed"] = True


# ---------- 2. versions: a change to a record or any of its lines bumps the record ----------
# url collection -> model name (for the X-Row-Version check and presence)
VERSIONED = {"customer-orders": "CustomerOrder", "purchase-orders": "PurchaseOrder", "shipments": "Shipment",
             "invoices": "Invoice", "quotes": "Quote", "customers": "Customer", "vendors": "Vendor"}
# attributes the system changes as a side effect (shipping fills shipped quantities, order status follows)
# -- they don't count as someone editing the record
_SIDE_EFFECTS = {"CustomerOrderLine": {"shipped_quantity"}, "CustomerOrder": {"status", "row_version", "row_updated_at", "updated_by", "updated_at"}}


def _root(obj):
    """The document a changed row belongs to (a line -> its order), or None if it isn't versioned."""
    name = type(obj).__name__
    if name in ("CustomerOrder", "PurchaseOrder", "Shipment", "Invoice", "Quote", "Customer", "Vendor"):
        return obj
    parent = {"CustomerOrderLine": "order", "ShipmentLine": "shipment", "ShipmentBox": "shipment", "PalletWeight": "shipment",
              "PurchaseOrderLine": "purchase_order", "PurchaseOrderCharge": "purchase_order", "PurchaseOrderPayment": "purchase_order",
              "VendorBill": "purchase_order", "InvoiceLine": "invoice", "InvoicePayment": "invoice", "QuoteLine": "quote"}.get(name)
    if not parent:
        return None
    rel = getattr(obj, parent, None)
    if rel is not None:
        return rel
    fk = {"order": ("CustomerOrder", "order_id"), "shipment": ("Shipment", "shipment_id"), "purchase_order": ("PurchaseOrder", "po_id"),
          "invoice": ("Invoice", "invoice_id"), "quote": ("Quote", "quote_id")}[parent]
    from app import models
    rid = getattr(obj, fk[1], None)
    sess = Session.object_session(obj)
    return sess.get(getattr(models, fk[0]), rid) if sess is not None and rid else None


def _really_changed(obj) -> bool:
    ignore = _SIDE_EFFECTS.get(type(obj).__name__, set())
    st = inspect(obj)
    return any(a.key not in ignore and a.history.has_changes() for a in st.attrs)


@event.listens_for(Session, "before_flush")
def _bump_versions(session, _ctx, _instances):
    roots = {}
    for obj in list(session.new) + list(session.deleted):
        r = _root(obj)
        if r is not None and r not in session.new:
            roots[id(r)] = r
    for obj in session.dirty:
        if session.is_modified(obj) and _really_changed(obj):
            r = _root(obj)
            if r is not None and r not in session.new:
                roots[id(r)] = r
    for r in roots.values():
        if r in session.deleted:
            continue
        r.row_version = (r.row_version or 1) + 1
        r.row_updated_at = datetime.utcnow()
        r.updated_by = CURRENT_USER.get()


# ---------- 3. presence: who has a record open (kept in memory: AT-HUB runs one server process) ----------
_PRESENCE = {}  # key -> {user: last_seen}
_LOCK = threading.Lock()
PRESENCE_TTL = 45  # seconds without a heartbeat = gone


def heartbeat(key: str, user: str) -> list:
    """Note that `user` has `key` open; returns the other people on it."""
    now = time.time()
    with _LOCK:
        seen = _PRESENCE.setdefault(key, {})
        seen[user] = now
        for u in [u for u, t in seen.items() if now - t > PRESENCE_TTL]:
            del seen[u]
        return sorted(u for u in seen if u != user)


def leave(key: str, user: str) -> None:
    with _LOCK:
        _PRESENCE.get(key, {}).pop(user, None)


RECORD_PATH = re.compile(r"^/api/(" + "|".join(VERSIONED) + r")/(\d+)(?:/|$)")


def current_version(db, collection: str, rid: int):
    from app import models
    rec = db.get(getattr(models, VERSIONED[collection]), rid)
    return rec
