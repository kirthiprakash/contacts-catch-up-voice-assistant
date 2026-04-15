from datetime import datetime, UTC
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.models.memory import CallbackIntent, ExtractionResult
from app.routes.webhook import (
    VapiCall,
    VapiCallMetadata,
    VapiWebhookPayload,
    classify_outcome,
    process_call_webhook,
)


# Feature: contacts-catch-up-voice-assistant, Property 12: Webhook outcome classification always produces a valid value
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ended_reason", "expected"),
    [
        ("customer-ended-call", "answered"),
        ("assistant-ended-call", "answered"),
        ("busy", "busy"),
        ("line-busy", "busy"),
        ("no-answer", "no_answer"),
        ("voicemail", "no_answer"),
        ("something-unknown", "no_answer"),
    ],
)
async def test_property_12_webhook_outcome_classification_valid(ended_reason, expected):
    result = await classify_outcome(ended_reason)
    assert result in {"answered", "busy", "no_answer"}
    assert result == expected


@pytest.mark.asyncio
async def test_process_call_webhook_stores_memories_and_updates_contact():
    payload = VapiWebhookPayload(
        call=VapiCall(
            id="call-123",
            endedReason="customer-ended-call",
            metadata=VapiCallMetadata(contact_id="contact-123"),
        ),
        transcript="We discussed work and agreed to talk tomorrow.",
    )

    extraction = ExtractionResult(
        summary="Talked about work.",
        highlights=["Promotion update"],
        facts=[{"key": "city", "value": "Seattle"}],
        callback=CallbackIntent(type="relative", value="1 day"),
        call_time_preference="evening",
    )

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.close = AsyncMock()

    with (
        patch("app.db.get_db", new_callable=AsyncMock, return_value=mock_db),
        patch("app.services.llm.extract_from_transcript", new_callable=AsyncMock, return_value=extraction),
        patch("app.services.qdrant.store_memory", new_callable=AsyncMock, side_effect=["mem-1", "mem-2"]),
        patch("app.workers.scheduler.schedule_one_off_call") as mock_schedule,
        patch("app.routes.webhook.mark_call_ended") as mock_mark_ended,
    ):
        await process_call_webhook(payload)

    assert mock_db.execute.await_count >= 2
    mock_schedule.assert_called_once()
    mock_mark_ended.assert_called_once_with("contact-123")


# Feature: contacts-catch-up-voice-assistant, Property 14: Highlights and facts count matches stored memory entries
@pytest.mark.asyncio
@given(
    highlights=st.lists(st.text(min_size=1, max_size=60), max_size=5),
    facts=st.lists(
        st.fixed_dictionaries(
            {
                "key": st.text(min_size=1, max_size=20),
                "value": st.text(min_size=1, max_size=40),
            }
        ),
        max_size=5,
    ),
)
@settings(max_examples=100)
async def test_property_14_highlights_and_facts_count_matches_memory_entries(highlights, facts):
    payload = VapiWebhookPayload(
        call=VapiCall(
            id="call-14",
            endedReason="customer-ended-call",
            metadata=VapiCallMetadata(contact_id="contact-14"),
        ),
        transcript="We talked.",
    )

    extraction = ExtractionResult(
        summary="Summary",
        highlights=highlights,
        facts=facts,
        callback=CallbackIntent(type="none", value=""),
        call_time_preference="none",
    )

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.close = AsyncMock()

    with (
        patch("app.db.get_db", new_callable=AsyncMock, return_value=mock_db),
        patch("app.services.llm.extract_from_transcript", new_callable=AsyncMock, return_value=extraction),
        patch("app.services.qdrant.store_memory", new_callable=AsyncMock, side_effect=[f"mem-{i}" for i in range(len(highlights) + len(facts) or 1)]) as mock_store,
        patch("app.workers.scheduler.schedule_one_off_call"),
        patch("app.routes.webhook.mark_call_ended"),
    ):
        await process_call_webhook(payload)

    expected_count = len(highlights) + len(facts)
    assert mock_store.await_count == expected_count
    stored_types = [call.args[0].type for call in mock_store.await_args_list]
    assert stored_types.count("highlight") == len(highlights)
    assert stored_types.count("fact") == len(facts)


# Feature: contacts-catch-up-voice-assistant, Property 15: Contact fields updated after webhook processing
@pytest.mark.asyncio
@given(
    summary=st.text(min_size=1, max_size=120),
    call_time_preference=st.sampled_from(["morning", "evening", "specific_time", "none"]),
)
@settings(max_examples=100)
async def test_property_15_contact_fields_updated_after_webhook_processing(summary, call_time_preference):
    import app.db as db_module
    from app.models.contact import Contact
    from app.routes.contacts import create_contact

    db_path = Path(tempfile.gettempdir()) / f"contacts_property_15_{uuid4().hex}.db"
    original_url = db_module.DATABASE_URL
    db_module.DATABASE_URL = str(db_path)

    try:
        await db_module.init_db()
        db = await db_module.get_db()
        try:
            await db.execute("DELETE FROM contacts")
            await db.commit()
        finally:
            await db.close()

        contact = Contact(
            contact_id="contact-15",
            name="Property Fifteen",
            phone="+12125550001",
            timezone="UTC",
            call_started_at=datetime.now(UTC),
        )
        await create_contact(contact)

        payload = VapiWebhookPayload(
            call=VapiCall(
                id="call-15",
                endedReason="customer-ended-call",
                metadata=VapiCallMetadata(contact_id=contact.contact_id),
            ),
            transcript="Discussed plans.",
        )

        extraction = ExtractionResult(
            summary=summary,
            highlights=["h1"],
            facts=[{"key": "k", "value": "v"}],
            callback=CallbackIntent(type="none", value=""),
            call_time_preference=call_time_preference,
        )

        with (
            patch("app.services.llm.extract_from_transcript", new_callable=AsyncMock, return_value=extraction),
            patch("app.services.qdrant.store_memory", new_callable=AsyncMock, return_value="mem-1"),
            patch("app.workers.scheduler.schedule_one_off_call"),
            patch("app.routes.webhook.mark_call_ended"),
        ):
            await process_call_webhook(payload)

        db = await db_module.get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM contacts WHERE contact_id = ?",
                (contact.contact_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            updated = db_module.row_to_contact(row)
        finally:
            await db.close()

        assert updated.last_called is not None
        assert updated.last_spoken is not None
        assert updated.last_call_outcome == "answered"
        assert updated.last_call_note == summary
        assert updated.call_started_at is None
        assert updated.call_time_preference == call_time_preference
    finally:
        db_module.DATABASE_URL = original_url
        if db_path.exists():
            db_path.unlink()
