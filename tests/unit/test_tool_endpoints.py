"""
Unit tests for Vapi tool endpoints (task 11).

Tests cover:
- POST /tools/get_contact_context — returns name, last_call_note, tags
- POST /tools/get_memory — calls search_memory with enriched query
- POST /tools/search_memory — calls search_memory with provided query
- POST /tools/save_memory — calls store_memory with provided text
- POST /tools/get_calendar_slots — delegates to Calendar Service (or stub)
- POST /tools/create_calendar_event — delegates to Calendar Service (or stub)
- All endpoints return 404 if contact not found
"""

import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime, UTC

# Required settings for calendar/config access in tool endpoints.
os.environ.setdefault("VAPI_API_KEY", "test-key")
os.environ.setdefault("VAPI_ASSISTANT_ID", "asst-123")
os.environ.setdefault("VAPI_PHONE_NUMBER_ID", "pn-456")
os.environ.setdefault("QDRANT_API_KEY", "qd-key")
os.environ.setdefault("QDRANT_ENDPOINT", "https://qdrant.example.com")
os.environ.setdefault("OPENAI_API_KEY", "oai-key")
os.environ.setdefault("OPENAI_BASE_URL", "https://api.openai.com/v1")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CONTACT_ID = "contact-test-001"

SAMPLE_CONTACT_ROW = {
    "contact_id": CONTACT_ID,
    "name": "Alice",
    "phone": "+12125550001",
    "sip": None,
    "contact_method": "phone",
    "tags": '["friend", "tech"]',
    "timezone": "America/New_York",
    "last_called": None,
    "last_spoken": None,
    "call_time_preference": "none",
    "preferred_time_window": None,
    "next_call_at": None,
    "priority_boost": 0.0,
    "last_call_outcome": None,
    "last_call_note": "We talked about her new job.",
    "call_started_at": None,
    "social_handles": '{"twitter": null, "instagram": null, "linkedin": null}',
}


def make_mock_row(data: dict):
    """Create a mock aiosqlite.Row-like object."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    row.keys = lambda: data.keys()

    # Make it work with dict(row)
    def items():
        return data.items()

    row.__iter__ = lambda self: iter(data.keys())
    # Support dict(row) via mapping protocol
    row.keys.return_value = list(data.keys())

    # The simplest approach: make it behave like a dict
    mock_row = MagicMock()
    mock_row.__class__ = dict
    # Return a real dict-like object
    return data


@pytest.fixture
def client_with_contact():
    """TestClient with a mock contact in the DB."""
    from app.main import create_app

    with (
        patch("app.services.qdrant.ensure_collection_exists", new_callable=AsyncMock),
        patch("app.workers.scheduler.start_scheduler"),
    ):
        app = create_app()

        # Patch _get_contact at the route level to return a real Contact
        from app.models.contact import Contact
        from app.db import deserialize_tags, deserialize_social_handles

        contact = Contact(
            contact_id=CONTACT_ID,
            name="Alice",
            phone="+12125550001",
            contact_method="phone",
            tags=["friend", "tech"],
            timezone="America/New_York",
            last_call_note="We talked about her new job.",
        )

        with patch("app.routes.calls._get_contact", new_callable=AsyncMock, return_value=contact):
            with TestClient(app, raise_server_exceptions=True) as c:
                yield c, contact


@pytest.fixture
def client_no_contact():
    """TestClient where _get_contact raises 404."""
    from app.main import create_app
    from fastapi import HTTPException

    with (
        patch("app.services.qdrant.ensure_collection_exists", new_callable=AsyncMock),
        patch("app.workers.scheduler.start_scheduler"),
    ):
        app = create_app()

        async def _raise_404(contact_id: str):
            raise HTTPException(status_code=404, detail=f"Contact '{contact_id}' not found")

        with patch("app.routes.calls._get_contact", side_effect=_raise_404):
            with TestClient(app, raise_server_exceptions=False) as c:
                yield c


# ---------------------------------------------------------------------------
# get_contact_context
# ---------------------------------------------------------------------------

def test_get_contact_context_returns_name_note_tags(client_with_contact):
    client, contact = client_with_contact
    response = client.post("/api/calls/tools/get_contact_context", json={"contact_id": CONTACT_ID})
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Alice"
    assert data["last_interaction_summary"] == "We talked about her new job."
    assert "friend" in data["tags"]
    assert "tech" in data["tags"]


def test_get_contact_context_response_contract(client_with_contact):
    """Contract test for Vapi tool response properties."""
    client, _ = client_with_contact
    response = client.post("/api/calls/tools/get_contact_context", json={"contact_id": CONTACT_ID})
    assert response.status_code == 200
    data = response.json()

    # Response properties expected by the tool schema in Vapi
    assert set(data.keys()) == {"name", "last_interaction_summary", "tags"}
    assert isinstance(data["name"], str)
    assert isinstance(data["tags"], list)
    assert data["last_interaction_summary"] is None or isinstance(data["last_interaction_summary"], str)


def test_get_contact_context_404_if_not_found(client_no_contact):
    response = client_no_contact.post("/api/calls/tools/get_contact_context", json={"contact_id": "nonexistent"})
    assert response.status_code == 404


def test_get_contact_context_400_if_no_contact_id(client_with_contact):
    client, _ = client_with_contact
    response = client.post("/api/calls/tools/get_contact_context", json={})
    assert response.status_code == 400


def test_get_contact_context_accepts_nested_vapi_toolcall_payload(client_with_contact):
    client, _ = client_with_contact
    response = client.post(
        "/api/calls/tools/get_contact_context",
        json={
            "message": {
                "toolCallList": [
                    {
                        "function": {
                            "arguments": {
                                "contact_id": CONTACT_ID,
                            }
                        }
                    }
                ]
            }
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Alice"


# ---------------------------------------------------------------------------
# get_memory
# ---------------------------------------------------------------------------

def test_get_memory_calls_search_with_enriched_query(client_with_contact):
    client, contact = client_with_contact
    from app.models.memory import MemoryEntry

    mock_entries = [
        MemoryEntry(contact_id=CONTACT_ID, type="highlight", text="She got a new job at Acme."),
    ]

    with patch("app.services.qdrant.search_memory", new_callable=AsyncMock, return_value=mock_entries) as mock_search:
        response = client.post("/api/calls/tools/get_memory", json={"contact_id": CONTACT_ID})

    assert response.status_code == 200
    data = response.json()
    assert "memories" in data
    assert len(data["memories"]) == 1
    assert data["memories"][0]["text"] == "She got a new job at Acme."

    # Verify the enriched query was used
    mock_search.assert_called_once()
    call_args = mock_search.call_args
    query_used = call_args[0][1]  # second positional arg is the query
    assert "Alice" in query_used
    assert "friend" in query_used or "tech" in query_used


def test_get_memory_404_if_not_found(client_no_contact):
    response = client_no_contact.post("/api/calls/tools/get_memory", json={"contact_id": "nonexistent"})
    assert response.status_code == 404


def test_get_memory_400_if_no_contact_id(client_with_contact):
    client, _ = client_with_contact
    response = client.post("/api/calls/tools/get_memory", json={})
    assert response.status_code == 400


def test_get_memory_returns_degraded_when_backend_unavailable(client_with_contact):
    client, _ = client_with_contact
    with patch("app.services.qdrant.search_memory", new_callable=AsyncMock, side_effect=RuntimeError("connect failed")):
        response = client.post("/api/calls/tools/get_memory", json={"contact_id": CONTACT_ID})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["memories"] == []


def test_get_memory_accepts_nested_vapi_toolcall_payload(client_with_contact):
    client, _ = client_with_contact
    with patch("app.services.qdrant.search_memory", new_callable=AsyncMock, return_value=[]):
        response = client.post(
            "/api/calls/tools/get_memory",
            json={
                "message": {
                    "toolCallList": [
                        {
                            "function": {
                                "arguments": "{\"contact_id\": \"contact-test-001\"}"
                            }
                        }
                    ]
                }
            },
        )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# search_memory
# ---------------------------------------------------------------------------

def test_search_memory_uses_provided_query(client_with_contact):
    client, contact = client_with_contact
    from app.models.memory import MemoryEntry

    mock_entries = [
        MemoryEntry(contact_id=CONTACT_ID, type="fact", text="She loves hiking."),
    ]

    with patch("app.services.qdrant.search_memory", new_callable=AsyncMock, return_value=mock_entries) as mock_search:
        response = client.post(
            "/api/calls/tools/search_memory",
            json={"contact_id": CONTACT_ID, "query": "hiking outdoor activities"},
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data["memories"]) == 1
    assert data["memories"][0]["text"] == "She loves hiking."

    mock_search.assert_called_once_with(CONTACT_ID, "hiking outdoor activities")


def test_search_memory_404_if_not_found(client_no_contact):
    response = client_no_contact.post(
        "/api/calls/tools/search_memory",
        json={"contact_id": "nonexistent", "query": "something"},
    )
    assert response.status_code == 404


def test_search_memory_400_if_no_query(client_with_contact):
    client, _ = client_with_contact
    response = client.post("/api/calls/tools/search_memory", json={"contact_id": CONTACT_ID})
    assert response.status_code == 400


def test_search_memory_returns_degraded_when_backend_unavailable(client_with_contact):
    client, _ = client_with_contact
    with patch("app.services.qdrant.search_memory", new_callable=AsyncMock, side_effect=RuntimeError("connect failed")):
        response = client.post(
            "/api/calls/tools/search_memory",
            json={"contact_id": CONTACT_ID, "query": "latest"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["memories"] == []


# ---------------------------------------------------------------------------
# save_memory
# ---------------------------------------------------------------------------

def test_save_memory_stores_entry(client_with_contact):
    client, contact = client_with_contact

    with patch("app.services.qdrant.store_memory", new_callable=AsyncMock, return_value="entry-id-123") as mock_store:
        response = client.post(
            "/api/calls/tools/save_memory",
            json={"contact_id": CONTACT_ID, "text": "She mentioned she's moving to Seattle."},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "saved"
    assert data["entry_id"] == "entry-id-123"

    mock_store.assert_called_once()
    stored_entry = mock_store.call_args[0][0]
    assert stored_entry.contact_id == CONTACT_ID
    assert stored_entry.text == "She mentioned she's moving to Seattle."


def test_save_memory_404_if_not_found(client_no_contact):
    response = client_no_contact.post(
        "/api/calls/tools/save_memory",
        json={"contact_id": "nonexistent", "text": "some text"},
    )
    assert response.status_code == 404


def test_save_memory_400_if_no_text(client_with_contact):
    client, _ = client_with_contact
    response = client.post("/api/calls/tools/save_memory", json={"contact_id": CONTACT_ID})
    assert response.status_code == 400


def test_save_memory_returns_degraded_when_backend_unavailable(client_with_contact):
    client, _ = client_with_contact
    with patch("app.services.qdrant.store_memory", new_callable=AsyncMock, side_effect=RuntimeError("connect failed")):
        response = client.post(
            "/api/calls/tools/save_memory",
            json={"contact_id": CONTACT_ID, "text": "remember this"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"


# ---------------------------------------------------------------------------
# get_calendar_slots
# ---------------------------------------------------------------------------

def test_get_calendar_slots_returns_slots_or_stub(client_with_contact):
    client, _ = client_with_contact
    response = client.post("/api/calls/tools/get_calendar_slots", json={"contact_id": CONTACT_ID})
    assert response.status_code == 200
    data = response.json()
    assert "slots" in data


def test_get_calendar_slots_404_if_not_found(client_no_contact):
    response = client_no_contact.post("/api/calls/tools/get_calendar_slots", json={"contact_id": "nonexistent"})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# create_calendar_event
# ---------------------------------------------------------------------------

def test_create_calendar_event_returns_response(client_with_contact):
    client, _ = client_with_contact
    response = client.post(
        "/api/calls/tools/create_calendar_event",
        json={
            "contact_id": CONTACT_ID,
            "start_time": "2025-01-15T10:00:00",
            "end_time": "2025-01-15T11:00:00",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "status" in data


def test_create_calendar_event_404_if_not_found(client_no_contact):
    response = client_no_contact.post(
        "/api/calls/tools/create_calendar_event",
        json={"contact_id": "nonexistent"},
    )
    assert response.status_code == 404
