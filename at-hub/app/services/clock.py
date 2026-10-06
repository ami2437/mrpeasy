"""Time zones, Outlook-style.

- Moments (shipped, delivered, received, every created_at / *_at stamp) are stored in UTC, naive. The API marks them
  with "Z" (app/main.py) and each person's screen shows them in their own time zone (User.timezone, else the company's).
- Calendar dates (delivery date, due date, invoice date, paid date, expected date...) are a day, not a moment: stored
  as naive midnight and shown as that same day to everyone, wherever they are -- like an all-day event.
- Printed documents and emails show moments in the company's time zone (settings.business_timezone, Central).
A date picked for a moment ("delivered on Oct 5") is stored as noon of that day in the picker's zone, so it reads as
the same day in every US time zone."""
from datetime import datetime, time
from typing import Optional

from app.config.settings import settings


def zone(name: Optional[str] = None):
    """A tz object for name, else the company's zone; None when neither is set (server's own clock)."""
    from zoneinfo import ZoneInfo
    name = name or settings.business_timezone
    try:
        return ZoneInfo(name) if name else None
    except Exception:
        return ZoneInfo(settings.business_timezone) if settings.business_timezone else None


def utcnow() -> datetime:
    return datetime.utcnow()


def to_utc(value: Optional[datetime], tz_name: Optional[str] = None) -> Optional[datetime]:
    """A wall-clock time in tz_name (or a zone-aware time) -> naive UTC."""
    if value is None:
        return None
    from datetime import timezone
    if value.tzinfo is None:
        z = zone(tz_name)
        value = value.replace(tzinfo=z) if z else value.astimezone()
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def local(value: Optional[datetime], tz_name: Optional[str] = None) -> Optional[datetime]:
    """A stored UTC moment -> naive wall time in tz_name (default: the company's zone) -- for PDFs and emails."""
    if value is None:
        return None
    from datetime import timezone
    z = zone(tz_name)
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return aware.astimezone(z).replace(tzinfo=None) if z else aware.astimezone().replace(tzinfo=None)


def today(tz_name: Optional[str] = None) -> datetime:
    """Today's date in that zone (default: the company's), as naive midnight -- a calendar date."""
    now = local(datetime.utcnow(), tz_name)
    return datetime.combine(now.date(), time())


def business_now() -> datetime:
    """Kept for callers of the first version: now, as a stored moment (UTC)."""
    return datetime.utcnow()


def to_business(value: Optional[datetime]) -> Optional[datetime]:
    """Kept for callers of the first version: a typed moment -> stored UTC (see moment_from_input)."""
    return moment_from_input(value)


def moment_from_input(value: Optional[datetime], tz_name: Optional[str] = None) -> Optional[datetime]:
    """A moment someone typed: zone-aware -> UTC; a bare date (midnight) -> noon of that day in their zone -> UTC;
    a naive time of day -> their wall clock -> UTC."""
    if value is None:
        return None
    if value.tzinfo is None and value.time() == time():
        value = datetime.combine(value.date(), time(12))
    return to_utc(value, tz_name)


def calendar_from_input(value: Optional[datetime], tz_name: Optional[str] = None) -> Optional[datetime]:
    """A calendar date someone typed -> naive midnight of that day (a zone-aware value: the day in their zone)."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = local(value, tz_name)
    return datetime.combine(value.date(), time())


def date_only_to_noon_utc(value: datetime, tz_name: Optional[str] = None) -> datetime:
    return to_utc(datetime.combine(value.date(), time(12)), tz_name)


def check_zone(name: Optional[str]) -> Optional[str]:
    """A zone name someone picked: '' / None = the company's (None); an unknown name is refused."""
    from fastapi import HTTPException
    from zoneinfo import ZoneInfo
    name = (name or "").strip()
    if not name:
        return None
    try:
        ZoneInfo(name)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Unknown time zone '{name}'")
    return name
