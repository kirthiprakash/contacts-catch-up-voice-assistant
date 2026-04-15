# AGENTS.md — Contacts Catch-Up Voice Assistant

This file provides context for coding assistants working in this repository. Read it before making any changes.
Refer to /.kiro/specs/contacts-catch-up-voice-assistant for the initial requirements, design spec and implementation tasks

---

## What This Project Is

A locally-hosted Python/FastAPI application that places proactive outbound voice calls to a curated list of ~10 contacts. It uses a deterministic scoring engine to decide who to call, enriches conversations with semantic memory from a vector database (Qdrant), and extracts structured insights from call transcripts via an LLM. A lightweight web dashboard handles contact management and call monitoring.

This is a **hackathon POC** — optimise for working demo over production robustness. Mocked external services (social feeds, calendar) are intentional and correct.

---

## Architecture at a Glance

Three runtime concerns:

- **FastAPI HTTP server** — dashboard HTML routes (`/`), contact API (`/api/contacts`), Vapi tool endpoints (`/tools/*`), and webhook handler (`/webhook/vapi`)
- **APScheduler background workers** — daily cron (scoring + call trigger) and 5-minute polling loop (callback checks + stale call sweep)
- **External services** — Vapi (voice calls), Qdrant Cloud (vector memory), OpenAI-compatible LLM (extraction + embeddings), Google Calendar (stubbed by default)

The two datastores have distinct roles: **SQLite** holds structured contact records; **Qdrant** holds semantic memory entries (embeddings of highlights, facts, and social updates per contact).

---

## Directory Structure

```
app/
  config.py              # pydantic-settings env var loading; raises ConfigurationError on missing vars
  main.py                # FastAPI app factory; registers routers; startup lifespan
  db.py                  # aiosqlite connection; init_db(); JSON helpers for tags/social_handles
  models/
    contact.py           # Contact, TimeWindow, SocialHandles Pydantic models
    memory.py            # MemoryEntry, ExtractionResult, CallbackIntent Pydantic models
  routes/
    contacts.py          # /api/contacts CRUD
    calls.py             # /tools/* Vapi tool endpoints + /api/calls manual trigger
    webhook.py           # /webhook/vapi post-call processing
    dashboard.py         # HTML routes: /, /contacts/{id}, /contacts/new
  services/
    scoring.py           # Deterministic scoring engine
    vapi.py              # Outbound call initiation; active call guard
    qdrant.py            # Memory store; ensure_collection_exists on startup
    embedding.py         # nomic-embed-text via OpenAI-compatible endpoint
    llm.py               # Transcript extraction with retry
    calendar.py          # Google Calendar or mock
    social/
      base.py            # SocialAdapterBase ABC + SocialUpdate model
      fixtures.py        # Fixture data keyed by contact name (lowercased) + __default__
      twitter.py
      instagram.py
      linkedin.py
      ingest.py          # ingest_social_updates(contact) — iterates all adapters
  workers/
    scheduler.py         # APScheduler setup; daily cron; 5-min poller; crash recovery
  templates/
    base.html
    contacts/
      list.html
      detail.html
      form.html
tests/
  unit/
  integration/
```

---

## Code Conventions

**Python version:** 3.12+. Use `datetime.now(UTC)` — never `datetime.utcnow()` (deprecated).

**Async:** All service functions are `async`. Use `await` consistently. Do not mix sync blocking calls in async paths.

**Pydantic:** Models live in `app/models/`. Use Pydantic v2. Validate E.164 phone numbers with a field validator on `Contact`. Do not use `dict()` — use `model_dump()`.

**Database:** Raw `aiosqlite` — no ORM. JSON columns (`tags`, `preferred_time_window`, `social_handles`) are serialised with `json.dumps` on write and `json.loads` on read. Helpers for this live in `db.py`.

**Error handling:** Raise typed exceptions (`ConfigurationError`, `VapiError`, `AlreadyOnCallError`, `MemoryStoreError`). Never swallow exceptions silently — always log before continuing.

**Routes:** Maintain a strict separation: `/api/...` routes return JSON only; `/...` routes return HTML only. This allows the HTML frontend to be replaced later without touching the API.

---

## Key Design Decisions to Preserve

**Webhook handler must return 200 immediately.** All post-call processing (LLM extraction, embedding, memory storage, contact update) runs in a FastAPI `BackgroundTasks` task. Never do this work inline — Vapi has a short webhook timeout.

**Idempotency on webhooks.** A `processed_calls` table in SQLite stores processed `call_id` values. The webhook handler checks this before processing and skips duplicates. This table is created in `init_db()`.

**Scoring uses `last_spoken`, not `last_called`.** `last_called` is when a call was initiated (may have been unanswered). `last_spoken` is when a real conversation happened. The scoring formula's `days_since_last_call` term always derives from `last_spoken`.

**Callback override bypasses recency filter.** A contact with `next_call_at <= now` is always selected first by `get_top_contacts`, even if they were recently called. This is intentional — it's an explicit user-requested callback.

**`_active_calls` is `dict[str, datetime]`, not a set.** The dict maps `contact_id → call_started_at`. The 5-minute polling loop calls `sweep_stale_active_calls()` which releases any entry older than 30 minutes. This prevents contacts getting permanently stuck if the post-call webhook is never delivered.

**`get_top_contacts` excludes contacts currently on a call.** Do not rely solely on the guard in `initiate_call` — exclude contacts where `call_started_at IS NOT NULL` at the scoring stage too.

**Qdrant collection is auto-created at startup.** `ensure_collection_exists()` is called in the FastAPI startup lifespan. Collection name: `memories`, vector size: `768` (nomic-embed-text), distance: `Cosine`. Safe to call repeatedly.

**Social adapters use fixture data — no real API calls.** Fixtures are keyed by `contact.name.lower()` with a `__default__` fallback per platform. This is correct and intentional for the POC. Do not add real API credentials or HTTP calls to these adapters.

**Social ingestion is triggered by the daily cron.** `ingest_social_updates(contact)` is called for each contact in the daily scheduling job before scoring, so social memory is fresh before calls are placed.

---

## Scoring Formula

```
score = days_since_last_spoken * 0.6 + category_gap_score * 0.3 + priority_boost * 0.1
```

`days_since_last_spoken` is unbounded — this is a known POC limitation. In practice it dominates the other two terms for contacts not spoken to in many days. Do not attempt to normalise it for the hackathon; it produces correct relative rankings for a small contact list.

---

## Testing

Framework: `pytest` + `pytest-asyncio`. Property-based tests use `Hypothesis` with `@settings(max_examples=100)`.

Tag every property test with:
```python
# Feature: contacts-catch-up-voice-assistant, Property N: <property title>
```

Mock Vapi HTTP calls using `respx` at the `httpx` boundary — do not make real Vapi API calls in tests.

The integration test (`tests/integration/test_call_flow.py`) is a stretch goal. Do not block other work on it.

---

## Environment Variables

All credentials are read from environment variables via `app/config.py`. Required vars:

```
VAPI_API_KEY, VAPI_ASSISTANT_ID, VAPI_PHONE_NUMBER_ID
QDRANT_API_KEY, QDRANT_ENDPOINT
OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN  # optional — mock used if absent
```

The LLM client can be pointed at a local Ollama instance by setting `OPENAI_BASE_URL` to a local endpoint. No code changes required.