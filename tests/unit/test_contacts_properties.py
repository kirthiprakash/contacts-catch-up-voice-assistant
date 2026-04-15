"""
Property-based tests for contact model/persistence and contact deletion behavior.
Feature: contacts-catch-up-voice-assistant
"""

from datetime import datetime, UTC
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

import app.db as db_module
from app.db import contact_to_row, row_to_contact
from app.models.contact import Contact, E164_PATTERN, SocialHandles, TimeWindow
from app.routes.contacts import create_contact, delete_contact


name_st = st.text(
    min_size=1,
    max_size=40,
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Zs")),
).filter(lambda s: s.strip() != "")
timezone_st = st.sampled_from(["UTC", "America/New_York", "Europe/London", "Asia/Kolkata"])
phone_st = st.from_regex(r"^\+[1-9]\d{6,14}$", fullmatch=True)
tags_st = st.lists(
    st.text(
        min_size=1,
        max_size=20,
        alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
    ),
    max_size=5,
)
time_window_st = st.one_of(
    st.none(),
    st.builds(
        TimeWindow,
        start=st.sampled_from(["09:00", "10:30", "18:00"]),
        end=st.sampled_from(["12:00", "20:00", "22:30"]),
    ),
)
social_handles_st = st.builds(
    SocialHandles,
    twitter=st.one_of(st.none(), st.text(min_size=1, max_size=20)),
    instagram=st.one_of(st.none(), st.text(min_size=1, max_size=20)),
    linkedin=st.one_of(st.none(), st.text(min_size=1, max_size=20)),
)


# Feature: contacts-catch-up-voice-assistant, Property 7: Contact creation round-trip
@given(
    name=name_st,
    phone=phone_st,
    timezone=timezone_st,
    tags=tags_st,
    preferred_time_window=time_window_st,
    social_handles=social_handles_st,
)
@settings(max_examples=100)
def test_property_7_contact_creation_round_trip(
    name: str,
    phone: str,
    timezone: str,
    tags: list[str],
    preferred_time_window: TimeWindow | None,
    social_handles: SocialHandles,
):
    """
    **Validates: Requirements 1.2**
    Serializing a valid Contact to DB row format and back preserves key fields.
    """
    contact = Contact(
        name=name,
        phone=phone,
        timezone=timezone,
        tags=tags,
        preferred_time_window=preferred_time_window,
        social_handles=social_handles,
    )

    row = contact_to_row(contact)
    round_tripped = row_to_contact(row)

    assert round_tripped.contact_id == contact.contact_id
    assert round_tripped.name == contact.name
    assert round_tripped.phone == contact.phone
    assert round_tripped.tags == contact.tags
    assert round_tripped.timezone == contact.timezone
    assert round_tripped.preferred_time_window == contact.preferred_time_window
    assert round_tripped.social_handles == contact.social_handles


# Feature: contacts-catch-up-voice-assistant, Property 8: Invalid phone number rejected
@given(
    invalid_phone=st.text(min_size=1, max_size=20).filter(
        lambda s: E164_PATTERN.match(s) is None
    )
)
@settings(max_examples=100)
def test_property_8_invalid_phone_rejected(invalid_phone: str):
    """
    **Validates: Requirements 1.4**
    Any non-E.164 phone number is rejected by Contact validation.
    """
    with pytest.raises(ValidationError):
        Contact(name="Alice", phone=invalid_phone, timezone="UTC")


# Feature: contacts-catch-up-voice-assistant, Property 9: Missing required field rejected
@given(missing_field=st.sampled_from(["name", "phone", "timezone"]))
@settings(max_examples=100)
def test_property_9_missing_required_field_rejected(missing_field: str):
    """
    **Validates: Requirements 1.3**
    A contact payload missing any required field fails validation.
    """
    payload = {
        "name": "Alice",
        "phone": "+12125550001",
        "timezone": "UTC",
    }
    payload.pop(missing_field)

    with pytest.raises(ValidationError):
        Contact.model_validate(payload)


# Feature: contacts-catch-up-voice-assistant, Property 10: Contact deletion removes all associated data
@pytest.mark.asyncio
@given(
    suffix=st.text(
        min_size=1,
        max_size=12,
        alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
    ).filter(lambda s: s.strip() != "")
)
@settings(max_examples=100)
async def test_property_10_contact_deletion_removes_all_associated_data(suffix: str):
    """
    **Validates: Requirements 1.6**
    Deleting a contact removes the DB row and triggers memory deletion.
    """
    db_path = Path(tempfile.gettempdir()) / f"contacts_property_10_{uuid4().hex}.db"
    original_url = db_module.DATABASE_URL
    db_module.DATABASE_URL = str(db_path)

    try:
        await db_module.init_db()

        # Ensure isolated state for each generated example.
        db = await db_module.get_db()
        try:
            await db.execute("DELETE FROM contacts")
            await db.commit()
        finally:
            await db.close()

        contact = Contact(
            name=f"Alice {suffix}",
            phone="+12125550001",
            timezone="UTC",
            call_started_at=datetime.now(UTC),
        )
        await create_contact(contact)

        with patch("app.routes.contacts.delete_contact_memories", new_callable=AsyncMock) as mock_delete_memories:
            await delete_contact(contact.contact_id)
            mock_delete_memories.assert_awaited_once_with(contact.contact_id)

        db = await db_module.get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM contacts WHERE contact_id = ?",
                (contact.contact_id,),
            )
            row = await cursor.fetchone()
        finally:
            await db.close()

        assert row is None
    finally:
        db_module.DATABASE_URL = original_url
        if db_path.exists():
            db_path.unlink()
