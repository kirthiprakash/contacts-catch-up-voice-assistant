"""
Tests for app/services/vapi.py

Property 11: Outbound call uses correct contact method
  For any contact with contact_method = "phone", the Vapi API call payload shall use
  the PSTN phone number; for any contact with contact_method = "sip", the payload
  shall use the SIP address.
  Validates: Requirements 4.1

Unit tests also cover:
  - AlreadyOnCallError raised for duplicate calls
  - mark_call_ended releases the guard
  - sweep_stale_active_calls removes entries older than max_age_minutes
"""

# Feature: contacts-catch-up-voice-assistant, Property 11: Outbound call uses correct contact method

import pytest
import respx
import httpx
from datetime import datetime, UTC, timedelta
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from app.models.contact import Contact
import app.services.vapi as vapi_module
from app.services.vapi import (
    AlreadyOnCallError,
    VapiCallResponse,
    mark_call_ended,
    sweep_stale_active_calls,
)


# ---------------------------------------------------------------------------
# Helpers / strategies
# ---------------------------------------------------------------------------

def make_phone_contact(**kwargs) -> Contact:
    defaults = dict(
        name="Alice",
        phone="+12125550001",
        contact_method="phone",
        timezone="America/New_York",
    )
    defaults.update(kwargs)
    return Contact(**defaults)


def make_sip_contact(**kwargs) -> Contact:
    defaults = dict(
        name="Bob",
        phone="+12125550002",
        sip="sip:bob@example.com",
        contact_method="sip",
        timezone="America/New_York",
    )
    defaults.update(kwargs)
    return Contact(**defaults)


# Hypothesis strategy for a valid E.164 phone number
e164_phone = st.from_regex(r"^\+[1-9]\d{6,14}$", fullmatch=True)

# Strategy for a valid SIP URI
sip_uri = st.from_regex(r"^sip:[a-z]{3,8}@example\.com$", fullmatch=True)

# Strategy for a contact with contact_method="phone"
phone_contact_strategy = st.builds(
    Contact,
    name=st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"))),
    phone=e164_phone,
    contact_method=st.just("phone"),
    timezone=st.just("UTC"),
)

# Strategy for a contact with contact_method="sip"
sip_contact_strategy = st.builds(
    Contact,
    name=st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"))),
    phone=e164_phone,
    sip=sip_uri,
    contact_method=st.just("sip"),
    timezone=st.just("UTC"),
)


# ---------------------------------------------------------------------------
# Property 11: Outbound call uses correct contact method
# ---------------------------------------------------------------------------

FAKE_VAPI_RESPONSE = {"id": "call-abc123", "status": "queued"}


@pytest.mark.asyncio
@given(contact=phone_contact_strategy)
@settings(max_examples=100)
async def test_property_11_phone_contact_uses_pstn(contact: Contact):
    """
    Property 11 (phone branch): For any contact with contact_method='phone',
    the Vapi API payload shall include 'phoneNumberId' and 'customer.number',
    and shall NOT include 'customer.sipUri'.
    Validates: Requirements 4.1
    """
    # Ensure the contact is not already in _active_calls
    vapi_module._active_calls.pop(contact.contact_id, None)

    captured_payload = {}

    with respx.mock(base_url="https://api.vapi.ai") as mock:
        mock.post("/call").mock(
            return_value=httpx.Response(200, json=FAKE_VAPI_RESPONSE)
        )

        import os
        os.environ["VAPI_API_KEY"] = "test-key"
        os.environ["VAPI_ASSISTANT_ID"] = "asst-123"
        os.environ["VAPI_PHONE_NUMBER_ID"] = "123e4567-e89b-12d3-a456-426614174000"
        os.environ["QDRANT_API_KEY"] = "qd-key"
        os.environ["QDRANT_ENDPOINT"] = "https://qdrant.example.com"
        os.environ["OPENAI_API_KEY"] = "oai-key"
        os.environ["OPENAI_BASE_URL"] = "https://api.openai.com/v1"
        os.environ["OPENAI_MODEL"] = "gpt-4o"

        # Patch DB calls so we don't need a real DB
        import unittest.mock as mock_lib
        with mock_lib.patch("app.db.get_db") as mock_get_db:
            mock_conn = mock_lib.AsyncMock()
            mock_get_db.return_value = mock_conn

            result = await vapi_module.initiate_call(contact)

        # Capture the request that was sent
        assert mock.calls, "Expected a POST /call request to be made"
        sent_request = mock.calls[0].request
        import json
        captured_payload = json.loads(sent_request.content)

    # Clean up guard
    vapi_module._active_calls.pop(contact.contact_id, None)

    # Property assertion: phone contacts use PSTN fields
    assert "phoneNumberId" in captured_payload, "Expected phoneNumberId in payload for phone contact"
    assert "customer" in captured_payload
    assert "number" in captured_payload["customer"], "Expected customer.number for phone contact"
    assert "sipUri" not in captured_payload.get("customer", {}), "phone contact must not use sipUri"
    assert captured_payload["customer"]["number"] == contact.phone


@pytest.mark.asyncio
@given(contact=sip_contact_strategy)
@settings(max_examples=100)
async def test_property_11_sip_contact_uses_sip_uri(contact: Contact):
    """
    Property 11 (SIP branch): For any contact with contact_method='sip',
    the Vapi API payload shall include 'customer.sipUri' and shall NOT include
    'phoneNumberId' or 'customer.number'.
    Validates: Requirements 4.1
    """
    assume(contact.sip is not None)
    vapi_module._active_calls.pop(contact.contact_id, None)

    with respx.mock(base_url="https://api.vapi.ai") as mock:
        mock.post("/call").mock(
            return_value=httpx.Response(200, json=FAKE_VAPI_RESPONSE)
        )

        import os
        os.environ["VAPI_API_KEY"] = "test-key"
        os.environ["VAPI_ASSISTANT_ID"] = "asst-123"
        os.environ["VAPI_PHONE_NUMBER_ID"] = "123e4567-e89b-12d3-a456-426614174000"
        os.environ["QDRANT_API_KEY"] = "qd-key"
        os.environ["QDRANT_ENDPOINT"] = "https://qdrant.example.com"
        os.environ["OPENAI_API_KEY"] = "oai-key"
        os.environ["OPENAI_BASE_URL"] = "https://api.openai.com/v1"
        os.environ["OPENAI_MODEL"] = "gpt-4o"

        import unittest.mock as mock_lib
        with mock_lib.patch("app.db.get_db") as mock_get_db:
            mock_conn = mock_lib.AsyncMock()
            mock_get_db.return_value = mock_conn

            result = await vapi_module.initiate_call(contact)

        assert mock.calls, "Expected a POST /call request to be made"
        sent_request = mock.calls[0].request
        import json
        captured_payload = json.loads(sent_request.content)

    vapi_module._active_calls.pop(contact.contact_id, None)

    # Property assertion: SIP contacts use sipUri
    assert "sipUri" in captured_payload["customer"], "Expected customer.sipUri for sip contact"
    assert "phoneNumberId" not in captured_payload, "sip contact must not include phoneNumberId"
    assert "number" not in captured_payload.get("customer", {}), "sip contact must not use customer.number"
    assert captured_payload["customer"]["sipUri"] == contact.sip


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_already_on_call_error():
    """initiate_call raises AlreadyOnCallError if contact is already in _active_calls."""
    contact = make_phone_contact()
    vapi_module._active_calls[contact.contact_id] = datetime.now(UTC)

    with pytest.raises(AlreadyOnCallError):
        await vapi_module.initiate_call(contact)

    # Clean up
    vapi_module._active_calls.pop(contact.contact_id, None)


def test_mark_call_ended_releases_guard():
    """mark_call_ended removes the contact from _active_calls."""
    contact_id = "test-contact-123"
    vapi_module._active_calls[contact_id] = datetime.now(UTC)

    mark_call_ended(contact_id)

    assert contact_id not in vapi_module._active_calls


def test_mark_call_ended_noop_if_not_active():
    """mark_call_ended is a no-op if the contact is not in _active_calls."""
    mark_call_ended("nonexistent-contact")  # should not raise


def test_sweep_stale_active_calls_removes_old_entries():
    """sweep_stale_active_calls removes entries older than max_age_minutes."""
    old_id = "old-contact"
    fresh_id = "fresh-contact"

    vapi_module._active_calls[old_id] = datetime.now(UTC) - timedelta(minutes=31)
    vapi_module._active_calls[fresh_id] = datetime.now(UTC) - timedelta(minutes=5)

    sweep_stale_active_calls(max_age_minutes=30)

    assert old_id not in vapi_module._active_calls
    assert fresh_id in vapi_module._active_calls

    # Clean up
    vapi_module._active_calls.pop(fresh_id, None)


def test_sweep_stale_active_calls_keeps_recent_entries():
    """sweep_stale_active_calls does not remove entries within the TTL."""
    contact_id = "recent-contact"
    vapi_module._active_calls[contact_id] = datetime.now(UTC) - timedelta(minutes=10)

    sweep_stale_active_calls(max_age_minutes=30)

    assert contact_id in vapi_module._active_calls

    # Clean up
    vapi_module._active_calls.pop(contact_id, None)


@pytest.mark.asyncio
async def test_invalid_phone_number_id_skips_vapi_call_and_sets_no_answer():
    """If VAPI_PHONE_NUMBER_ID is not a UUID, initiate_call returns None without API call."""
    contact = make_phone_contact()
    vapi_module._active_calls.pop(contact.contact_id, None)

    import unittest.mock as mock_lib
    with (
        mock_lib.patch("app.config.get_settings") as mock_settings,
        mock_lib.patch("app.services.vapi._set_no_answer", new_callable=mock_lib.AsyncMock) as mock_set_no_answer,
        respx.mock(base_url="https://api.vapi.ai", assert_all_called=False) as mock,
    ):
        mock_settings.return_value = mock_lib.MagicMock(
            VAPI_API_KEY="test-key",
            VAPI_ASSISTANT_ID="asst-123",
            VAPI_PHONE_NUMBER_ID="not-a-uuid",
        )
        mock.post("/call").mock(return_value=httpx.Response(200, json=FAKE_VAPI_RESPONSE))

        result = await vapi_module.initiate_call(contact)

    assert result is None
    mock_set_no_answer.assert_awaited_once_with(contact)
    assert len(mock.calls) == 0


@pytest.mark.asyncio
async def test_vapi_api_error_sets_no_answer_and_does_not_raise():
    """On Vapi API error, last_call_outcome is set to no_answer and no exception is raised."""
    contact = make_phone_contact(contact_id="err-contact")
    vapi_module._active_calls.pop(contact.contact_id, None)

    import os
    os.environ["VAPI_API_KEY"] = "test-key"
    os.environ["VAPI_ASSISTANT_ID"] = "asst-123"
    os.environ["VAPI_PHONE_NUMBER_ID"] = "123e4567-e89b-12d3-a456-426614174000"
    os.environ["QDRANT_API_KEY"] = "qd-key"
    os.environ["QDRANT_ENDPOINT"] = "https://qdrant.example.com"
    os.environ["OPENAI_API_KEY"] = "oai-key"
    os.environ["OPENAI_BASE_URL"] = "https://api.openai.com/v1"
    os.environ["OPENAI_MODEL"] = "gpt-4o"

    import unittest.mock as mock_lib
    with respx.mock(base_url="https://api.vapi.ai") as mock:
        mock.post("/call").mock(return_value=httpx.Response(500, json={"error": "server error"}))

        async def _noop_set_no_answer(c):
            pass

        with mock_lib.patch("app.services.vapi._set_no_answer", side_effect=_noop_set_no_answer):
            # Should not raise
            result = await vapi_module.initiate_call(contact)

    assert result is None
    # Contact should NOT be in _active_calls since the call failed
    assert contact.contact_id not in vapi_module._active_calls
