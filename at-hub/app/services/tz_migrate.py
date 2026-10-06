"""One-time move of dates to the Outlook-style rules in app/services/clock.py (2026-10-06).

Before: the MRPeasy importer wrote MRPeasy's wall-clock time (Eastern), AT-HUB wrote UTC, and dates picked on screen
were local midnight. After: moments are UTC (a bare date = noon that day, company zone), calendar dates are midnight.
normalize_import() runs inside the importer (everything there came from MRPeasy); run_once() converts a database made
before this change, once (AppSetting "tz_v2")."""
from datetime import time

from sqlalchemy.orm import Session

from app.models import (AppSetting, CustomerOrder, Invoice, InvoicePayment, Lot, PurchaseOrder, PurchaseOrderPayment, Shipment,
                        VendorBill, VendorPayment)
from app.services import clock

DONE = "tz_v2"
MRP_TZ = "America/New_York"  # MRPeasy's dates land at Eastern midnight
IMPORT_BY = "mrpeasy-import"


def _midnight(v) -> bool:
    return v is not None and v.time() == time()


def _moment(v, wall_tz):
    """A stored value -> UTC: a bare date -> noon (company zone); a wall time in wall_tz -> UTC (None = already UTC)."""
    if v is None:
        return None
    if _midnight(v):
        return clock.date_only_to_noon_utc(v)
    return clock.to_utc(v, wall_tz) if wall_tz else v


def _calendar(v, wall_tz):
    """A stored value -> that day at midnight (a UTC time is first moved to the company's day)."""
    if v is None or _midnight(v):
        return v
    return clock.calendar_from_input(clock.local(v) if wall_tz is None else v)


def normalize_import(db: Session) -> None:
    """Inside the importer: every date here is MRPeasy's (Eastern wall clock)."""
    for s in db.query(Shipment).all():
        s.ship_date, s.delivered_at = _moment(s.ship_date, MRP_TZ), _moment(s.delivered_at, MRP_TZ)
        s.created_at = clock.to_utc(s.created_at, MRP_TZ)
    for l in db.query(Lot).all():
        l.received_date = _moment(l.received_date, MRP_TZ)
    for model in (CustomerOrder, PurchaseOrder, Invoice):
        for r in db.query(model).all():
            r.created_at = clock.to_utc(r.created_at, MRP_TZ)
    for i in db.query(Invoice).all():
        i.invoice_date = _calendar(i.invoice_date, MRP_TZ)
    for p in db.query(PurchaseOrder).all():
        p.order_date = _calendar(p.order_date, MRP_TZ)
    db.merge(AppSetting(key=DONE, value='"import"'))
    db.flush()


def run_once(db: Session) -> bool:
    """A database from before the change: imported rows were Eastern wall time, AT-HUB's own UTC."""
    if db.get(AppSetting, DONE):
        return False
    for s in db.query(Shipment).all():
        # MRPeasy ship dates are bare dates; a ship date with a time was stamped by AT-HUB (UTC), even on an imported shipment
        s.ship_date = _moment(s.ship_date, None)
        s.delivered_at = _moment(s.delivered_at, MRP_TZ if s.delivered_by == IMPORT_BY else None)
    for l in db.query(Lot).all():
        # a lot made in AT-HUB is received the moment it's created (both UTC); an imported one kept MRPeasy's time
        made_here = l.created_at and l.received_date and abs((l.received_date - l.created_at).total_seconds()) < 60
        l.received_date = _moment(l.received_date, None if made_here else MRP_TZ)
    for i in db.query(Invoice).all():
        i.invoice_date = _calendar(i.invoice_date, MRP_TZ if i.created_by == IMPORT_BY else None)
    for p in db.query(PurchaseOrder).all():
        p.order_date = _calendar(p.order_date, MRP_TZ if p.created_by == IMPORT_BY else None)
    for model in (InvoicePayment, PurchaseOrderPayment, VendorPayment):
        for r in db.query(model).all():
            r.paid_date = _calendar(r.paid_date, None)
    for b in db.query(VendorBill).all():
        b.bill_date = _calendar(b.bill_date, None)
    db.add(AppSetting(key=DONE, value='"converted"'))
    db.commit()
    return True
