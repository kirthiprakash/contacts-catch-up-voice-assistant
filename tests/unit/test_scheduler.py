"""
Unit tests for app/workers/scheduler.py

Task 9.1: Verify that after schedule_one_off_call, the contact's next_call_at
is persisted and the APScheduler job exists.
Requirements: 3.3
"""

import asyncio
import os
from datetime import datetime, UTC, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# Set required env vars before importing anything that triggers config loading
os.environ.setdefault("VAPI_API_KEY", "test-key")
os.environ.setdefault("VAPI_ASSISTANT_ID", "asst-123")
os.environ.setdefault("VAPI_PHONE_NUMBER_ID", "pn-456")
os.environ.setdefault("QDRANT_API_KEY", "qd-key")
os.environ.setdefault("QDRANT_ENDPOINT", "https://qdrant.example.com")
os.environ.setdefault("OPENAI_API_KEY", "oai-key")
os.environ.setdefault("OPENAI_BASE_URL", "https://api.openai.com/v1")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o")

import app.workers.scheduler as scheduler_module
from app.workers.scheduler import schedule_one_off_call, start_scheduler, get_scheduler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_scheduler():
    """Stop and reset the module-level scheduler between tests."""
    sched = scheduler_module._scheduler
    if sched is not None and sched.running:
        sched.shutdown(wait=False)
    scheduler_module._scheduler = None


# ---------------------------------------------------------------------------
# Task 9.1: schedule_one_off_call persists next_call_at and creates APScheduler job
# ---------------------------------------------------------------------------

def test_schedule_one_off_call_persists_next_call_at_and_creates_job():
    """
    After schedule_one_off_call(contact_id, run_at):
    - The contact's next_call_at is persisted to SQLite.
    - An APScheduler job with the expected ID exists in the scheduler.
    Requirements: 3.3
    """
    _reset_scheduler()

    contact_id = "test-contact-abc"
    run_at = datetime.now(UTC) + timedelta(hours=1)

    persisted_values = {}

    async def fake_persist(cid, dt):
        persisted_values[cid] = dt

    async def fake_crash_recovery():
        pass  # no-op for this test

    with patch.object(scheduler_module, "_persist_next_call_at", side_effect=fake_persist), \
         patch.object(scheduler_module, "_crash_recovery", side_effect=fake_crash_recovery):

        # Start the scheduler so jobs can be added
        start_scheduler()

        job_id = schedule_one_off_call(contact_id, run_at)

    try:
        sched = get_scheduler()
        assert sched is not None, "Scheduler should be initialized"
        assert sched.running, "Scheduler should be running"

        # Verify the job was added
        job = sched.get_job(job_id)
        assert job is not None, f"Expected APScheduler job '{job_id}' to exist"

        # Verify next_call_at was persisted
        assert contact_id in persisted_values, "next_call_at should have been persisted"
        assert persisted_values[contact_id] == run_at, "Persisted run_at should match the requested time"

        # Verify job ID format
        assert job_id == f"one_off_{contact_id}"

    finally:
        _reset_scheduler()


def test_schedule_one_off_call_returns_job_id():
    """schedule_one_off_call returns a non-empty job ID string."""
    _reset_scheduler()

    contact_id = "contact-xyz"
    run_at = datetime.now(UTC) + timedelta(minutes=30)

    async def fake_persist(cid, dt):
        pass

    async def fake_crash_recovery():
        pass

    with patch.object(scheduler_module, "_persist_next_call_at", side_effect=fake_persist), \
         patch.object(scheduler_module, "_crash_recovery", side_effect=fake_crash_recovery):
        start_scheduler()
        job_id = schedule_one_off_call(contact_id, run_at)

    try:
        assert isinstance(job_id, str)
        assert len(job_id) > 0
        assert contact_id in job_id
    finally:
        _reset_scheduler()


def test_schedule_one_off_call_replaces_existing_job():
    """
    Calling schedule_one_off_call twice for the same contact replaces the existing job
    (replace_existing=True).
    """
    _reset_scheduler()

    contact_id = "contact-replace"
    run_at_1 = datetime.now(UTC) + timedelta(hours=1)
    run_at_2 = datetime.now(UTC) + timedelta(hours=2)

    async def fake_persist(cid, dt):
        pass

    async def fake_crash_recovery():
        pass

    with patch.object(scheduler_module, "_persist_next_call_at", side_effect=fake_persist), \
         patch.object(scheduler_module, "_crash_recovery", side_effect=fake_crash_recovery):
        start_scheduler()
        job_id_1 = schedule_one_off_call(contact_id, run_at_1)
        job_id_2 = schedule_one_off_call(contact_id, run_at_2)

    try:
        # Both calls return the same job ID
        assert job_id_1 == job_id_2

        sched = get_scheduler()
        job = sched.get_job(job_id_2)
        assert job is not None, "Job should still exist after replacement"
    finally:
        _reset_scheduler()


def test_schedule_one_off_call_without_running_scheduler_still_persists():
    """
    If the scheduler is not running, schedule_one_off_call still persists next_call_at
    to the DB (durable) and returns a job ID.
    """
    _reset_scheduler()
    # Ensure scheduler is None / not running
    assert scheduler_module._scheduler is None

    contact_id = "contact-no-sched"
    run_at = datetime.now(UTC) + timedelta(hours=1)

    persisted_values = {}

    async def fake_persist(cid, dt):
        persisted_values[cid] = dt

    with patch.object(scheduler_module, "_persist_next_call_at", side_effect=fake_persist):
        job_id = schedule_one_off_call(contact_id, run_at)

    assert contact_id in persisted_values, "next_call_at should be persisted even without a running scheduler"
    assert job_id == f"one_off_{contact_id}"


# ---------------------------------------------------------------------------
# start_scheduler: basic smoke tests
# ---------------------------------------------------------------------------

def test_start_scheduler_creates_daily_and_polling_jobs():
    """start_scheduler registers the daily_cron and polling_5min jobs."""
    _reset_scheduler()

    async def fake_crash_recovery():
        pass

    with patch.object(scheduler_module, "_crash_recovery", side_effect=fake_crash_recovery):
        start_scheduler()

    try:
        sched = get_scheduler()
        assert sched is not None
        assert sched.running

        job_ids = {job.id for job in sched.get_jobs()}
        assert "daily_cron" in job_ids, "Expected 'daily_cron' job"
        assert "polling_5min" in job_ids, "Expected 'polling_5min' job"
    finally:
        _reset_scheduler()


def test_start_scheduler_idempotent():
    """Calling start_scheduler twice does not crash or create duplicate schedulers."""
    _reset_scheduler()

    async def fake_crash_recovery():
        pass

    with patch.object(scheduler_module, "_crash_recovery", side_effect=fake_crash_recovery):
        start_scheduler()
        first_sched = get_scheduler()
        start_scheduler()  # second call — should be a no-op
        second_sched = get_scheduler()

    try:
        assert first_sched is second_sched, "Should reuse the same scheduler instance"
    finally:
        _reset_scheduler()
