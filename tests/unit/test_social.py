from unittest.mock import AsyncMock, patch

import pytest

from app.models.contact import Contact


# Feature: contacts-catch-up-voice-assistant, Property 19: Social updates stored with type "social"
@pytest.mark.asyncio
async def test_property_19_social_updates_stored_with_social_type():
    contact = Contact(
        contact_id="contact-social-001",
        name="Alice",
        phone="+12125550001",
        timezone="UTC",
    )

    captured_entries = []

    async def _capture(entry):
        captured_entries.append(entry)
        return entry.entry_id

    with patch("app.services.social.ingest.store_memory", new_callable=AsyncMock, side_effect=_capture):
        from app.services.social.ingest import ingest_social_updates

        stored_ids = await ingest_social_updates(contact)

    assert stored_ids
    assert captured_entries
    assert all(entry.type == "social" for entry in captured_entries)
    assert all(entry.contact_id == contact.contact_id for entry in captured_entries)
