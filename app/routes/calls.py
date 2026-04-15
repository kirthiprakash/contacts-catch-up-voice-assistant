from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Optional

router = APIRouter()


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
async def get_contact_context(body: ToolCallRequest):
    """
    Requirement 4.3: Returns the contact's name, last_call_note, and tags.
    """
    if not body.contact_id:
        raise HTTPException(status_code=400, detail="contact_id is required")

    contact = await _get_contact(body.contact_id)

    return {
        "name": contact.name,
        "last_interaction_summary": contact.last_call_note,
        "tags": contact.tags,
    }


@router.post("/tools/get_memory", summary="Retrieve top semantic memories for a contact")
async def get_memory(body: ToolCallRequest):
    """
    Requirement 4.4: Performs a semantic search using an enriched default context
    query built from the contact's name, tags, and last_call_note.
    """
    if not body.contact_id:
        raise HTTPException(status_code=400, detail="contact_id is required")

    contact = await _get_contact(body.contact_id)

    from app.services.qdrant import search_memory

    tags_joined = " ".join(contact.tags) if contact.tags else ""
    last_note = contact.last_call_note or ""
    query = f"{contact.name} {tags_joined} {last_note}".strip()

    entries = await search_memory(contact.contact_id, query)
    return {"memories": [{"text": e.text, "type": e.type, "timestamp": e.timestamp.isoformat()} for e in entries]}


@router.post("/tools/search_memory", summary="Targeted semantic search in memory for a contact")
async def search_memory_tool(body: ToolCallRequest):
    """
    Requirement 4.5 / 4.6: Performs a targeted semantic search using the query
    string from the request body. Must respond within ~2 seconds.
    """
    if not body.contact_id:
        raise HTTPException(status_code=400, detail="contact_id is required")
    if not body.query:
        raise HTTPException(status_code=400, detail="query is required")

    # Validate contact exists (returns 404 if not)
    await _get_contact(body.contact_id)

    from app.services.qdrant import search_memory

    entries = await search_memory(body.contact_id, body.query)
    return {"memories": [{"text": e.text, "type": e.type, "timestamp": e.timestamp.isoformat()} for e in entries]}


@router.post("/tools/save_memory", summary="Store a memory entry for a contact")
async def save_memory(body: ToolCallRequest):
    """
    Requirement 4.7: Stores the provided text as a memory entry in the Memory_Store.
    """
    if not body.contact_id:
        raise HTTPException(status_code=400, detail="contact_id is required")
    if not body.text:
        raise HTTPException(status_code=400, detail="text is required")

    # Validate contact exists (returns 404 if not)
    await _get_contact(body.contact_id)

    from app.services.qdrant import store_memory
    from app.models.memory import MemoryEntry

    entry = MemoryEntry(
        contact_id=body.contact_id,
        type="highlight",
        text=body.text,
    )
    entry_id = await store_memory(entry)
    return {"status": "saved", "entry_id": entry_id}


@router.post("/tools/get_calendar_slots", summary="Return available calendar time slots")
async def get_calendar_slots(body: ToolCallRequest):
    """
    Requirement 4.8: Delegates to Calendar Service to return available slots.
    """
    if not body.contact_id:
        raise HTTPException(status_code=400, detail="contact_id is required")

    # Validate contact exists (returns 404 if not)
    await _get_contact(body.contact_id)

    try:
        from app.services.calendar import get_free_slots
        slots = await get_free_slots()
        return {"slots": [s.model_dump() if hasattr(s, "model_dump") else s for s in slots]}
    except ImportError:
        # Calendar service not yet implemented (task 18)
        return {"slots": [], "note": "Calendar service not yet available"}


@router.post("/tools/create_calendar_event", summary="Create a calendar event for a contact")
async def create_calendar_event(body: ToolCallRequest):
    """
    Requirement 4.8: Delegates to Calendar Service to create a calendar event.
    """
    if not body.contact_id:
        raise HTTPException(status_code=400, detail="contact_id is required")

    contact = await _get_contact(body.contact_id)

    try:
        from app.services.calendar import create_event
        from datetime import datetime

        start = datetime.fromisoformat(body.start_time) if body.start_time else None
        end = datetime.fromisoformat(body.end_time) if body.end_time else None

        event = await create_event(start=start, end=end, contact=contact)
        return {"status": "created", "event": event.model_dump() if hasattr(event, "model_dump") else event}
    except ImportError:
        # Calendar service not yet implemented (task 18)
        return {"status": "unavailable", "note": "Calendar service not yet available"}
