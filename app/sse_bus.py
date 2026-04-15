"""
Simple in-process pub/sub bus for SSE live call events.

Webhook handler publishes events; SSE endpoints consume them.
"""

import asyncio
import json
from collections import defaultdict
from typing import AsyncGenerator

# contact_id -> list of subscriber queues
_subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)


def subscribe(contact_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers[contact_id].append(q)
    return q


def unsubscribe(contact_id: str, q: asyncio.Queue) -> None:
    try:
        _subscribers[contact_id].remove(q)
    except ValueError:
        pass
    if not _subscribers[contact_id]:
        _subscribers.pop(contact_id, None)


async def publish(contact_id: str, event: dict) -> None:
    for q in list(_subscribers.get(contact_id, [])):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


async def sse_generator(contact_id: str) -> AsyncGenerator[str, None]:
    """
    Yields SSE-formatted strings for a contact's live call events.
    Sends a keepalive ping every 15 seconds so the connection stays open.
    Stops when a 'call-ended' event is received or the client disconnects.
    """
    q = subscribe(contact_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=15.0)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") in ("call-ended", "end-of-call-report"):
                    break
            except asyncio.TimeoutError:
                # keepalive ping
                yield "data: {\"type\":\"ping\"}\n\n"
    finally:
        unsubscribe(contact_id, q)
