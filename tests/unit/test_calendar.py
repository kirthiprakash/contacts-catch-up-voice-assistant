from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_calendar_stub_mock_mode_returns_slots_and_event():
    from app.models.contact import Contact
    from app.services.calendar import create_event, get_free_slots

    contact = Contact(
        contact_id="contact-calendar-001",
        name="Alice",
        phone="+12125550001",
        timezone="UTC",
    )

    with patch("app.services.calendar.get_settings") as mock_settings:
        mock_settings.return_value = type(
            "Settings",
            (),
            {
                "GOOGLE_CLIENT_ID": "",
                "GOOGLE_CLIENT_SECRET": "",
                "GOOGLE_REFRESH_TOKEN": "",
            },
        )()

        slots = await get_free_slots()
        event = await create_event(start=None, end=None, contact=contact)

    assert slots
    assert event.event_id
    assert event.attendee_name == "Alice"
