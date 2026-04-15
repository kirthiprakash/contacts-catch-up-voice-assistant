import logging
from datetime import datetime, UTC, timedelta

from pydantic import BaseModel, Field

from app.config import get_settings
from app.models.contact import Contact

logger = logging.getLogger(__name__)


class TimeSlot(BaseModel):
    start: datetime
    end: datetime


class CalendarEvent(BaseModel):
    event_id: str
    title: str
    start: datetime
    end: datetime
    status: str = "confirmed"
    attendee_name: str | None = None


def _has_google_credentials() -> bool:
    settings = get_settings()
    return all(
        [
            settings.GOOGLE_CLIENT_ID,
            settings.GOOGLE_CLIENT_SECRET,
            settings.GOOGLE_REFRESH_TOKEN,
        ]
    )


async def get_free_slots() -> list[TimeSlot]:
    if _has_google_credentials():
        logger.info("Google Calendar credentials configured; using mock slot generation for POC")

    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    return [
        TimeSlot(start=now + timedelta(days=1, hours=10), end=now + timedelta(days=1, hours=10, minutes=30)),
        TimeSlot(start=now + timedelta(days=1, hours=15), end=now + timedelta(days=1, hours=15, minutes=30)),
        TimeSlot(start=now + timedelta(days=2, hours=11), end=now + timedelta(days=2, hours=11, minutes=30)),
    ]


async def create_event(
    *,
    start: datetime | None,
    end: datetime | None,
    contact: Contact,
) -> CalendarEvent:
    event_start = start or (datetime.now(UTC) + timedelta(days=1))
    event_end = end or (event_start + timedelta(minutes=30))

    if _has_google_credentials():
        logger.info("Google Calendar credentials configured; using mock event creation for POC")

    return CalendarEvent(
        event_id=f"mock-{contact.contact_id}",
        title=f"Catch up with {contact.name}",
        start=event_start,
        end=event_end,
        attendee_name=contact.name,
    )
