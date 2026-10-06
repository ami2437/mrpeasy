"""The business's own clock. Audit stamps (created_at, packed_at...) are UTC, but the dates people read as dates --
ship date, invoice date, received date, paid date -- are the business's local date: a shipment that leaves at 9 pm
must not print as tomorrow. Dates typed on screens arrive as local midnight, so these match them.

settings.business_timezone (e.g. America/Chicago) sets the zone; blank = the server's own time zone. A cloud server
runs on UTC, so set it there."""
from datetime import datetime
from typing import Optional

from app.config.settings import settings


def _zone():
    if not settings.business_timezone:
        return None
    from zoneinfo import ZoneInfo
    return ZoneInfo(settings.business_timezone)


def business_now() -> datetime:
    """Now on the business's clock, naive (as every stored datetime is)."""
    zone = _zone()
    return datetime.now(zone).replace(tzinfo=None) if zone else datetime.now()


def to_business(value: Optional[datetime]) -> Optional[datetime]:
    """A typed date/time with a zone (e.g. "...Z" from an API client) -> naive business time; naive ones are
    already business time and pass through."""
    if value is None or value.tzinfo is None:
        return value
    zone = _zone()
    return value.astimezone(zone).replace(tzinfo=None) if zone else value.astimezone().replace(tzinfo=None)
