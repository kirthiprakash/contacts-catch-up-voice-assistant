# Contacts Catch-Up Voice Assistant

> *"You haven't spoken to Maya in 47 days. Want me to give her a call?"*

We all have people in our lives we genuinely care about but consistently fail to keep up with — old college friends, parents of your kids' friends, former colleagues you swore you'd stay in touch with. Life gets busy. Months pass. The guilt builds.

This project is a **personal relationship manager that actually takes action**. It runs in the background, scores your contacts by how overdue a conversation is, and places outbound AI phone calls on your behalf — armed with context about who they are, what you last talked about, and what's been happening in their life. After every call it updates its memory so the next one feels even more personal.

---

## What it actually does

A scheduler runs periodically and picks the most overdue contact using a scoring engine (time since last call, priority boosts, preferred call times). It then:

1. **Calls them** via Vapi — a real phone call, not a notification
2. **Introduces itself** as calling on your behalf, with your name and context about your relationship
3. **Remembers the conversation** — highlights, key facts, and a summary are stored in a vector database (Qdrant)
4. **Updates your dashboard** in real-time as the call happens via Server-Sent Events
5. **Logs the outcome** — answered, busy, or no-answer — and schedules the next attempt accordingly

---

## Example conversations

**Catching up with a friend you haven't spoken to in months:**
> "Hey Maya! This is an AI assistant calling on behalf of Kirthi. He's been meaning to catch up for a while and wanted to check in — last time you two spoke, you'd just started a new job. How's that going?"

**Reconnecting after a long gap:**
> "Hi David, calling on behalf of Kirthi. He mentioned you were in London recently — did you end up making it there? He'd love to hear how it went."

**Handling a busy contact gracefully:**
> If no one picks up, the call is logged as `no_answer` and the contact moves back into the queue for another attempt — no awkward voicemails unless you configure it.

---

## Architecture

```
Scheduler → Scoring engine → Vapi outbound call
                                     ↓
                          AI conversation (with tools)
                                     ↓
                          End-of-call webhook → Process transcript
                                     ↓
                    ┌────────────────┼────────────────┐
                    ↓                ↓                ↓
              Update contact    Store memories    Notify UI (SSE)
               (SQLite)          (Qdrant)         (live view)
```

**Stack:**
- **FastAPI** — REST API + SSE streaming
- **Vapi** — outbound voice calls + AI conversation
- **Qdrant** — vector database for semantic memory
- **APScheduler** — call scheduling
- **SQLite (aiosqlite)** — contact and call history storage
- **Vanilla JS SPA** — modern dark-theme dashboard, no build step

---

## Quick Start

### 1. Clone and install

```bash
git clone <repo>
cd contacts-catch-up-voice-assistant
uv sync --extra dev
```

### 2. Configure environment

Copy the example and fill in your credentials:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `VAPI_API_KEY` | Yes | Your Vapi API key — [vapi.ai](https://vapi.ai) |
| `VAPI_ASSISTANT_ID` | Yes | The assistant ID created by `setup_vapi.py` |
| `VAPI_PHONE_NUMBER_ID` | Yes | Your Vapi phone number ID for outbound PSTN calls |
| `APP_BASE` | Yes | Public URL of this server (ngrok URL when developing) |
| `USER_NAME` | Yes | Your name — the AI introduces itself as calling on your behalf |
| `QDRANT_API_KEY` | Yes | Qdrant Cloud API key — [cloud.qdrant.io](https://cloud.qdrant.io) |
| `QDRANT_ENDPOINT` | Yes | Qdrant Cloud endpoint URL |
| `OPENAI_API_KEY` | Yes | OpenAI API key (used for fallback outcome classification) |
| `OPENAI_BASE_URL` | Yes | OpenAI-compatible base URL (`https://api.openai.com/v1`) |
| `OPENAI_MODEL` | Yes | Model name (e.g. `gpt-4o`) |
| `GOOGLE_CLIENT_ID` | No | Google OAuth — for calendar-aware scheduling |
| `GOOGLE_CLIENT_SECRET` | No | Google OAuth client secret |
| `GOOGLE_REFRESH_TOKEN` | No | Google OAuth refresh token |

### 3. Expose your server publicly

Vapi needs a public URL to send webhooks and tool-call requests back to your server.

**Option A — ngrok (recommended for development):**

```bash
ngrok http 8000
# Copy the https://xxx.ngrok-free.app URL → set as APP_BASE in .env
```

**Option B — Vapi CLI:**

```bash
vapi listen --port 8000
```

### 4. Provision Vapi tools and assistant

Run the setup script once. It creates all 6 conversation tools and the assistant on your Vapi account:

```bash
python scripts/setup_vapi.py
```

This will:
- Create 6 server tools pointing to your `APP_BASE` (get context, get/save memory, get/update calendar, get social updates)
- Create the assistant with the right system prompt, voice, transcriber config, and `serverUrl` for webhooks
- Print the `VAPI_ASSISTANT_ID` — copy it into your `.env`

> **Re-running:** The script exits with an error if any of the tool names already exist, to avoid duplicates. Delete existing tools from the Vapi dashboard before re-running.

### 5. Seed sample contacts (optional but recommended for demos)

Populates the database with 5 realistic contacts — complete with past call notes, social media handles, and conversation memories already stored in Qdrant:

```bash
python scripts/seed_contacts.py
```

This creates contacts like:
- **Maya Patel** — college friend, just got promoted to Staff Engineer, cat had kittens
- **David Okafor** — ex-colleague who left Stripe to build a climate-tech startup
- **Sarah Chen** — family friend who moved to London, working on a novel
- **Raj Sundaram** — university friend in Singapore, competitive chess player
- **Priya Menon** — school friend, doctor considering a Johns Hopkins fellowship

Each contact has realistic `last_called` timestamps, outcomes, call notes, and multiple memory entries (highlights, facts, summaries, social updates) so the AI has rich context to draw on during a call. Safe to re-run — skips contacts that already exist by name.

### 6. Start the server

```bash
uv run uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000` — the dashboard should load with your contact list.

---

## Setting up Vapi — in detail

The assistant relies on 6 server tools that Vapi calls back to your server during a live conversation:

| Tool | Endpoint | What it does |
|---|---|---|
| `get_contact_context` | `POST /api/calls/tools/get_contact_context` | Returns contact name, tags, and last call summary |
| `get_memory` | `POST /api/calls/tools/get_memory` | Semantic search over past conversation highlights |
| `save_memory` | `POST /api/calls/tools/save_memory` | Stores a new fact or highlight during the call |
| `get_calendar_events` | `POST /api/calls/tools/get_calendar_events` | Returns upcoming calendar events for context |
| `update_calendar` | `POST /api/calls/tools/update_calendar` | Schedules a follow-up or callback |
| `get_social_updates` | `POST /api/calls/tools/get_social_updates` | Returns recent social activity for conversation starters |

The assistant also has a `serverUrl` pointing to `POST /webhook/vapi` — Vapi calls this at the end of every call with the full transcript, analysis summary, and artifact data.

### Tool request/response format

Vapi sends tool calls in this envelope:

```json
{
  "message": {
    "toolWithToolCallList": [{
      "toolCall": {
        "id": "call_abc123",
        "function": {
          "name": "save_memory",
          "arguments": { "contact_id": "...", "text": "She mentioned she's training for a marathon" }
        }
      }
    }]
  }
}
```

Your server must respond with:

```json
{
  "results": [{
    "toolCallId": "call_abc123",
    "result": { "status": "saved" }
  }]
}
```

The `setup_vapi.py` script handles all the tool registration so you don't need to configure this manually.

### Testing without Vapi

You can simulate the full end-of-call flow locally without ngrok by POSTing directly to the webhook endpoint:

```bash
curl -X POST http://localhost:8000/webhook/vapi \
  -H "Content-Type: application/json" \
  -d '{
    "type": "end-of-call-report",
    "call": {
      "id": "test-001",
      "endedReason": "customer-ended-call",
      "metadata": {"contact_id": "<your-contact-id>"}
    },
    "analysis": {
      "summary": "Had a great catch-up. She is moving to Seattle next month and is excited about the new role."
    },
    "artifact": {
      "messages": [
        {"role": "user", "message": "Yeah I'm really excited, the new job starts in May!"},
        {"role": "user", "message": "We should definitely catch up in person when I'm settled in."}
      ]
    }
  }'
```

This will update the contact's `last_called`, `last_call_note`, and store memory entries in Qdrant — same as a real call.

---

## Dashboard

The web UI at `http://localhost:8000` gives you:

- **Contact list** with search, last outcome status dots, and live pulse indicators during active calls
- **Contact detail** with all fields — phone, SIP URI, timezone, tags, social handles, call history
- **Create / Edit / Delete** contacts with a full-featured modal form
- **Call Now** button that triggers an immediate outbound call and shows a live view as it happens — connecting → in progress (with timer) → ended (with outcome and summary)
- **Memory feed** showing conversation highlights, summaries, and facts stored after each call

---

## Testing with SIP (no PSTN number needed)

### What you need

A **SIP softphone** — an app that registers with a SIP server and rings like a phone when called. The easiest free option is [Linphone](https://www.linphone.org/).

### Setup with Linphone (5 minutes)

1. **Create a free SIP account** at [linphone.org](https://www.linphone.org/freesip) — you'll get a SIP address like `sip:yourname@sip.linphone.org`

2. **Install the Linphone app** on your phone or desktop:
   - iOS / Android: search "Linphone" in the app store
   - macOS / Windows / Linux: download from [linphone.org/downloads](https://www.linphone.org/downloads)

3. **Sign in** to the app with your Linphone credentials — you'll see a green "registered" indicator when it's connected

4. **Update your contact** in the dashboard (or in `seed_contacts.py`):
   - Set **Contact Method** to **SIP**
   - Set **SIP URI** to `sip:yourname@sip.linphone.org`

5. **Hit Call Now** — Vapi dials the SIP URI, Linphone rings on your device

### Alternative SIP providers

| Provider | Free tier | Notes |
|---|---|---|
| [Linphone](https://linphone.org/freesip) | Yes | Easiest, no config needed |
| [Zoiper](https://www.zoiper.com/) | Free tier | Good softphone app, needs a separate SIP account |
| [OnSIP](https://www.onsip.com/) | Free for developers | More features |

### Vapi SIP requirements

Vapi sends calls to SIP URIs in the format `sip:user@domain`. Make sure your contact's SIP URI is in this format — no `sips:` or custom ports needed for Linphone.

The `phone` field is still required by the Contact model (for validation). For SIP-only contacts, use a placeholder like `+10000000001` — it won't be dialled.

---

## Scheduler — automated calling

The server runs two background jobs via APScheduler as soon as it starts:

### Daily cron job

Fires once a day (default: **09:00 server local time**) and:
1. Loads all contacts from the database
2. Scores them using the call decision engine (time since last call, priority boost, preferred call window)
3. Calls the top-ranked contacts via Vapi

To change the hour, set `SCHEDULER_DAILY_HOUR` in your `.env`:

```bash
export SCHEDULER_DAILY_HOUR=10   # fire at 10:00 instead of 09:00
```

### 5-minute polling job

Runs every 5 minutes and calls any contacts whose `next_call_at` timestamp has passed. This is used for scheduled callbacks — if a contact asks to be called back at a specific time, it gets written to the database and this job picks it up.

### How contacts are scored

The scoring engine in `app/services/scoring.py` ranks contacts by:

- **Days since last spoken** — the longer the gap, the higher the score
- **Priority boost** — a manual multiplier you set per contact (0–10)
- **Call time preference** — contacts set to "morning" only score highly during morning hours in their timezone; "evening" contacts score at night; "any time" contacts are always eligible
- **In-progress calls** — contacts already on a call are skipped

The top-ranked contacts above a minimum threshold get called each day.

### Disabling the scheduler

The scheduler starts automatically with the server. If you want to manage calls manually only (via the Call Now button), you can comment out `start_scheduler()` in `app/main.py`.

---

## Project Structure

```
app/
  config.py           # Environment variable loading (pydantic-settings)
  main.py             # FastAPI app factory + lifespan startup
  db.py               # SQLite schema and helpers
  sse_bus.py          # In-process pub/sub for live call SSE events
  routes/
    contacts.py       # Contact CRUD + memories endpoint
    calls.py          # Call trigger, SSE stream, Vapi tool endpoints
    webhook.py        # End-of-call webhook handler
    dashboard.py      # Serves the SPA
  services/
    vapi.py           # Vapi outbound call initiation
    scoring.py        # Call decision scoring engine
    embedding.py      # Text embeddings (with SHA-256 fallback)
    qdrant.py         # Qdrant memory store
    calendar.py       # Google Calendar integration (stub)
    social/           # Social media adapters (Twitter, Instagram, LinkedIn)
  models/
    contact.py        # Contact Pydantic model
    memory.py         # MemoryEntry model
  workers/
    scheduler.py      # APScheduler periodic call jobs
  static/
    index.html        # Single-page application (Tailwind CSS, vanilla JS)
scripts/
  setup_vapi.py       # One-shot Vapi provisioning (tools + assistant)
tests/
  unit/               # Unit tests (35 passing)
```
