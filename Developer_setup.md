# Ask TechBear — Developer Setup

_Gymnarcots Studios LLC_
_Last updated: 2026-06-23_

This document covers everything needed to get a local development environment
running from scratch. Follow steps in order.

---

## Prerequisites

- macOS with Apple Silicon (M1/M2/M3) — primary development target
- [Homebrew](https://brew.sh) installed
- [Ollama](https://ollama.ai) installed and running
- [Postgres.app](https://postgresapp.com) or equivalent PostgreSQL installation
- Node.js 18+ (for frontend)
- Python 3.11+

---

## 1. Clone the repo

```bash
git clone https://github.com/NekkidBear/Ask-Techbear.git
cd ask-techbear
```

---

## 2. Wire up pre-commit hooks

The repo uses versioned pre-commit hooks tracked in `.githooks/`.
This is a one-time setup step — do it before making any commits.

```bash
chmod +x .githooks/*
ln -sf ../../.githooks/pre-commit .git/hooks/pre-commit
```

Verify both hooks run cleanly:

```bash
bash .githooks/pre-commit-v2.0-integrity
bash .githooks/pre-commit-v2.5-pipeline
```

Both should report ✅ on a clean checkout.

**Hook architecture:**

- `.git/hooks/pre-commit` — dispatcher (not tracked by git)
- `.githooks/pre-commit-v2.0-integrity` — v2.0 architectural constraints
- `.githooks/pre-commit-v2.5-pipeline` — v2.5 async pipeline constraints

When new branches introduce new constraints, new hook files are added
to `.githooks/` and wired into the dispatcher.

---

## 3. Python environment

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt --break-system-packages
```

Verify:

```bash
python --version        # should be 3.11+
pip show chromadb       # confirm RAG dependencies installed
pip show fastapi        # confirm backend dependencies installed
```

---

## 4. Environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```bash
DATABASE_URL=postgresql://localhost/ask_techbear
OLLAMA_HOST=localhost
ANTHROPIC_API_KEY=           # required for judge/scoring (Claude API)
```

Never commit `.env` — it's in `.gitignore`.

---

## 5. Database setup

Ensure Postgres is running, then:

```bash
createdb ask_techbear
```

The FastAPI backend runs migrations on startup via SQLAlchemy `create_all()`.
Start the backend once (step 8) to initialize the schema.

---

## 6. Ollama models

Pull all required models:

```bash
# Inference models
ollama pull llama3.1:8b
ollama pull qwen2.5:7b
ollama pull mistral:latest
ollama pull llama3.2:latest

# Embedding model (required for RAG)
ollama pull nomic-embed-text
```

Verify all are present:

```bash
ollama list
```

Expected models: `llama3.1:8b`, `qwen2.5:7b`, `mistral:latest`,
`llama3.2:latest`, `nomic-embed-text`.

---

## 7. Corpus ingestion

ChromaDB requires the corpus to be ingested before any RAG features work.
On a fresh environment or after resetting the database:

```bash
cd backend
source venv/bin/activate
python -m backend.scripts.ingest_corpus
```

Verify collections are populated:

```bash
python -m backend.scripts.environment_health
```

Expected output:

```text
✅ Ollama is up.
✅ Model present: llama3.1:8b
✅ Model present: nomic-embed-text
✅ techbear_facts: N documents
✅ techbear_voice: N documents
✅ Character file loaded (N words).
✅ All checks passed. Ready to benchmark.
```

**Corpus seeding policy:**

- Facts corpus: reingest when major technology shifts warrant it,
  or semi-annually as a floor
- Voice corpus: append after every tabling event or new column installment
- Full rebuild: required before cutting an RC

---

## 8. Start the full dev stack

From the project root:

```bash
./AskTB.sh
```

This starts (in order):

1. Postgres check
2. Ollama check + model presence verification
3. FastAPI backend (port 8000)
4. Vite frontend (port 3000)

Optional flags:

```bash
./AskTB.sh --seed    # also seed the blocklist on startup
```

Endpoints when running:

- Backend API: <http://localhost:8000>
- API docs: <http://localhost:8000/docs>
- Submit form: <http://localhost:3000/submit>
- Dashboard: <http://localhost:3000/dashboard>
- Slideshow: <http://localhost:3000/slideshow>

---

## 9. Run benchmarks (optional)

With the full stack running and corpus ingested:

```bash
python -m backend.scripts.benchmarking.benchmark_models --limit 5
```

Flags:

```bash
--limit N          # run only N questions (useful for quick smoke tests)
--models llama3.1:8b   # run specific model only
--modes raw prompt_only    # run specific modes only
--skip-health      # bypass environment health check (not recommended)
```

Results land in `benchmark_results/` as CSVs.

---

## 10. Cloudflare tunnel (optional, events only)

The public submission URL runs through a Cloudflare tunnel.
Only needed for live events or external sharing.

```bash
cloudflared tunnel run ask-techbear
```

Keep tunnel version current:

```bash
brew upgrade cloudflared
```

Check version before every event:

```bash
cloudflared --version
```

---

## Character files

Character context is split by pipeline phase — never feed `character_full.md`
to a pipeline phase directly. Use the appropriate split file:

| Phase              | Character file(s)                                  |
| ------------------ | -------------------------------------------------- |
| Moderation         | `character_moderation.md`                          |
| Factual pass       | `character_facts.md`                               |
| Fact critique      | `character_facts.md`                               |
| Voice pass         | `character_identity.md` + `character_voice.md`     |
| Character critique | `character_voice.md`                               |
| Editorial pass     | `character_identity.md` + `character_editorial.md` |
| Editorial critique | `character_editorial.md`                           |

`character_full.md` is retained as reference. The v2.5 pre-commit hook
will block any pipeline phase that imports it directly.

---

## Key architectural constraints

Enforced by pre-commit hooks — commits will be blocked if violated:

**v2.0 constraints:**

- `pipeline.py` must not contain `TECHBEAR_SYSTEM` prompt
- Character must be loaded via `character_loader.py` only
- Benchmark must use injected `prompt_builder` pattern
- No direct Ollama calls in benchmark layer
- No zip files in repo

**v2.5 constraints:**

- Pipeline phases must not import each other directly
- Voice pass must not query `techbear_facts` collection
- Factual pass must not query `techbear_voice` collection
- No pipeline phase may import `character_full.md`
- `handoff.py` must write scores to JSON
- Critique modules must not make direct Ollama calls
- Only `orchestrator.py` may import multiple pipeline phases

---

## Branch structure

```text
main
└── feature/v2.0-main
    ├── feature/v2.0-model-scoring  (merged)
    └── feature/v2.5-async-pipeline (active)
```

Always branch from `feature/v2.0-main`, not from `main`.
PRs target `feature/v2.0-main`, not `main`.

---

## Common issues

**ChromaDB collections empty after fresh clone:**
Run `python -m backend.scripts.ingest_corpus` — the vector store
is not committed to the repo (`.gitignore` excludes `chroma_db/`).

**Pre-commit hook not running:**
Re-run the one-time setup:

```bash
chmod +x .githooks/*
ln -sf ../../.githooks/pre-commit .git/hooks/pre-commit
```

**Ollama not responding:**
Open Ollama.app or run `ollama serve` in a separate terminal,
then re-run `./AskTB.sh`.

**`nomic-embed-text` missing:**

```bash
ollama pull nomic-embed-text
```

Required for all RAG features. The environment health check will
flag this explicitly.

**Backend port 8000 already in use:**

```bash
lsof -i :8000
kill -9 <PID>
```

---

_See `ROADMAP.md` and `ROADMAP-v2-addendum.md` for architecture decisions
and build order rationale._

Current ORM models use classic SQLAlchemy Column(...) declarations.

Pyright/Pylance may report:

- reportAttributeAccessIssue
- reportArgumentType
- reportGeneralTypeIssues

when interacting with ORM instance fields.

These are expected and do not currently indicate runtime failures.

Planned resolution:
Future migration to SQLAlchemy 2.0 typed models
using Mapped[] and mapped_column().
