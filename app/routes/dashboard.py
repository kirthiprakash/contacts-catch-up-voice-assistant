from datetime import datetime, UTC

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.db import contact_to_row, get_db, row_to_contact
from app.models.contact import Contact, SocialHandles, TimeWindow
from app.services.vapi import AlreadyOnCallError, initiate_call

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


async def _fetch_contact(contact_id: str) -> Contact:
    db = await get_db()
    try:
        async with db.execute(
            "SELECT * FROM contacts WHERE contact_id = ?",
            (contact_id,),
        ) as cursor:
            row = await cursor.fetchone()
    finally:
        await db.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Contact not found")

    return row_to_contact(row)


@router.get("/")
async def dashboard_index(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM contacts ORDER BY name COLLATE NOCASE")
        rows = await cursor.fetchall()
        contacts = [row_to_contact(row) for row in rows]
    finally:
        await db.close()

    return templates.TemplateResponse(
        request=request,
        name="contacts/list.html",
        context={
            "request": request,
            "contacts": contacts,
            "page_title": "Contacts",
        },
    )


@router.get("/contacts/new")
async def new_contact_form(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="contacts/form.html",
        context={
            "request": request,
            "page_title": "New Contact",
        },
    )


@router.post("/contacts/new")
async def create_contact_from_form(
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    timezone: str = Form(...),
    tags: str = Form(""),
    call_time_preference: str = Form("none"),
    preferred_start: str = Form(""),
    preferred_end: str = Form(""),
    twitter: str = Form(""),
    instagram: str = Form(""),
    linkedin: str = Form(""),
):
    preferred_time_window = None
    if preferred_start and preferred_end:
        preferred_time_window = TimeWindow(start=preferred_start, end=preferred_end)

    contact = Contact(
        name=name,
        phone=phone,
        timezone=timezone,
        tags=[tag.strip() for tag in tags.split(",") if tag.strip()],
        call_time_preference=call_time_preference,
        preferred_time_window=preferred_time_window,
        social_handles=SocialHandles(
            twitter=twitter or None,
            instagram=instagram or None,
            linkedin=linkedin or None,
        ),
    )

    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO contacts (
                contact_id, name, phone, sip, contact_method, tags, timezone,
                last_called, last_spoken, call_time_preference, preferred_time_window,
                next_call_at, priority_boost, last_call_outcome, last_call_note,
                call_started_at, social_handles
            ) VALUES (
                :contact_id, :name, :phone, :sip, :contact_method, :tags, :timezone,
                :last_called, :last_spoken, :call_time_preference, :preferred_time_window,
                :next_call_at, :priority_boost, :last_call_outcome, :last_call_note,
                :call_started_at, :social_handles
            )
            """,
            contact_to_row(contact),
        )
        await db.commit()
    finally:
        await db.close()

    return RedirectResponse(
        url=f"/contacts/{contact.contact_id}",
        status_code=303,
    )


@router.get("/contacts/{contact_id}")
async def contact_detail(request: Request, contact_id: str):
    contact = await _fetch_contact(contact_id)

    try:
        from app.services.qdrant import search_memory

        memories = await search_memory(contact.contact_id, contact.name, top_k=20)
    except Exception:
        memories = []

    highlights = [entry for entry in memories if entry.type == "highlight"]
    facts = [entry for entry in memories if entry.type == "fact"]
    social_updates = [entry for entry in memories if entry.type == "social"]

    timeline: list[dict[str, str | None]] = []
    if contact.last_called:
        timeline.append(
            {
                "label": "Last called",
                "timestamp": contact.last_called.isoformat(),
                "detail": contact.last_call_outcome,
            }
        )
    if contact.last_spoken:
        timeline.append(
            {
                "label": "Last spoken",
                "timestamp": contact.last_spoken.isoformat(),
                "detail": contact.last_call_note,
            }
        )
    if contact.next_call_at:
        timeline.append(
            {
                "label": "Next callback",
                "timestamp": contact.next_call_at.isoformat(),
                "detail": None,
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="contacts/detail.html",
        context={
            "request": request,
            "contact": contact,
            "highlights": highlights,
            "facts": facts,
            "social_updates": social_updates,
            "timeline": timeline,
            "page_title": contact.name,
        },
    )


@router.post("/contacts/{contact_id}/call")
async def manual_call_trigger(contact_id: str):
    contact = await _fetch_contact(contact_id)

    try:
        await initiate_call(contact)
    except AlreadyOnCallError:
        return RedirectResponse(url=f"/contacts/{contact_id}?status=already_on_call", status_code=303)

    db = await get_db()
    try:
        await db.execute(
            "UPDATE contacts SET last_called = ? WHERE contact_id = ?",
            (datetime.now(UTC).isoformat(), contact_id),
        )
        await db.commit()
    finally:
        await db.close()

    return RedirectResponse(url=f"/contacts/{contact_id}?status=initiated", status_code=303)
