# Contacts Catch-Up Voice Assistant

A locally-hosted voice assistant that proactively places outbound calls to keep personal and professional relationships warm. It uses a deterministic scoring engine to decide who to call, enriches conversations with semantic memory (Qdrant), and extracts structured insights from transcripts via an LLM.

---

## Environment Variable Setup

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `VAPI_API_KEY` | Yes | Your Vapi API key |
| `VAPI_ASSISTANT_ID` | Yes | The Vapi assistant ID to use for outbound calls |
| `VAPI_PHONE_NUMBER_ID` | Yes | The Vapi phone number ID for PSTN calls |
| `QDRANT_API_KEY` | Yes | Qdrant Cloud API key |
| `QDRANT_ENDPOINT` | Yes | Qdrant Cloud endpoint URL |
| `OPENAI_API_KEY` | Yes | OpenAI (or compatible) API key |
| `OPENAI_BASE_URL` | Yes | OpenAI-compatible base URL (e.g. `https://api.openai.com/v1` or a local Ollama endpoint) |
| `OPENAI_MODEL` | Yes | Model name to use for LLM extraction (e.g. `gpt-4o`) |
| `GOOGLE_CLIENT_ID` | No | Google OAuth client ID (calendar integration) |
| `GOOGLE_CLIENT_SECRET` | No | Google OAuth client secret |
| `GOOGLE_REFRESH_TOKEN` | No | Google OAuth refresh token |

> To use a local Ollama instance instead of OpenAI, set `OPENAI_BASE_URL=http://localhost:11434/v1` and `OPENAI_API_KEY=ollama`.

---

## How to Run

1. Install dependencies:

```bash
uv sync --extra dev
```

2. Initialise the database and start the server:

```bash
uv run uvicorn app.main:app --reload --port 8000
```

The API will be available at `http://localhost:8000` and the dashboard at `http://localhost:8000/`.

---

## Exposing the Server via ngrok / `vapi listen`

Vapi needs a publicly reachable URL to deliver webhooks and tool-call requests. You have two options:

### Option A — ngrok

```bash
ngrok http 8000
```

Copy the `https://...ngrok-free.app` URL and set it as your Vapi assistant's server URL:
`https://<your-ngrok-subdomain>.ngrok-free.app/webhook/vapi`

### Option B — Vapi CLI (`vapi listen`)

```bash
# Install the Vapi CLI first: https://docs.vapi.ai/cli
vapi listen --port 8000
```

The CLI will print a tunnel URL — use that as your Vapi webhook URL.

> **Note:** Update the tunnel URL in your Vapi assistant configuration whenever you restart the tunnel. Details on pointing Vapi's webhook URL to the tunnel will be completed in task 16.

---

## Project Structure

```
app/
  config.py          # Environment variable loading (pydantic-settings)
  main.py            # FastAPI app factory + lifespan
  db.py              # SQLite connection and schema (task 2)
  routes/
    contacts.py      # Contact CRUD API (task 3)
    calls.py         # Call trigger + tool endpoints (tasks 5, 11)
    webhook.py       # Vapi webhook handler (tasks 6, 13)
    dashboard.py     # HTML dashboard routes (task 15)
  services/
    vapi.py          # Vapi outbound call service (task 5)
    scoring.py       # Call decision engine (task 8)
    embedding.py     # nomic-embed-text embeddings (task 10)
    qdrant.py        # Qdrant memory store (task 10)
    llm.py           # LLM transcript extractor (task 12)
    calendar.py      # Calendar stub (task 18)
    social/
      base.py        # SocialAdapterBase ABC (task 17)
      fixtures.py    # Fixture data (task 17)
      twitter.py     # Twitter adapter (task 17)
      instagram.py   # Instagram adapter (task 17)
      linkedin.py    # LinkedIn adapter (task 17)
      ingest.py      # Social update ingestion (task 17)
  models/
    contact.py       # Contact Pydantic models (task 2)
    memory.py        # MemoryEntry, ExtractionResult models (task 2)
  workers/
    scheduler.py     # APScheduler jobs (task 9)
  templates/
    base.html        # Base layout (task 15)
    contacts/        # Contact list/detail/form templates (task 15)
tests/
  unit/              # Unit + property-based tests
  integration/       # End-to-end tests (stretch goal)
```
