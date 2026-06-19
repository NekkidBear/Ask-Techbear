# Ask TechBear — System Design Document
*Version 1.0 | Gymnarctos Studios LLC*

---

## Overview

**Ask TechBear** is a locally-hosted, RAG-powered interactive Q&A performance tool for live tabling events and presentations. Attendees submit questions via a public-facing chat UI; an LLM drafts character-accurate responses in TechBear's voice; a human operator (Jason) performs those responses live. Between shows, the system runs a slideshow of highlighted Q&As.

The LLM is a **writer's assistant and drafting tool**, not a direct-to-public chatbot. Jason is always in the loop.

---

## System Modes

| Mode | Description | Who sees it |
|---|---|---|
| **Submission** | Attendee submits name + question via form (QR → URL) | Attendees on their phones |
| **Live Show** | Moderator dashboard — queue, LLM draft, show controls | Jason (operator) on laptop |
| **Slideshow** | Auto-scrolling highlights reel of best Q&As | Big screen / projector, between shows |

---

## Architecture

```
[ Attendee Phone ]
        │  HTTP POST (name, question)
        ▼
[ React Frontend ]  ←──── Submission Form (Messenger-style UI)
        │
        ▼
[ FastAPI Backend ]
    ├── Queue Manager (Postgres)
    ├── Moderation Filter (blocklist + topic rules)
    ├── RAG Engine (LangChain + ChromaDB)
    │       └── TechBear Blog Corpus (embedded)
    ├── Ollama (local LLM)
    │       └── TechBear Character File (system prompt)
    └── Session Context (rolling window, last N Q&As)
        │
        ▼
[ Moderator Dashboard ]  ←── Jason's view: queue + draft + controls
        │
        ▼
[ Slideshow Mode ]  ←── Highlighted Q&As, full-screen display
```

---

## Tech Stack

### Local LLM
- **Ollama** — runs locally, Apple Silicon optimized, uses Metal GPU acceleration
- **Recommended models** (pick based on your RAM):
  - 16GB RAM → `llama3.2:latest` (8B, fast, good quality)
  - 32GB RAM → `gemma3:12b` or `llama3.1:latest` (strong quality, worth the wait)
  - 64GB RAM → `llama3.3:70b-instruct-q4_K_M` (excellent, near-API quality)
- Install: `brew install ollama && ollama pull llama3.2`

### RAG Layer
- **ChromaDB** — local vector database, zero config, persists to disk
- **LangChain** — orchestrates chunking, embedding, retrieval pipeline
- **Embedding model** — `nomic-embed-text` via Ollama (free, local, fast)
- **Corpus** — TechBear blog articles chunked at ~500 tokens with 50-token overlap

### Backend
- **FastAPI** (Python) — async, fast, WebSocket support for live dashboard updates
- **SQLAlchemy** + **asyncpg** — ORM for Postgres
- **WebSockets** — real-time push to moderator dashboard (no polling)

### Database
- **PostgreSQL** (local instance)
- Manages question queue, session history, highlight flags, moderation log

### Frontend
- **React** (Vite) — single app, three views
- **Tailwind CSS** — utility styling, mobile-friendly submission form
- **WebSocket client** — dashboard receives live updates

### Networking (QR Code / Public URL)
- **Cloudflare Tunnel** — maps `ask.gymnarctosstudios.com` → `localhost:3000`
- Free, permanent subdomain, survives NAT, no router config needed
- Install: `brew install cloudflared`
- One-time setup: `cloudflared tunnel login && cloudflared tunnel create ask-techbear`

---

## Database Schema

```sql
-- Questions submitted by attendees
CREATE TABLE questions (
    id              SERIAL PRIMARY KEY,
    session_id      UUID NOT NULL,
    attendee_name   VARCHAR(100) NOT NULL,
    question_text   TEXT NOT NULL,
    submitted_at    TIMESTAMPTZ DEFAULT NOW(),
    status          VARCHAR(20) DEFAULT 'pending',
    -- 'pending' | 'approved' | 'rejected' | 'answered' | 'highlighted'
    moderation_flag VARCHAR(50),   -- reason if rejected
    llm_draft       TEXT,          -- TechBear's generated response
    draft_generated_at TIMESTAMPTZ,
    answered_at     TIMESTAMPTZ,
    highlight       BOOLEAN DEFAULT FALSE,
    display_order   INTEGER        -- for slideshow ordering
);

-- Session tracking (one per tabling event)
CREATE TABLE sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_name      VARCHAR(200),
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    active          BOOLEAN DEFAULT TRUE
);

-- Rolling context: answered Q&As for this session (for LLM context window)
CREATE TABLE session_context (
    id              SERIAL PRIMARY KEY,
    session_id      UUID NOT NULL,
    question_text   TEXT NOT NULL,
    response_text   TEXT NOT NULL,  -- the PERFORMED response (may differ from draft)
    answered_at     TIMESTAMPTZ DEFAULT NOW()
);

-- Moderation blocklist (editable at runtime)
CREATE TABLE blocklist (
    id              SERIAL PRIMARY KEY,
    term            VARCHAR(200) NOT NULL,
    category        VARCHAR(50),   -- 'profanity' | 'competitor' | 'topic' | 'custom'
    added_at        TIMESTAMPTZ DEFAULT NOW()
);
```

---

## TechBear Character File

The character file is a markdown system prompt prepended to every LLM call. Draft:

```markdown
# TechBear — Character System Prompt

You are TechBear, the sassy, warmhearted IT expert alter ego of Jason at Gymnarctos Studios.

## Voice & Personality
- You are a Southern-inflected queen with decades of IT experience and zero tolerance for foolishness
- Endearments: "honey," "sugar," "sweetheart," "darling," "precious," "sweetie"
- You are LOUD in your opinions and use ALL CAPS for emphasis on key words
- You are genuinely helpful beneath the sass — the snark is the wrapper, not the filling
- You have a gift for unexpected metaphors (dust bunnies holding elections, Mercury retrograde affecting exes not cybersecurity)
- You reference your experience casually: "I've seen inside computers that would make you weep"
- You may reference Gymnarctos Studios and your services, but never pushy
- You end responses with warmth, not dismissal

## Format for Live Responses
- Open with a reaction (the "Oh sweetie" beat)
- Give the actual useful answer in the middle
- Close with a memorable one-liner or callback
- Keep it punchy — you're performing this live, not writing an essay
- 150–250 words is the sweet spot for a live read

## Forbidden Topics
- Competitors by name (redirect to general principles)
- Political opinions
- Medical advice (redirect to professionals)
- Anything about Philip or Jason's personal life
- Pricing (redirect: "Call us, sugar, we'll make it work")
- Any topic flagged in the moderation system

## What You Know
You have access to TechBear's blog articles on: device maintenance, cybersecurity, WiFi networking,
small business IT, backup strategies, and general tech accessibility. Draw from this knowledge.
If you don't know something, say so with flair: "Honey, that's outside my honey pot — 
but here's what I CAN tell you..."

## Session Context
{ROLLING_CONTEXT}

## Relevant Knowledge (from TechBear's articles)
{RAG_CONTEXT}
```

---

## RAG Pipeline

### Corpus Ingestion (one-time setup script)
```
1. Load all TechBear blog articles (markdown or HTML → strip to text)
2. Chunk at 500 tokens, 50-token overlap, with metadata: {source_url, article_title, date}
3. Embed with nomic-embed-text via Ollama
4. Store in ChromaDB collection: "techbear_corpus"
```

### At Query Time
```
1. Embed the attendee's question
2. Retrieve top 3–5 chunks from ChromaDB by cosine similarity
3. Inject as {RAG_CONTEXT} into character file
4. Inject last 5 answered Q&As as {ROLLING_CONTEXT}
5. Send full prompt to Ollama
6. Stream response to moderator dashboard
```

---

## Moderation Layer

Two-stage filter, runs before the question hits the LLM queue:

**Stage 1 — Blocklist (instant, synchronous)**
- Check `question_text` against `blocklist` table
- Fuzzy match (not just exact) — use `rapidfuzz` library
- If flagged: auto-reject, log reason, never enters queue
- Operator can review rejected questions on dashboard

**Stage 2 — Topic filter (LLM-assisted, async)**
- Small, fast Ollama call: "Is this question appropriate for a family-friendly tech event? Answer YES or NO and one reason."
- Use a tiny fast model for this (e.g., `llama3.2:1b`) — speed matters
- If NO: flag for operator review, hold from queue
- Operator has final approve/reject on flagged items

**Operator always has override.** The moderation system is a first pass, not a wall.

---

## Frontend: Three Views

### View 1 — Submission Form (Public / Attendee-Facing)
- Messenger-style aesthetic — dark theme, TechBear avatar as "contact"
- Fields: **Your Name** + **Your Question** (textarea, 500 char max)
- Submit button: "Ask TechBear!"
- After submit: confirmation screen — "TechBear has received your question, sugar. 🐻"
- No queue position shown (avoids gaming)
- Accessible: large tap targets, high contrast, screen-reader friendly
- QR code resolves here via Cloudflare Tunnel

### View 2 — Moderator Dashboard (Jason's screen, localhost only)
- Protected — only accessible on local network (not exposed via tunnel)
- Columns:
  - **Queue** — pending questions, oldest first; click to select
  - **Selected Question** — full text, attendee name
  - **LLM Draft** — TechBear's generated response, streaming in live
  - **Controls** — Approve / Reject / Highlight / Mark Answered
- "Now Performing" indicator — shows which question is live
- Hotkeys for speed during show
- Session stats: questions received, answered, highlighted
- Moderation review panel: flagged items needing operator decision

### View 3 — Slideshow (Display / Projector)
- Full-screen, dark background, TechBear branding
- Auto-cycles through `highlight = TRUE` questions
- Shows: **[Name] asked:** question text → **TechBear said:** response text
- Configurable display duration per card (default: 15 seconds)
- Smooth cross-fade transitions
- Can be triggered from dashboard ("Go to slideshow mode")
- Falls back to a branded holding screen when no highlights exist yet

---

## API Endpoints (FastAPI)

```
POST   /api/questions          — Submit a question (public)
GET    /api/questions          — List queue (dashboard only)
PATCH  /api/questions/{id}     — Update status/highlight (dashboard only)
POST   /api/questions/{id}/generate  — Trigger LLM draft generation
GET    /api/slideshow          — Get highlighted Q&As for display
POST   /api/sessions           — Start a new session
PATCH  /api/sessions/{id}      — End/update session
GET    /api/sessions/{id}/context — Get rolling context for LLM

WS     /ws/dashboard           — Real-time push to moderator dashboard
```

---

## Project Structure

```
ask-techbear/
├── backend/
│   ├── main.py                  # FastAPI app entry point
│   ├── models.py                # SQLAlchemy models
│   ├── database.py              # Postgres connection
│   ├── routers/
│   │   ├── questions.py
│   │   ├── sessions.py
│   │   └── slideshow.py
│   ├── services/
│   │   ├── moderation.py        # Blocklist + LLM filter
│   │   ├── rag.py               # ChromaDB retrieval
│   │   ├── llm.py               # Ollama client + prompt assembly
│   │   └── context.py           # Rolling session context manager
│   ├── character_file.md        # TechBear system prompt (editable)
│   ├── corpus/                  # Raw blog articles for ingestion
│   └── scripts/
│       ├── ingest_corpus.py     # One-time RAG setup
│       └── seed_blocklist.py    # Initial moderation terms
├── frontend/
│   ├── src/
│   │   ├── views/
│   │   │   ├── Submission.jsx   # Attendee-facing form
│   │   │   ├── Dashboard.jsx    # Moderator view
│   │   │   └── Slideshow.jsx    # Display/projector view
│   │   ├── components/
│   │   │   ├── QuestionCard.jsx
│   │   │   ├── DraftPanel.jsx
│   │   │   └── TechBearAvatar.jsx
│   │   └── App.jsx              # Route: /submit /dashboard /slideshow
│   └── vite.config.js
├── docker-compose.yml           # Postgres + ChromaDB (optional)
├── .cloudflared/
│   └── config.yml               # Tunnel config
└── README.md
```

---

## Local Development Setup

```bash
# 1. Install Ollama and pull models
brew install ollama
ollama pull llama3.2          # Main response model
ollama pull llama3.2:1b       # Fast moderation model
ollama pull nomic-embed-text  # Embedding model

# 2. Start Postgres (if not already running)
brew install postgresql@16
brew services start postgresql@16
createdb ask_techbear

# 3. Python backend
cd backend
python -m venv venv && source venv/bin/activate
pip install fastapi uvicorn sqlalchemy asyncpg langchain chromadb rapidfuzz

# 4. Ingest corpus
python scripts/ingest_corpus.py --corpus-dir ./corpus

# 5. Start backend
uvicorn main:app --reload --port 8000

# 6. React frontend
cd frontend
npm install
npm run dev   # localhost:3000

# 7. Cloudflare Tunnel (for event day)
cloudflared tunnel run ask-techbear
# Attendees hit: https://ask.gymnarctosstudios.com → localhost:3000/submit
```

---

## Event Day Checklist

- [ ] Ollama running, models loaded (`ollama list`)
- [ ] Postgres running, session created via dashboard
- [ ] Corpus up to date (re-run ingest if new articles added)
- [ ] Cloudflare tunnel active, QR code generated
- [ ] TechBear profile image loaded in submission UI
- [ ] Dashboard open on laptop, slideshow ready on external display
- [ ] Blocklist reviewed for event-specific terms
- [ ] Test submission end-to-end before doors open

---

## Phase 2 Ideas (Post-MVP)

- **Voice output** — TTS via `kokoro` or `coqui` for draft read-aloud in earpiece
- **Pre-scripted responses** — flag certain question patterns to auto-surface a canned answer
- **Corpus auto-update** — webhook from WordPress to re-ingest on new blog post
- **Analytics dashboard** — most common topics, question volume over time
- **Multi-event archive** — searchable history across tabling events
- **Philip co-host mode** — second operator view with different permission level

---

*Gymnarctos Studios LLC | Internal Technical Document*
*TechBear is the alter ego of Jason, CEO/CTO/Chief Everything Officer*
