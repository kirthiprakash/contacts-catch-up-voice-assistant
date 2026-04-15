# 📞 Contact Catch-up Voice Assistant (POC Plan)

## 🎯 Goal
Build a voice assistant that:
- Regularly calls ~10 contacts
- Keeps relationships warm over time
- Uses memory + context to personalize conversations
- Avoids over-contacting the same people

---

## 🧠 Core Idea
Instead of calling everyone at once:
- System tracks *recency of interaction*
- Prioritizes contacts you haven’t spoken to recently
- Balances across categories:
  - personal
  - family
  - highschool
  - college
  - professional

---

## 🧱 System Components

### 1. Voice Layer
- Vapi agent
- Handles call + conversation
- Uses tools (API calls) to fetch/store data

### 2. Backend (FastAPI / Node)
Responsibilities:
- Contact management
- Scheduling calls
- Calendar integration (read/write)
- Social signal ingestion
- Memory orchestration

### 3. Memory (Qdrant)
Stores:
- Conversation summaries
- Preferences
- Social updates
- Last interaction timestamps

---

## 👤 Contact Model

```json
{
  "contact_id": "uuid",
  "name": "Rahul",
  "phone": "+91...",
  "tags": ["college"],
  "last_called": "timestamp",
  "last_spoken": "timestamp",
  "call_time_preference": "evening | morning | specific_time",
  "preferred_time_window": { "start": "18:00", "end": "21:00" },
  "next_call_at": "timestamp (optional)",
  "priority_boost": 0,
  "last_call_outcome": "answered | busy | no_answer",
  "last_call_note": "string",
  "social_handles": {
    "instagram": "rahul_xyz"
  }
}
```json
{
  "contact_id": "uuid",
  "name": "Rahul",
  "phone": "+91...",
  "tags": ["college"],
  "last_called": "timestamp",
  "last_spoken": "timestamp",
  "call_time_preference": "evening | morning | specific_time",
  "preferred_time_window": { "start": "18:00", "end": "21:00" },
  "next_call_at": "timestamp (optional)",
  "priority_boost": 0,
  "social_handles": {
    "instagram": "rahul_xyz"
  }
}
```json
{
  "contact_id": "uuid",
  "name": "Rahul",
  "phone": "+91...",
  "tags": ["college"],
  "last_called": "timestamp",
  "last_spoken": "timestamp",
  "social_handles": {
    "instagram": "rahul_xyz"
  }
}
```

---

## 🧠 Memory Model (Qdrant)

Each memory = vector + metadata

```json
{
  "contact_id": "uuid",
  "type": "preference | summary | social",
  "text": "Visited Andaman last week",
  "timestamp": "..."
}
```

---

## 🔁 Call Decision Engine

Runs using hybrid scheduling:

### Step 0: Immediate callbacks (highest priority)
- If `next_call_at <= now()` → pick immediately OR trigger scheduled job

### Step 1: Filter candidates
- Not called in last X days
- Not recently skipped

### Step 2: Category balancing
- Identify least-contacted category

### Step 3: Preference-aware filtering
- Only consider contacts within their preferred time window

### Step 4: Score
- Score = recency + category + priority_boost

### Step 5: Pick contact
- Pick top candidate

---

### 🧠 Handling "busy / call later"

When user says:
- "call me tomorrow evening"
- "call me in 1 hour"

Backend should:

1. Parse intent (LLM extraction)
2. Update contact:

```json
{
  "next_call_at": "computed timestamp",
  "priority_boost": 1,
  "last_call_outcome": "busy",
  "last_call_note": "asked to call later"
}
```

3. Schedule immediate job (APScheduler)

---

### ⏱️ Scheduler Design (UPDATED)

#### 1. Immediate jobs (APScheduler)

```python
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()
scheduler.start()

def schedule_call(contact, run_at):
    scheduler.add_job(
        func=trigger_call,
        trigger="date",
        run_date=run_at,
        args=[contact]
    )
```

Used for:
- "call in 1 hour"
- "tomorrow evening"

---

#### 2. Daily cron

- Runs once per day
- Picks next best contact using scoring

---

#### 3. Optional safety loop

- Runs every 10 mins
- Picks missed `next_call_at`

---

### 🧠 Handling "busy / call later"

When user says:
- "call me tomorrow evening"
- "call me in 1 hour"

Backend should:

1. Parse intent (LLM extraction)
2. Update contact:

```json
{
  "next_call_at": "computed timestamp",
  "priority_boost": 1
}
```

3. Scheduler gives this contact highest priority

---

### ⏱️ Short-term scheduler (NEW)

In addition to daily cron, run a loop every 5–10 minutes:

```python
if contact.next_call_at <= now():
    trigger_call(contact)
```

---

---

## 📅 Calendar Integration

Backend should:
- Read free slots
- Suggest meeting during call
- Book event if confirmed

Flow:
1. Get availability
2. Offer 2–3 slots
3. Confirm
4. Create calendar event

---

## 📞 Call Flow

1. Fetch contact context
2. Fetch memory (Qdrant)
3. Fetch social updates
4. Start call via Vapi

Conversation style:
- Friendly
- Short
- Context-aware

Example:
> "Hey Rahul, saw your Andaman trip — looked amazing! When are you free to catch up?"

---

## 📲 Social Signal Integration (POC choice)

### Recommended: Twitter/X (easy API)
Why:
- Easier API access vs Instagram
- Free tier possible
- Simple text-based signals

Track:
- Recent tweets
- Mentions of travel/events

Store as memory:
- "Posted about Andaman trip"

---

## 🔗 Social Mapping

During onboarding:
- Map contact → social handle

```json
{
  "contact_id": "uuid",
  "twitter": "@rahul"
}
```

---

## 🧾 Onboarding Flow

For each contact:
1. Add name + phone
2. Assign category/tag
3. Add social handle (optional)
4. Initialize memory

---

## 🛠️ Vapi Tools

### 1. get_contact_context
- Returns:
  - name
  - last interaction
  - category

### 2. get_memory
- Query Qdrant

### 3. save_memory
- Store summary after call

### 4. get_calendar_slots
- Returns free slots

### 5. create_calendar_event
- Books meeting

---

## 🧠 Conversation Intelligence

LLM should:
- Reference past context
- Mention social updates
- Ask for availability
- Confirm clearly

---

## 🔄 Post-call Processing

After each call:
- Generate summary
- Extract:
  - availability
  - preferences
  - notes
- Store in Qdrant
- Update contact.last_called

---

## ⚙️ Scheduling Strategy

- Max 1–2 calls per day
- Spread across week
- Retry if:
  - no answer
  - busy

---

## 🚀 MVP Scope

Keep it simple:
- 5–10 contacts
- Manual trigger OR daily cron
- Twitter integration only
- Basic memory (summaries)

---

## 🔁 Interaction Modes (Updated)

### 🎯 Mode 1: Voice (Primary)
- Agent calls human via Vapi
- Focus: relationship building
- Uses:
  - memory (Qdrant)
  - social context
  - past interactions

### ⚙️ Mode 2: Assist (Backend Automation)
- No voice
- Agent handles:
  - calendar negotiation
  - slot finalization
  - event creation

Flow:
1. Voice call captures intent
2. Backend agent completes scheduling via APIs

---

### 🔄 Mode 3: Fallback (Voice → Text)

Trigger when user says:
- "Text me"
- "Busy now"

System switches to:
- SMS / WhatsApp (future)

Conversation continues async:
- share slots
- confirm availability

---

### 👤 Mode 4: Human Assist (Optional)

Before call:
- System prepares briefing:
  - last interaction
  - social updates
  - suggested talking points

User can:
- call manually
- OR let agent proceed

---

### 🚫 Avoid: Agent ↔ Agent Conversations (Default)

Not recommended because:
- Removes human element
- Feels transactional
- Reduces emotional value

Allowed only if:
- Both parties opt-in
- Used strictly for scheduling (short interactions)

---

## 🔮 Future Enhancements

- WhatsApp integration
- Instagram scraping (if API restricted)
- Smart follow-ups
- Mood detection
- Relationship health scoring

---

## ⚠️ Considerations

- Consent for automated calls
- Avoid over-calling
- Keep tone natural
- Respect time zones

---

## ✅ Success Criteria

- Contacts feel conversations are personal
- No one is contacted too frequently
- System maintains long-term relationships automatically

---

## 🧠 Timezone & Memory Module (Added)

### 🌍 Timezone Handling

Store per contact:

```json
{
  "contact_id": "...",
  "timezone": "Asia/Kolkata"
}
```

Rules:
- Calls only between 9 AM – 8 PM (contact local time)
- Respect `preferred_time_window` if present
- Backend converts all times before sending to agent

---

### 🧠 Post-call Memory Extraction

After each call:
1. Receive transcript (from Vapi webhook)
2. Run extraction step (LLM)
3. Store structured output

Also extract scheduling signals:

```json
{
  "callback": {
    "type": "relative | absolute",
    "value": "in 1 hour | tomorrow 6pm"
  },
  "call_time_preference": "evening"
}
```

---

### 💾 Where to store what (IMPORTANT)

- Qdrant → semantic memory (highlights, facts)
- Contacts DB → operational fields:
  - next_call_at
  - call_time_preference
  - priority_boost

---

### 🔁 Retrieval Before Call

Before each call:
- Fetch top memories
- Provide as context to agent

Example usage:
> "Last time you mentioned your promotion — how’s that going?"

Before each call:
- Fetch top memories
- Provide as context to agent

Example usage:
> "Last time you mentioned your promotion — how’s that going?"

---

## 📡 Communication Modes (Telecom vs SIP)

### 🎯 Goal
Allow the agent to switch between:
- Real phone calls (PSTN)
- SIP-based calls (dev/testing)

---

### 👤 Contact Model (Extended)

```json
{
  "contact_id": "uuid",
  "name": "Rahul",
  "contact_method": "phone",  // or "sip"
  "phone": "+91...",
  "sip": "sip:rahul@domain.com",
  "timezone": "Asia/Kolkata"
}
```

---

### ☎️ Mode 1: Telecom (Default)

Flow:
- Vapi → PSTN → Phone

Use when:
- Calling real users
- Production usage

Pros:
- Real experience
- No setup for contacts

Cons:
- Costs per minute
- Spam perception risk

---

### 🔌 Mode 2: SIP (Dev/Test)

Flow:
- Vapi → SIP → Softphone (Zoiper/Linphone)

Use when:
- Testing flows
- Debugging conversations
- Avoiding telecom cost

Pros:
- Free/cheap
- Fast iteration

Cons:
- Not usable for real users

---

### ⚙️ Call Routing Logic

```python
if contact.contact_method == "phone":
    call_via_vapi_number(contact.phone)
elif contact.contact_method == "sip":
    call_via_sip(contact.sip)
```

---

### 🧠 Design Principle

- Default = phone
- SIP = optional testing layer
- Backend decides routing (not Vapi agent)

---

## 🤖 LLM Usage (Added)

### 🎯 Where LLM is used

1. Post-call summarization
2. Fact extraction (promotion, travel, preferences)
3. Follow-up generation

---

### 🔌 Config

Use OpenAI-compatible API (can point to Ollama):

```bash
OPENAI_API_KEY=...
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=llama3
```

---

### 🧠 Extraction Prompt (example)

Input: transcript
Output:

```json
{
  "summary": "...",
  "highlights": [],
  "facts": [],
  "followups": []
}
```

---

### ⚠️ Design

- LLM only used post-call (not in core scheduling logic)
- Backend remains mostly deterministic
- Keeps cost + complexity low

---

## 📊 Contact Scoring (Added)

### 🎯 Goal
Pick who to call next intelligently

---

### Inputs

- days_since_last_call
- category_frequency (how often category contacted)
- manual priority (optional)

---

### Example formula

```python
score = (
    days_since_last_call * 0.6
    + category_gap_score * 0.3
    + priority * 0.1
)
```

---

### Category balancing

- Track last interaction per category
- Prefer least recently contacted category

---

### Output

- Rank contacts
- Pick top 1–2 per day

---

### ❗ No LLM needed here

- Fully deterministic
- Fast + predictable

---

## 🏗️ Implementation Specification (Codex-ready)

### 📁 Project Structure

```
app/
  main.py
  config.py
  routes/
    contacts.py
    calls.py
    webhook.py
  services/
    vapi.py
    qdrant.py
    llm.py
    calendar.py
    scoring.py
  models/
    contact.py
    memory.py
  workers/
    scheduler.py
```

---

### ⚙️ Environment Variables

```bash
# Vapi
VAPI_API_KEY=
VAPI_ASSISTANT_ID=
VAPI_PHONE_NUMBER_ID=

# Qdrant
QDRANT_API_KEY=
QDRANT_ENDPOINT=

# LLM (Ollama/OpenAI compatible)
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=

# Google Calendar
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REFRESH_TOKEN=
```

---

### 🧱 Core APIs

#### 1. Start Call

```http
POST /calls/start
```

Logic:
- pick contact (scoring)
- fetch memory
- trigger Vapi call

```python
call_vapi(contact.phone)
```

---

#### 2. Vapi Webhook

```http
POST /webhook/vapi
```

Receives events from Vapi (call status, transcript etc.) ([docs.vapi.ai](https://docs.vapi.ai/server-url/events?utm_source=chatgpt.com))

Payload includes:
- transcript
- call metadata

Steps:
1. extract transcript
2. call LLM for summary
3. store in Qdrant
4. update contact

---

### ☎️ Vapi Call Trigger

```bash
POST https://api.vapi.ai/call
Authorization: Bearer $VAPI_API_KEY
```

```json
{
  "assistantId": "...",
  "phoneNumberId": "...",
  "customer": {
    "number": "+91..."
  }
}
```

Used to initiate outbound calls programmatically ([docs.vapi.ai](https://docs.vapi.ai/calls/outbound-calling?utm_source=chatgpt.com))

---

### 🧠 Qdrant Collection

Collection: `memories`

Vector size: depends on embedding model (e.g. 768 for nomic-embed-text)

Payload:

```json
{
  "contact_id": "...",
  "type": "summary | highlight | fact",
  "text": "Got promoted",
  "timestamp": "..."
}
```

---

### 🧠 Embedding Service (NEW)

```python
from openai import OpenAI
from app.config import settings

client = OpenAI(api_key=settings.OPENAI_API_KEY, base_url=settings.OPENAI_BASE_URL)

EMBED_MODEL = "nomic-embed-text"


def embed(text: str):
    res = client.embeddings.create(
        model=EMBED_MODEL,
        input=text
    )
    return res.data[0].embedding
```

---

### 🧠 Updated Qdrant Save (with embeddings)

```python
from app.services.embedding import embed

vector = embed(text)

points.append({
    "id": str(uuid.uuid4()),
    "vector": vector,
    "payload": {
        "contact_id": contact_id,
        "text": text,
        "type": "highlight"
    }
})
```

---

### 🔍 Memory Search (NEW)

```python
def search_memory(contact_id, query):
    query_vector = embed(query)

    results = client.search(
        collection_name=COLLECTION,
        query_vector=query_vector,
        limit=5,
        query_filter={
            "must": [
                {"key": "contact_id", "match": {"value": contact_id}}
            ]
        }
    )

    return [r.payload for r in results]
```

---

### 🔁 Memory Usage Before Call

```python
memories = search_memory(contact_id, "recent updates, life events, work")
```

Pass this into Vapi context.

---json
{
  "contact_id": "...",
  "type": "summary",
  "text": "Got promoted",
  "timestamp": "..."
}
```

---

### 🧠 LLM Service (Robust JSON + Retries)

```python
import json
from openai import OpenAI
from app.config import settings

client = OpenAI(api_key=settings.OPENAI_API_KEY, base_url=settings.OPENAI_BASE_URL)

SCHEMA = {
  "summary": "string",
  "highlights": ["string"],
  "facts": [{"type": "string", "value": "string"}],
  "followups": ["string"],
  "callback": {"type": "relative | absolute | none", "value": "string"},
  "call_time_preference": "morning | evening | specific_time | none"
}

PROMPT = """
Extract structured data from the transcript.
Return ONLY valid JSON. No explanations.
If unsure, use empty fields.

Schema:
- summary: short string
- highlights: array of key points
- facts: array of {type, value}
- followups: array of suggested follow-ups
- callback: {type: relative|absolute|none, value: string}
- call_time_preference: morning|evening|specific_time|none

Transcript:
{transcript}
"""


def safe_parse(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


def extract(transcript: str):
    for _ in range(2):
        res = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[{"role": "user", "content": PROMPT.format(transcript=transcript)}],
            response_format={"type": "json_object"}
        )
        content = res.choices[0].message.content
        parsed = safe_parse(content)
        if parsed:
            return parsed

    # fallback
    return {
        "summary": "",
        "highlights": [],
        "facts": [],
        "followups": [],
        "callback": {"type": "none", "value": ""},
        "call_time_preference": "none"
    }
```

---python
summarize(transcript) -> {
  summary,
  highlights,
  facts,
  followups
}
```

---

### 📊 Scoring Service

```python
def score(contact):
    return days_since_last_call * 0.6 + category_gap * 0.3
```

---

### 📅 Calendar Service

Functions:

```python
get_free_slots()
create_event(start, end, contact)
```

---

### 🔁 Scheduler

Runs daily:

```python
contacts = get_contacts()
sorted = rank(contacts)
pick top 1
trigger call
```

---

### 🧪 Local Dev Setup

- use ngrok for webhook
- run:

```bash
vapi listen --forward-to localhost:8000/webhook/vapi
```

Requires public URL exposure for Vapi to send events ([docs.vapi.ai](https://docs.vapi.ai/cli/webhook?utm_source=chatgpt.com))

---

### 🛠️ Vapi Tool Examples

```json
{
  "name": "get_memory",
  "description": "Fetch relevant past memories for a contact",
  "parameters": {
    "type": "object",
    "properties": {
      "contact_id": { "type": "string" }
    },
    "required": ["contact_id"]
  }
}
```

```json
{
  "name": "get_calendar_slots",
  "description": "Fetch available time slots",
  "parameters": {
    "type": "object",
    "properties": {}
  }
}
```

```json
{
  "name": "create_calendar_event",
  "description": "Create meeting",
  "parameters": {
    "type": "object",
    "properties": {
      "start": {"type": "string"},
      "end": {"type": "string"}
    },
    "required": ["start", "end"]
  }
}
```

---json
{
  "name": "get_memory",
  "description": "Fetch memory",
  "parameters": {
    "type": "object",
    "properties": {
      "contact_id": { "type": "string" }
    }
  }
}
```

---

### 🗣️ Vapi Assistant Prompt (v1)

System Prompt:

```
You are a friendly assistant calling to catch up.

Goals:
- Be warm, short, natural
- Reference past context if available
- Ask about availability for a meetup

Rules:
- Keep responses under 2 sentences
- If user mentions life updates (job, travel), acknowledge it
- If user is busy, ask for preferred callback time
- If user gives a time, confirm it clearly

Examples:
- "Hey Rahul! Last time you mentioned your promotion — how’s that going?"
- "When would be a good time to catch up this week?"
- "Got it, tomorrow evening works — I’ll reach out then."
```

---

## 🧠 Memory Injection Prompt (v1)

Before call, backend should prepare context:

```
Recent context about this person:
- Got promoted recently
- Planning a trip

Use this naturally in conversation.
```

---

## 🔁 End-to-End Flow

1. Scheduler picks contact
2. Backend calls Vapi API
3. Call happens
4. Vapi sends webhook
5. Backend processes transcript
6. LLM extracts insights
7. Store in Qdrant
8. Next call uses memory

---

## 🧪 Detailed Code Stubs (Copy‑paste ready)

### app/services/embedding.py
```python
from openai import OpenAI
from app.config import settings

client = OpenAI(api_key=settings.OPENAI_API_KEY, base_url=settings.OPENAI_BASE_URL)

EMBED_MODEL = "nomic-embed-text"


def embed(text: str):
    res = client.embeddings.create(
        model=EMBED_MODEL,
        input=text
    )
    return res.data[0].embedding
```

---

### app/services/qdrant.py
```python
from qdrant_client import QdrantClient
from app.config import settings
import uuid

client = QdrantClient(url=settings.QDRANT_ENDPOINT, api_key=settings.QDRANT_API_KEY)

COLLECTION = "memories"


def init_collection():
    client.recreate_collection(
        collection_name=COLLECTION,
        vectors_config={"size": 768, "distance": "Cosine"},
    )


def save_memory(contact_id, data):
    from app.services.embedding import embed

    points = []

    for text in data.get("highlights", []):
        vector = embed(text)
        points.append({
            "id": str(uuid.uuid4()),
            "vector": vector,
            "payload": {
                "contact_id": contact_id,
                "text": text,
                "type": "highlight"
            }
        })

    if points:
        client.upsert(collection_name=COLLECTION, points=points)
```

---

### app/services/llm.py
```python
from openai import OpenAI
from app.config import settings

client = OpenAI(api_key=settings.OPENAI_API_KEY, base_url=settings.OPENAI_BASE_URL)


def extract(transcript: str):
    prompt = f"""
    Extract structured data from conversation.

    Transcript:
    {transcript}

    Return JSON with keys: summary, highlights, facts, followups
    """

    res = client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}]
    )

    # naive parse (improve later)
    return {"highlights": [res.choices[0].message.content]}
```

---

### app/services/scoring.py
```python
from datetime import datetime

CONTACTS = []  # replace with DB later


def score(c):
    days = (datetime.utcnow() - c["last_called"]).days
    return days


def pick_contact():
    return sorted(CONTACTS, key=score, reverse=True)[0]
```

---

---

## 🌐 Web Dashboard (NEW)

### 🎯 Goal
- View + manage contacts
- Onboard new contacts
- See call history + highlights

---

### ➕ Onboarding UI (NEW)

Fields:
- name
- phone
- category
- timezone
- preferred call time
- social handle

---

### Backend APIs

#### Create Contact
```http
POST /contacts
```

#### Update Contact
```http
PATCH /contacts/{id}
```

---

### Dashboard Views

#### Contact List
- Name
- Category
- Last called
- Next call scheduled

#### Contact Detail
- Highlights
- Facts
- Notes
- Call timeline

---

### Example UI

```
Rahul
Last called: 2 days ago
Next call: Tomorrow 6 PM

Highlights:
- Got promoted
- Planning trip
```

---

### Future UI Enhancements

- search contacts
- filter by category
- timeline view of interactions
- manual trigger call button


- search contacts
- filter by category
- timeline view of interactions

---

## 🐳 Docker (Optional but useful)

### docker-compose.yml
```yaml
version: "3.8"
services:
  app:
    build: .
    ports:
      - "8000:8000"
    env_file: .env
```

---

## 🧪 Qdrant Init Script

```python
from app.services.qdrant import init_collection

if __name__ == "__main__":
    init_collection()
```

---

## 🔍 Notes for Coding Assistants

- Start with webhook → easiest to validate
- Hardcode contacts first
- Skip embeddings initially (use dummy vectors)
- Add embedding later (OpenAI or local model)
- Keep everything synchronous for MVP

---

## 📞 Call Outcome Classification (NEW)

### 🎯 Goal
Automatically determine outcome of each call and update contact state.

---

### 📥 Source
From Vapi webhook payload:
- call status (completed, failed, no-answer)
- transcript

---

### 🧠 Outcome Extraction

Use simple rules + LLM fallback:

```python
def classify_outcome(status, transcript):
    if status in ["no-answer", "failed"]:
        return "no_answer", "no response"

    text = transcript.lower()

    if "busy" in text or "call later" in text:
        return "busy", "asked to call later"

    return "answered", "normal conversation"
```

---

### 🧠 LLM-assisted refinement (optional)

```python
prompt = f"""
Classify call outcome:

Transcript:
{transcript}

Return JSON:
{{
  "outcome": "answered | busy | no_answer",
  "note": "short reason"
}}
"""
```

---

### 🔄 Webhook Update Flow

```python
outcome, note = classify_outcome(status, transcript)

update_contact(contact_id, {
    "last_call_outcome": outcome,
    "last_call_note": note,
    "last_called": now()
})
```

---

### 🔁 Retry Strategy (NEW)

```python
if outcome == "no_answer":
    next_call = now() + timedelta(days=2)
elif outcome == "busy":
    # already handled via extracted callback
    pass
```

---

### 📊 Dashboard Usage

Show:
- last outcome
- last note

Example:

```
Rahul
Last call: busy
Note: asked to call tomorrow evening
```

---

## 🧩 Next Steps

1. Run FastAPI (`uvicorn app.main:app --reload`)
2. Expose webhook (ngrok)
3. Configure Vapi webhook URL
4. Trigger `/calls/start`
5. Observe webhook logs
6. Add real LLM parsing
7. Add embeddings

---

This is now fully executable for a POC.

