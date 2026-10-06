"""Outlook-style time zones (app/services/clock.py): moments in UTC shown in each user's zone, calendar dates as the day."""
from datetime import datetime

from app.services import clock


def test_a_picked_delivery_date_is_that_day_for_everyone(make, api):
    a = make.item()
    make.stock(a, 10)
    sh = make.ship(make.order(lines=[(a, 5, 1)]))
    day = datetime.utcnow().strftime("%Y-%m-%d")
    sh = api.post(f"/api/shipments/{sh['id']}/delivered", json={"delivered_at": day})
    stored = datetime.fromisoformat(sh["delivered_at"][:-1])
    for zone in ("America/Los_Angeles", "America/Chicago", "America/New_York"):  # noon in the picker's zone
        assert clock.local(stored, zone).strftime("%Y-%m-%d") == day


def test_calendar_dates_stay_bare(make, api):
    a = make.item()
    o = make.order(lines=[(a, 5, 1)], delivery_date="2026-11-20")
    assert o["delivery_date"].startswith("2026-11-20T00:00") and not o["delivery_date"].endswith("Z")
    assert o["created_at"].endswith("Z")


def test_each_user_picks_a_time_zone(api):
    me = api.put("/api/auth/timezone", json={"timezone": "Asia/Kolkata"})
    assert me["timezone"] == "Asia/Kolkata" and me["effective_timezone"] == "Asia/Kolkata"
    api.put("/api/auth/timezone", json={"timezone": "Mars/Base"}, expect=400)
    me = api.put("/api/auth/timezone", json={"timezone": ""})
    assert me["timezone"] is None and me["effective_timezone"] == "America/Chicago"


def test_invoice_date_is_the_companys_today(make, api):
    a = make.item(price=2)
    make.stock(a, 10)
    inv = make.invoice(make.ship(make.order(lines=[(a, 5, 2)])))
    assert inv["invoice_date"][:10] == clock.today().strftime("%Y-%m-%d") and not inv["invoice_date"].endswith("Z")


def test_old_dates_convert_once():
    from app.services.tz_migrate import _moment, _calendar, MRP_TZ
    assert _moment(datetime(2026, 9, 24), MRP_TZ) == datetime(2026, 9, 24, 17, 0)          # a bare date -> noon Central
    assert _moment(datetime(2026, 7, 22, 16, 9), MRP_TZ) == datetime(2026, 7, 22, 20, 9)   # Eastern wall time -> UTC
    assert _moment(datetime(2026, 10, 6, 2, 26), None) == datetime(2026, 10, 6, 2, 26)     # AT-HUB's own: already UTC
    assert _calendar(datetime(2026, 10, 6, 2, 35), None) == datetime(2026, 10, 5)          # 9:35 pm Central on the 5th
