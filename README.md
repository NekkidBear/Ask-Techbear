# Ask TechBear

A locally-hosted, RAG-powered interactive Q&A performance tool for live tabling
events and presentations, built for Gymnarctos Studios LLC.

Attendees submit questions via a public-facing chat-style form. TechBear (a
local LLM in character) drafts a response. The operator reviews and performs
the response live. Between shows, a slideshow rotates through highlighted
Q&As on the display.

## Architecture

- **Frontend**: React (Vite), Tailwind CSS — three views: submission form,
  moderator dashboard, slideshow
- **Backend**: FastAPI (Python), async SQLAlchemy + PostgreSQL
- **LLM**: Ollama (local), llama3.2 for drafting, llama3.2:1b for fast
  moderation checks
- **RAG**: ChromaDB + nomic-embed-text (planned)
- **Tunnel**: Cloudflare Tunnel — `ask.gymnarctosstudiosllc.com` → localhost:3000

See `ask-techbear-system-design.md` for the full original design doc and
`ROADMAP.md` for planned features and v2 ideas.

## Local Development Setup

### Prerequisites

- Python 3.12 (not 3.14 — see note below)
- Node.js (any recent version)
- PostgreSQL 16+ (Postgres.app on macOS)
- Ollama

### Backend

```bash
cd backend
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create `.env` in the project root (see `.env.example`):

```text
DATABASE_URL=postgresql+asyncpg://localhost/ask_techbear
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
CLOUDFLARE_TUNNEL_TOKEN=your_token_here
```

Start Postgres and create the database:

```bash
createdb ask_techbear
```

Pull the Ollama models:

```bash
ollama pull llama3.2
ollama pull llama3.2:1b
ollama pull nomic-embed-text
```

Run the backend:

```bash
uvicorn backend.main:app --reload --port 8000
```

API docs available at `http://localhost:8000/docs`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Runs on `http://localhost:3000`.

- Submission form: `/submit`
- Moderator dashboard: `/dashboard` (localhost only — never exposed via tunnel)
- Slideshow display: `/slideshow`

### Cloudflare Tunnel (event day)

```bash
cloudflared tunnel run --token $CLOUDFLARE_TUNNEL_TOKEN <tunnel-id>
```

Public submission URL resolves via Namecheap DNS CNAME →
`ask.gymnarctosstudiosllc.com`.

## Important Notes

**Python version**: Use Python 3.12 for the backend venv, not 3.14. As of
this writing, `greenlet` (a SQLAlchemy async dependency) doesn't yet support
3.14. If `python3 --version` shows 3.14, explicitly use
`/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12` when
creating the venv.

**Security**: The moderator dashboard (`/dashboard`) must never be exposed
through the Cloudflare tunnel — only `/submit` should be publicly routable.
Double-check the tunnel's ingress rules before any live event.

**Moderation**: Two-stage system — a fast blocklist (fuzzy match) runs
synchronously on submission; an LLM-based topic filter (using the small
`llama3.2:1b` model) is intended as a secondary async pass. The operator
always has final override on the dashboard.

## Project Structure

```text
ask-techbear/
├── backend/
│   ├── main.py              # FastAPI app entry point
│   ├── models.py            # SQLAlchemy models
│   ├── database.py          # Async Postgres connection
│   ├── character_file.md    # TechBear's voice/personality system prompt
│   ├── routers/
│   │   └── questions.py     # Question submission, queue, draft generation
│   ├── services/
│   │   ├── llm.py           # Ollama integration
│   │   └── moderation.py    # Blocklist + topic filter
│   └── scripts/              # One-time setup scripts (corpus ingestion, etc.)
├── frontend/
│   └── src/
│       ├── views/
│       │   ├── Submission.jsx
│       │   ├── Dashboard.jsx
│       │   └── Slideshow.jsx
│       └── App.jsx
├── ROADMAP.md
└── ask-techbear-system-design.md
```

## License

Internal project — Gymnarctos Studios LLC. Not licensed for external use.
