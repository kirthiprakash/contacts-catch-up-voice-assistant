"""
Tests for app/services/llm.py

Property 13: LLM extraction result conforms to schema
  For any non-empty transcript string, the result of extract_from_transcript
  shall be a valid ExtractionResult instance (all required fields present,
  correct types, callback.type in {relative, absolute, none}).
  Validates: Requirements 5.3

Unit tests also cover:
  - Retry logic: 2 failures → empty ExtractionResult (Requirements 5.4, 5.5)
  - Parse error → empty ExtractionResult
"""

# Feature: contacts-catch-up-voice-assistant, Property 13: LLM extraction result conforms to schema

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from app.models.memory import CallbackIntent, ExtractionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm_response(content: str):
    """Build a minimal mock openai ChatCompletion response."""
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


def _valid_extraction_json(**overrides) -> str:
    data = {
        "summary": "We caught up about work.",
        "highlights": ["Got a promotion"],
        "facts": [{"key": "job", "value": "engineer"}],
        "followups": ["Send article"],
        "callback": {"type": "none", "value": ""},
        "call_time_preference": "none",
    }
    data.update(overrides)
    return json.dumps(data)


# ---------------------------------------------------------------------------
# Property 13: LLM extraction result conforms to schema
# ---------------------------------------------------------------------------

# Strategy: generate non-empty transcript strings
transcript_strategy = st.text(min_size=1, max_size=500).filter(lambda s: s.strip())

# Strategy: generate valid ExtractionResult JSON payloads
callback_type_st = st.sampled_from(["relative", "absolute", "none"])
call_time_pref_st = st.sampled_from(["morning", "evening", "specific_time", "none"])

valid_extraction_payload_st = st.fixed_dictionaries({
    "summary": st.text(max_size=200),
    "highlights": st.lists(st.text(max_size=50), max_size=5),
    "facts": st.lists(
        st.fixed_dictionaries({"key": st.text(max_size=20), "value": st.text(max_size=50)}),
        max_size=5,
    ),
    "followups": st.lists(st.text(max_size=50), max_size=5),
    "callback": st.fixed_dictionaries({
        "type": callback_type_st,
        "value": st.text(max_size=30),
    }),
    "call_time_preference": call_time_pref_st,
})


@pytest.mark.asyncio
@given(transcript=transcript_strategy, payload=valid_extraction_payload_st)
@h_settings(max_examples=100)
async def test_property_13_extraction_result_conforms_to_schema(transcript: str, payload: dict):
    """
    Property 13: For any non-empty transcript, extract_from_transcript returns
    a valid ExtractionResult with all required fields and correct types.
    Validates: Requirements 5.3
    """
    from app.services.llm import extract_from_transcript

    mock_response = _make_llm_response(json.dumps(payload))

    with patch("app.services.llm.get_settings") as mock_settings, \
         patch("app.services.llm.AsyncOpenAI") as mock_openai_cls:

        mock_settings.return_value = MagicMock(
            OPENAI_BASE_URL="https://api.openai.com/v1",
            OPENAI_API_KEY="test-key",
            OPENAI_MODEL="gpt-4o",
        )
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_openai_cls.return_value = mock_client

        result = await extract_from_transcript(transcript)

    # Must be a valid ExtractionResult instance
    assert isinstance(result, ExtractionResult)

    # All fields must be present with correct types
    assert isinstance(result.summary, str)
    assert isinstance(result.highlights, list)
    assert isinstance(result.facts, list)
    assert isinstance(result.followups, list)
    assert isinstance(result.callback, CallbackIntent)
    assert result.callback.type in {"relative", "absolute", "none"}
    assert result.call_time_preference in {"morning", "evening", "specific_time", "none"}


# ---------------------------------------------------------------------------
# Unit test 12.2: Retry and fallback behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_exhausted_returns_empty_extraction_result():
    """
    When the LLM client raises an exception on every attempt (3 total),
    extract_from_transcript must return an empty ExtractionResult.
    Validates: Requirements 5.4, 5.5
    """
    from app.services.llm import extract_from_transcript

    call_count = 0

    async def _always_fail(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("network error")

    with patch("app.services.llm.get_settings") as mock_settings, \
         patch("app.services.llm.AsyncOpenAI") as mock_openai_cls:

        mock_settings.return_value = MagicMock(
            OPENAI_BASE_URL="https://api.openai.com/v1",
            OPENAI_API_KEY="test-key",
            OPENAI_MODEL="gpt-4o",
        )
        mock_client = AsyncMock()
        mock_client.chat.completions.create = _always_fail
        mock_openai_cls.return_value = mock_client

        result = await extract_from_transcript("Some transcript text.")

    assert isinstance(result, ExtractionResult)
    assert result.summary == ""
    assert result.highlights == []
    assert result.facts == []
    assert result.followups == []
    assert result.callback.type == "none"
    assert result.call_time_preference == "none"
    # Should have attempted exactly 3 times (initial + 2 retries)
    assert call_count == 3


@pytest.mark.asyncio
async def test_parse_error_returns_empty_extraction_result():
    """
    When the LLM returns invalid JSON, extract_from_transcript must return
    an empty ExtractionResult without raising.
    Validates: Requirements 5.5
    """
    from app.services.llm import extract_from_transcript

    mock_response = _make_llm_response("This is not JSON at all!")

    with patch("app.services.llm.get_settings") as mock_settings, \
         patch("app.services.llm.AsyncOpenAI") as mock_openai_cls:

        mock_settings.return_value = MagicMock(
            OPENAI_BASE_URL="https://api.openai.com/v1",
            OPENAI_API_KEY="test-key",
            OPENAI_MODEL="gpt-4o",
        )
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_openai_cls.return_value = mock_client

        result = await extract_from_transcript("Some transcript.")

    assert isinstance(result, ExtractionResult)
    assert result.summary == ""


@pytest.mark.asyncio
async def test_succeeds_on_second_attempt():
    """
    When the first attempt fails but the second succeeds, the valid result
    is returned (not an empty fallback).
    """
    from app.services.llm import extract_from_transcript

    good_payload = _valid_extraction_json(summary="Recovered on retry")
    mock_good_response = _make_llm_response(good_payload)

    attempt = 0

    async def _fail_then_succeed(*args, **kwargs):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            raise RuntimeError("transient error")
        return mock_good_response

    with patch("app.services.llm.get_settings") as mock_settings, \
         patch("app.services.llm.AsyncOpenAI") as mock_openai_cls:

        mock_settings.return_value = MagicMock(
            OPENAI_BASE_URL="https://api.openai.com/v1",
            OPENAI_API_KEY="test-key",
            OPENAI_MODEL="gpt-4o",
        )
        mock_client = AsyncMock()
        mock_client.chat.completions.create = _fail_then_succeed
        mock_openai_cls.return_value = mock_client

        result = await extract_from_transcript("Some transcript.")

    assert isinstance(result, ExtractionResult)
    assert result.summary == "Recovered on retry"
