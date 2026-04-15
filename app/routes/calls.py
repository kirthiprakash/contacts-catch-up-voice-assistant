import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Flexible Vapi tool-call request body
# ---------------------------------------------------------------------------

class ToolCallRequest(BaseModel):
    """
    Flexible model for Vapi tool-call payloads.
    Vapi sends: { "message": { "toolCallList": [...] }, ... }
    We also accept flat payloads with contact_id at the top level.
    All extra fields are allowed so we don't reject unexpected Vapi fields.
    """
    contact_id: Optional[str] = None
    query: Optional[str] = None
    text: Optional[str] = None
    # Calendar event fields
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    # Allow arbitrary extra fields from Vapi
    model_config = {"extra": "allow"}


def _dict_from_unknown(value: Any) -> dict[str, Any]:
    """Best-effort conversion of unknown tool argument container to dict."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _extract_tool_request(payload: dict[str, Any]) -> ToolCallRequest:
    """
    Extract tool arguments from either:
    1) flat payloads: {contact_id, query, ...}
    2) nested Vapi envelopes: {message: {toolCallList: [...]}}
    """
    candidate_dicts: list[dict[str, Any]] = []

    # Flat / direct forms
    candidate_dicts.append(payload)
    for key in ("arguments", "parameters", "args", "input"):
        candidate_dicts.append(_dict_from_unknown(payload.get(key)))

    # Nested single tool call forms
    for key in ("toolCall", "tool_call"):
        call_obj = _dict_from_unknown(payload.get(key))
        if call_obj:
            candidate_dicts.append(call_obj)
            for k in ("arguments", "parameters", "args", "input"):
                candidate_dicts.append(_dict_from_unknown(call_obj.get(k)))
            function_obj = _dict_from_unknown(call_obj.get("function"))
            if function_obj:
                candidate_dicts.append(function_obj)
                candidate_dicts.append(_dict_from_unknown(function_obj.get("arguments")))

    # Vapi message envelope
    message = _dict_from_unknown(payload.get("message"))
    if message:
        candidate_dicts.append(message)
        tool_calls = message.get("toolCallList") or message.get("toolCalls") or []
        if isinstance(tool_calls, list):
            for item in tool_calls:
                item_dict = _dict_from_unknown(item)
                if not item_dict:
                    continue
                candidate_dicts.append(item_dict)
                for k in ("arguments", "parameters", "args", "input"):
                    candidate_dicts.append(_dict_from_unknown(item_dict.get(k)))
                function_obj = _dict_from_unknown(item_dict.get("function"))
                if function_obj:
                    candidate_dicts.append(function_obj)
                    candidate_dicts.append(_dict_from_unknown(function_obj.get("arguments")))

    merged: dict[str, Any] = {}
    for d in candidate_dicts:
        for k, v in d.items():
            if k not in merged or merged[k] in (None, ""):
                merged[k] = v

    return ToolCallRequest.model_validate(merged)


# ---------------------------------------------------------------------------
# DB helper (shared)
# ---------------------------------------------------------------------------

async def _get_contact(contact_id: str):
    """Fetch a contact by ID or raise 404."""
    from app.db import get_db, row_to_contact

    db = await get_db()
    try:
        async with db.execute(
            "SELECT * FROM contacts WHERE contact_id = ?", (contact_id,)
        ) as cursor:
            row = await cursor.fetchone()
    finally:
        await db.close()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Contact '{contact_id}' not found")

    return row_to_contact(row)


# ---------------------------------------------------------------------------
# Manual trigger endpoint (task 5)
# ---------------------------------------------------------------------------

@router.post("/trigger/{contact_id}", summary="Manually trigger an outbound call for a contact")
async def trigger_call(contact_id: str):
    """
    Manual trigger endpoint — initiates an immediate outbound call via Vapi
    for the given contact_id. Useful for smoke testing before the dashboard exists.
    """
    from app.services.vapi import initiate_call, AlreadyOnCallError

    contact = await _get_contact(contact_id)

    try:
        result = await initiate_call(contact)
    except AlreadyOnCallError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    if result is None:
        return {"status": "error", "detail": "Vapi API error — call not initiated; outcome set to no_answer"}

    return {"status": "initiated", "call_id": result.call_id}


# ---------------------------------------------------------------------------
# Tool endpoints — called by Vapi during a live call
# ---------------------------------------------------------------------------

@router.post("/tools/get_contact_context", summary="Return contact name, last interaction summary, and tags")
async def get_contact_context(body: dict[str, Any]):
    """
    Requirement 4.3: Returns the contact's name, last_call_note, and tags.
    """
    request = _extract_tool_request(body)
    if not request.contact_id:
        logger.warning("get_contact_context missing contact_id. payload_keys=%s", list(body.keys()))
        raise HTTPException(status_code=400, detail="contact_id is required")

    contact = await _get_contact(request.contact_id)

    return {
        "name": contact.name,
        "last_interaction_summary": contact.last_call_note,
        "tags": contact.tags,
    }


@router.post("/tools/get_memory", summary="Retrieve top semantic memories for a contact")
async def get_memory(body: dict[str, Any]):
    """
    Requirement 4.4: Performs a semantic search using an enriched default context
    query built from the contact's name, tags, and last_call_note.
    """
    request = _extract_tool_request(body)
    if not request.contact_id:
        logger.warning("get_memory missing contact_id. payload_keys=%s", list(body.keys()))
        raise HTTPException(status_code=400, detail="contact_id is required")

    contact = await _get_contact(request.contact_id)

    from app.services.qdrant import search_memory

    tags_joined = " ".join(contact.tags) if contact.tags else ""
    last_note = contact.last_call_note or ""
    query = f"{contact.name} {tags_joined} {last_note}".strip()

    try:
        entries = await search_memory(contact.contact_id, query)
    except Exception as exc:
        logger.error(
            "get_memory backend failure for contact_id=%s: %s",
            contact.contact_id,
            exc,
        )
        return {
            "memories": [],
            "status": "degraded",
            "note": "memory backend unavailable",
        }
    return {"memories": [{"text": e.text, "type": e.type, "timestamp": e.timestamp.isoformat()} for e in entries]}


@router.post("/tools/search_memory", summary="Targeted semantic search in memory for a contact")
async def search_memory_tool(body: dict[str, Any]):
    """
    Requirement 4.5 / 4.6: Performs a targeted semantic search using the query
    string from the request body. Must respond within ~2 seconds.
    """
    request = _extract_tool_request(body)
    if not request.contact_id:
        logger.warning("search_memory missing contact_id. payload_keys=%s", list(body.keys()))
        raise HTTPException(status_code=400, detail="contact_id is required")
    if not request.query:
        logger.warning("search_memory missing query. payload_keys=%s", list(body.keys()))
        raise HTTPException(status_code=400, detail="query is required")

    # Validate contact exists (returns 404 if not)
    await _get_contact(request.contact_id)

    from app.services.qdrant import search_memory

    try:
        entries = await search_memory(request.contact_id, request.query)
    except Exception as exc:
        logger.error(
            "search_memory backend failure for contact_id=%s: %s",
            request.contact_id,
            exc,
        )
        return {
            "memories": [],
            "status": "degraded",
            "note": "memory backend unavailable",
        }
    return {"memories": [{"text": e.text, "type": e.type, "timestamp": e.timestamp.isoformat()} for e in entries]}


@router.post("/tools/save_memory", summary="Store a memory entry for a contact")
async def save_memory(body: dict[str, Any]):
    """
    Requirement 4.7: Stores the provided text as a memory entry in the Memory_Store.
    """
    request = _extract_tool_request(body)
    if not request.contact_id:
        logger.warning("save_memory missing contact_id. payload_keys=%s", list(body.keys()))
        raise HTTPException(status_code=400, detail="contact_id is required")
    if not request.text:
        logger.warning("save_memory missing text. payload_keys=%s", list(body.keys()))
        raise HTTPException(status_code=400, detail="text is required")

    # Validate contact exists (returns 404 if not)
    await _get_contact(request.contact_id)

    from app.services.qdrant import store_memory
    from app.models.memory import MemoryEntry

    entry = MemoryEntry(
        contact_id=request.contact_id,
        type="highlight",
        text=request.text,
    )
    try:
        entry_id = await store_memory(entry)
    except Exception as exc:
        logger.error(
            "save_memory backend failure for contact_id=%s: %s",
            request.contact_id,
            exc,
        )
        return {
            "status": "degraded",
            "note": "memory backend unavailable",
        }
    return {"status": "saved", "entry_id": entry_id}


@router.post("/tools/get_calendar_slots", summary="Return available calendar time slots")
async def get_calendar_slots(body: dict[str, Any]):
    """
    Requirement 4.8: Delegates to Calendar Service to return available slots.
    """
    request = _extract_tool_request(body)
    if not request.contact_id:
        logger.warning("get_calendar_slots missing contact_id. payload_keys=%s", list(body.keys()))
        raise HTTPException(status_code=400, detail="contact_id is required")

    # Validate contact exists (returns 404 if not)
    await _get_contact(request.contact_id)

    try:
        from app.services.calendar import get_free_slots
        slots = await get_free_slots()
        return {"slots": [s.model_dump() if hasattr(s, "model_dump") else s for s in slots]}
    except ImportError:
        # Calendar service not yet implemented (task 18)
        return {"slots": [], "note": "Calendar service not yet available"}


@router.post("/tools/create_calendar_event", summary="Create a calendar event for a contact")
async def create_calendar_event(body: dict[str, Any]):
    """
    Requirement 4.8: Delegates to Calendar Service to create a calendar event.
    """
    request = _extract_tool_request(body)
    if not request.contact_id:
        logger.warning("create_calendar_event missing contact_id. payload_keys=%s", list(body.keys()))
        raise HTTPException(status_code=400, detail="contact_id is required")

    contact = await _get_contact(request.contact_id)

    try:
        from app.services.calendar import create_event
        from datetime import datetime

        start = datetime.fromisoformat(request.start_time) if request.start_time else None
        end = datetime.fromisoformat(request.end_time) if request.end_time else None

        event = await create_event(start=start, end=end, contact=contact)
        return {"status": "created", "event": event.model_dump() if hasattr(event, "model_dump") else event}
    except ImportError:
        # Calendar service not yet implemented (task 18)
        return {"status": "unavailable", "note": "Calendar service not yet available"}
