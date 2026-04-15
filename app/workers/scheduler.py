"""
Background scheduler using APScheduler.

Jobs:
  - Daily cron (09:00 local): calls get_top_contacts, then initiate_call for each;
    also calls ingest_social_updates for each selected contact.
  - 5-minute polling: queries contacts where next_call_at <= now(), calls initiate_call;
    also calls sweep_stale_active_calls().
  - Crash recovery on startup: scans for contacts with next_call_at <= now() and re-queues.

schedule_one_off_call: persists next_call_at to SQLite AND adds an APScheduler DateTrigger job.
All job callbacks are wrapped in try/except; errors are logged without crashing the scheduler.
"""

import asyncio
import logging
from datetime import datetime, UTC
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

# Module-level scheduler instance
_scheduler: Optional[BackgroundScheduler] = None

# Try to import ingest_social_updates — it won't exist until task 17
try:
    from app.services.social.ingest import ingest_social_updates as _ingest_social_updates
    _has_social_ingest = True
except ImportError:
    _has_social_ingest = False
    _ingest_social_updates = None  # type: ignore[assignment]


def _run_async(coro) -> None:
    """Run an async coroutine from a synchronous APScheduler job callback."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(coro)
        else:
            loop.run_until_complete(coro)
    except RuntimeError:
        # No event loop in this thread — create one
        asyncio.run(coro)


async def _daily_cron_job() -> None:
    """
    Daily cron job:
    1. Load all contacts from DB.
    2. Call get_top_contacts to select candidates.
    3. For each selected contact: initiate_call + ingest_social_updates.
    """
    from app.db import get_db, row_to_contact
    from app.services.scoring import get_top_contacts
    from app.services.vapi import initiate_call, AlreadyOnCallError

    logger.info("Daily cron job: starting")
    try:
        db = await get_db()
        try:
            async with db.execute("SELECT * FROM contacts") as cursor:
                rows = await cursor.fetchall()
            contacts = [row_to_contact(row) for row in rows]
        finally:
            await db.close()

        now = datetime.now(UTC)
        selected = get_top_contacts(contacts, now)
        logger.info("Daily cron job: selected %d contact(s) for calls", len(selected))

        for contact in selected:
            try:
                await initiate_call(contact)
            except AlreadyOnCallError:
                logger.warning("Daily cron: contact %s already on a call, skipping", contact.contact_id)
            except Exception as exc:
                logger.error("Daily cron: error initiating call for contact %s: %s", contact.contact_id, exc)

            # Ingest social updates if available
            if _has_social_ingest and _ingest_social_updates is not None:
                try:
                    await _ingest_social_updates(contact)
                except Exception as exc:
                    logger.error(
                        "Daily cron: error ingesting social updates for contact %s: %s",
                        contact.contact_id,
                        exc,
                    )

    except Exception as exc:
        logger.error("Daily cron job failed: %s", exc)

    logger.info("Daily cron job: done")


async def _polling_job() -> None:
    """
    5-minute polling job:
    1. Sweep stale active calls.
    2. Query contacts where next_call_at <= now().
    3. For each: initiate_call.
    """
    from app.db import get_db, row_to_contact
    from app.services.vapi import initiate_call, sweep_stale_active_calls, AlreadyOnCallError

    logger.debug("Polling job: starting")
    try:
        # Sweep stale active calls first
        sweep_stale_active_calls()

        now = datetime.now(UTC)
        now_iso = now.isoformat()

        db = await get_db()
        try:
            async with db.execute(
                "SELECT * FROM contacts WHERE next_call_at IS NOT NULL AND next_call_at <= ?",
                (now_iso,),
            ) as cursor:
                rows = await cursor.fetchall()
            contacts = [row_to_contact(row) for row in rows]
        finally:
            await db.close()

        if contacts:
            logger.info("Polling job: found %d contact(s) with next_call_at <= now", len(contacts))

        for contact in contacts:
            try:
                await initiate_call(contact)
            except AlreadyOnCallError:
                logger.warning("Polling: contact %s already on a call, skipping", contact.contact_id)
            except Exception as exc:
                logger.error("Polling: error initiating call for contact %s: %s", contact.contact_id, exc)

    except Exception as exc:
        logger.error("Polling job failed: %s", exc)

    logger.debug("Polling job: done")


async def _crash_recovery() -> None:
    """
    On startup: scan for contacts with next_call_at <= now() and re-queue them
    as one-off APScheduler jobs (crash recovery for persisted callbacks).
    """
    from app.db import get_db, row_to_contact

    logger.info("Crash recovery: scanning for overdue callbacks")
    try:
        now = datetime.now(UTC)
        now_iso = now.isoformat()

        db = await get_db()
        try:
            async with db.execute(
                "SELECT * FROM contacts WHERE next_call_at IS NOT NULL AND next_call_at <= ?",
                (now_iso,),
            ) as cursor:
                rows = await cursor.fetchall()
            contacts = [row_to_contact(row) for row in rows]
        finally:
            await db.close()

        if contacts:
            logger.info("Crash recovery: found %d overdue contact(s), re-queuing", len(contacts))
            for contact in contacts:
                try:
                    # Schedule immediately (run_at = now) via the polling job on next cycle
                    # The polling loop will pick them up within 5 minutes.
                    # For immediate re-queue, add a one-off job firing right now.
                    if _scheduler is not None and _scheduler.running:
                        _scheduler.add_job(
                            lambda cid=contact.contact_id: _run_async(_call_contact_by_id(cid)),
                            trigger=DateTrigger(run_date=now),
                            id=f"recovery_{contact.contact_id}",
                            replace_existing=True,
                            misfire_grace_time=300,
                        )
                        logger.info(
                            "Crash recovery: re-queued immediate job for contact %s",
                            contact.contact_id,
                        )
                except Exception as exc:
                    logger.error(
                        "Crash recovery: failed to re-queue contact %s: %s",
                        contact.contact_id,
                        exc,
                    )
        else:
            logger.info("Crash recovery: no overdue callbacks found")

    except Exception as exc:
        logger.error("Crash recovery scan failed: %s", exc)


async def _call_contact_by_id(contact_id: str) -> None:
    """Helper: load a contact by ID and initiate a call."""
    from app.db import get_db, row_to_contact
    from app.services.vapi import initiate_call, AlreadyOnCallError

    try:
        db = await get_db()
        try:
            async with db.execute(
                "SELECT * FROM contacts WHERE contact_id = ?", (contact_id,)
            ) as cursor:
                row = await cursor.fetchone()
        finally:
            await db.close()

        if row is None:
            logger.warning("_call_contact_by_id: contact %s not found", contact_id)
            return

        contact = row_to_contact(row)
        await initiate_call(contact)
    except AlreadyOnCallError:
        logger.warning("_call_contact_by_id: contact %s already on a call", contact_id)
    except Exception as exc:
        logger.error("_call_contact_by_id: error for contact %s: %s", contact_id, exc)


def start_scheduler() -> None:
    """
    Starts APScheduler with:
    - Daily cron job at 09:00 (configurable via SCHEDULER_DAILY_HOUR env var)
    - 5-minute polling interval job
    - Crash recovery scan on startup
    """
    global _scheduler

    if _scheduler is not None and _scheduler.running:
        logger.warning("start_scheduler called but scheduler is already running")
        return

    _scheduler = BackgroundScheduler()

    # Daily cron job — default 09:00, configurable
    try:
        from app.config import get_settings
        settings = get_settings()
        daily_hour = getattr(settings, "SCHEDULER_DAILY_HOUR", 9)
    except Exception:
        daily_hour = 9

    _scheduler.add_job(
        lambda: _run_async(_daily_cron_job()),
        trigger=CronTrigger(hour=daily_hour, minute=0),
        id="daily_cron",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # 5-minute polling job
    _scheduler.add_job(
        lambda: _run_async(_polling_job()),
        trigger=IntervalTrigger(minutes=5),
        id="polling_5min",
        replace_existing=True,
        misfire_grace_time=60,
    )

    _scheduler.start()
    logger.info("Scheduler started (daily cron at %02d:00, 5-min polling)", daily_hour)

    # Crash recovery: run immediately in a background thread
    _run_async(_crash_recovery())


def schedule_one_off_call(contact_id: str, run_at: datetime) -> str:
    """
    Persists run_at to contact.next_call_at in SQLite (durable).
    Also adds an APScheduler one-off DateTrigger job.
    Returns the APScheduler job ID.
    """
    # Persist to SQLite synchronously via asyncio
    _run_async(_persist_next_call_at(contact_id, run_at))

    job_id = f"one_off_{contact_id}"

    if _scheduler is not None and _scheduler.running:
        _scheduler.add_job(
            lambda: _run_async(_call_contact_by_id(contact_id)),
            trigger=DateTrigger(run_date=run_at),
            id=job_id,
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info(
            "Scheduled one-off call for contact %s at %s (job_id=%s)",
            contact_id,
            run_at.isoformat(),
            job_id,
        )
    else:
        logger.warning(
            "Scheduler not running; one-off call for contact %s persisted to DB only (will be recovered on next startup)",
            contact_id,
        )

    return job_id


async def _persist_next_call_at(contact_id: str, run_at: datetime) -> None:
    """Persist next_call_at to the contacts table."""
    from app.db import get_db

    try:
        db = await get_db()
        try:
            await db.execute(
                "UPDATE contacts SET next_call_at = ? WHERE contact_id = ?",
                (run_at.isoformat(), contact_id),
            )
            await db.commit()
        finally:
            await db.close()
        logger.debug("Persisted next_call_at=%s for contact %s", run_at.isoformat(), contact_id)
    except Exception as exc:
        logger.error("Failed to persist next_call_at for contact %s: %s", contact_id, exc)


def get_scheduler() -> Optional[BackgroundScheduler]:
    """Return the current scheduler instance (for testing/inspection)."""
    return _scheduler
