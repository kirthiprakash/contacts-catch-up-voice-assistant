from datetime import datetime, UTC
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models.contact import Contact
from app.models.memory import MemoryEntry
from app.services.vapi import VapiCallResponse


CONTACT = Contact(
    contact_id="contact-dashboard-001",
    name="Alice Dashboard",
    phone="+12125550001",
    timezone="UTC",
    tags=["friend", "tech"],
    last_call_note="Talked about a new job.",
    last_called=datetime(2026, 4, 10, 9, 0, tzinfo=UTC),
    last_spoken=datetime(2026, 4, 10, 9, 10, tzinfo=UTC),
)


class _CursorContext:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None


@pytest.fixture
def dashboard_client():
    from app.main import create_app

    with (
        patch("app.db.init_db", new_callable=AsyncMock),
        patch("app.services.qdrant.ensure_collection_exists", new_callable=AsyncMock),
        patch("app.workers.scheduler.start_scheduler"),
    ):
        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as client:
            yield client


def test_dashboard_list_route_returns_html(dashboard_client):
    mock_db = AsyncMock()
    mock_db.execute.return_value = _CursorContext([CONTACT.model_dump(mode="json")])
    mock_db.close = AsyncMock()

    with patch("app.routes.dashboard.get_db", new_callable=AsyncMock, return_value=mock_db), \
         patch("app.routes.dashboard.row_to_contact", return_value=CONTACT):
        response = dashboard_client.get("/")

    assert response.status_code == 200
    assert "Contacts" in response.text
    assert "Alice Dashboard" in response.text


def test_dashboard_detail_route_renders_contact_name(dashboard_client):
    memories = [
        MemoryEntry(contact_id=CONTACT.contact_id, type="highlight", text="Started a new job."),
        MemoryEntry(contact_id=CONTACT.contact_id, type="fact", text="Lives in Seattle."),
    ]

    with patch("app.routes.dashboard._fetch_contact", new_callable=AsyncMock, return_value=CONTACT), \
         patch("app.services.qdrant.search_memory", new_callable=AsyncMock, return_value=memories):
        response = dashboard_client.get(f"/contacts/{CONTACT.contact_id}")

    assert response.status_code == 200
    assert "Alice Dashboard" in response.text
    assert "Started a new job." in response.text


def test_dashboard_new_contact_form_contains_fields(dashboard_client):
    response = dashboard_client.get("/contacts/new")

    assert response.status_code == 200
    assert 'name="name"' in response.text
    assert 'name="phone"' in response.text
    assert 'name="contact_method"' in response.text
    assert 'name="sip"' in response.text
    assert 'name="timezone"' in response.text


def test_dashboard_edit_contact_form_renders_prefilled_fields(dashboard_client):
    with patch("app.routes.dashboard._fetch_contact", new_callable=AsyncMock, return_value=CONTACT):
        response = dashboard_client.get(f"/contacts/{CONTACT.contact_id}/edit")

    assert response.status_code == 200
    assert f'action="/contacts/{CONTACT.contact_id}/edit"' in response.text
    assert f'value="{CONTACT.phone}"' in response.text


def test_dashboard_delete_contact_route_redirects(dashboard_client):
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock()
    mock_db.execute.return_value.fetchone = AsyncMock(return_value={"contact_id": CONTACT.contact_id})
    mock_db.commit = AsyncMock()
    mock_db.close = AsyncMock()

    with (
        patch("app.routes.dashboard.get_db", new_callable=AsyncMock, return_value=mock_db),
        patch("app.services.qdrant.delete_contact_memories", new_callable=AsyncMock) as mock_delete_memories,
    ):
        response = dashboard_client.post(f"/contacts/{CONTACT.contact_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/?status=deleted"
    mock_delete_memories.assert_awaited_once_with(CONTACT.contact_id)


def test_dashboard_manual_call_trigger_redirects_failed_on_vapi_error(dashboard_client):
    with (
        patch("app.routes.dashboard._fetch_contact", new_callable=AsyncMock, return_value=CONTACT),
        patch("app.routes.dashboard.initiate_call", new_callable=AsyncMock, return_value=None),
    ):
        response = dashboard_client.post(f"/contacts/{CONTACT.contact_id}/call", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/contacts/{CONTACT.contact_id}?status=failed"


def test_dashboard_manual_call_trigger_redirects_initiated_on_success(dashboard_client):
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.close = AsyncMock()

    with (
        patch("app.routes.dashboard._fetch_contact", new_callable=AsyncMock, return_value=CONTACT),
        patch("app.routes.dashboard.initiate_call", new_callable=AsyncMock, return_value=VapiCallResponse("call-1", {})),
        patch("app.routes.dashboard.get_db", new_callable=AsyncMock, return_value=mock_db),
    ):
        response = dashboard_client.post(f"/contacts/{CONTACT.contact_id}/call", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/contacts/{CONTACT.contact_id}?status=initiated"
    assert mock_db.execute.await_count == 1
