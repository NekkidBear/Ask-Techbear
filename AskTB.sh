#!/usr/bin/env bash
# AskTB.sh — One-command local dev startup for Ask TechBear
# Gymnarctos Studios LLC
#
# Starts (in order): Postgres check -> Ollama check -> FastAPI backend -> Vite frontend
# Ctrl+C will clean up all background processes.
#
# Place this at the project root (same level as backend/ and frontend/).
# Make it executable once: chmod +x AskTB.sh
# Then just run: ./AskTB.sh

set -uo pipefail

# ── Config — adjust if your paths differ ──────────────────────
VENV_PATH="backend/venv/bin/activate"
BACKEND_PORT=8000
FRONTEND_PORT=3000
OLLAMA_URL="http://localhost:11434"
DB_NAME="ask_techbear"

# ── Colors for readability ─────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[start-dev]${NC} $1"; }
warn() { echo -e "${YELLOW}[start-dev]${NC} $1"; }
fail() { echo -e "${RED}[start-dev]${NC} $1"; }

# ── Track background PIDs for cleanup ──────────────────────────
PIDS=()
cleanup() {
    echo ""
    warn "Shutting down..."
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null
        fi
    done
    wait 2>/dev/null
    log "All processes stopped."
    exit 0
}
trap cleanup INT TERM

# ── Sanity check: are we at the project root? ──────────────────
if [[ ! -d "backend" || ! -d "frontend" ]]; then
    fail "Run this from the project root (backend/ and frontend/ not found here)."
    exit 1
fi

# ── Optional: seed the blocklist ────────────────────────────────
# Not run by default — the blocklist is meant to be edited live
# (see ROADMAP.md's adaptive moderation notes), so auto-reseeding
# on every launch could silently undo a deliberate removal mid-event.
# Run explicitly with: ./AskTB.sh --seed
SEED_BLOCKLIST=false
for arg in "$@"; do
    if [[ "$arg" == "--seed" ]]; then
        SEED_BLOCKLIST=true
    fi
done

# ── 1. Check Postgres is accepting connections ─────────────────
log "Checking Postgres..."
if command -v pg_isready >/dev/null 2>&1; then
    if pg_isready -q; then
        log "Postgres is up."
    else
        fail "Postgres isn't responding. Start Postgres.app (or your pg service) and re-run."
        exit 1
    fi
else
    warn "pg_isready not found — skipping check, but make sure Postgres is running."
fi

# Confirm the actual database exists (createdb is idempotent-unsafe, so just check)
if command -v psql >/dev/null 2>&1; then
    if ! psql -lqt 2>/dev/null | cut -d '|' -f 1 | grep -qw "$DB_NAME"; then
        warn "Database '$DB_NAME' not found. Creating it..."
        createdb "$DB_NAME" && log "Created database '$DB_NAME'." || {
            fail "Failed to create database '$DB_NAME'. Create it manually: createdb $DB_NAME"
            exit 1
        }
    fi
fi

# ── 2. Check Ollama is actually responding ──────────────────────
log "Checking Ollama..."
if curl -s -o /dev/null -w "%{http_code}" "$OLLAMA_URL" 2>/dev/null | grep -q "200"; then
    log "Ollama is up at $OLLAMA_URL."
else
    fail "Ollama isn't responding at $OLLAMA_URL."
    fail "Open the Ollama app or run 'ollama serve' in another terminal, then re-run this script."
    exit 1
fi

# Quick model presence check (won't block startup, just warns)
if command -v ollama >/dev/null 2>&1; then
    MODELS=$(ollama list 2>/dev/null)
    for m in "llama3.1:8b" "nomic-embed-text"; do
        if ! echo "$MODELS" | grep -q "$m"; then
            warn "Model '$m' not found locally. Run: ollama pull $m"
        fi
    done
fi

# ── 3. Start backend ─────────────────────────────────────────────
log "Starting FastAPI backend on port $BACKEND_PORT..."
if [[ ! -f "$VENV_PATH" ]]; then
    fail "Virtualenv not found at $VENV_PATH. Adjust VENV_PATH in this script, or create the venv per the README."
    exit 1
fi

(
    source "$VENV_PATH"
    uvicorn backend.main:app --reload --port "$BACKEND_PORT"
) &
BACKEND_PID=$!
PIDS+=("$BACKEND_PID")

# Give the backend a moment, then confirm /health responds
log "Waiting for backend to come up..."
for i in {1..15}; do
    if curl -s "http://localhost:$BACKEND_PORT/health" 2>/dev/null | grep -q "ok"; then
        log "Backend is healthy."
        break
    fi
    if [[ $i -eq 15 ]]; then
        fail "Backend didn't respond after 15s. Check the uvicorn output above for errors."
    fi
    sleep 1
done

# ── Optional seed step (only if --seed was passed) ──────────────
# Runs after the backend's health check, since init_db()'s create_all
# (which makes the blocklist table exist) happens during FastAPI's
# startup lifespan — seeding any earlier would hit a missing-table error.
if [[ "$SEED_BLOCKLIST" == true ]]; then
    log "Seeding blocklist (--seed flag passed)..."
    if [[ -f "$VENV_PATH" ]]; then
        (source "$VENV_PATH" && python -m backend.scripts.seed_blocklist) || \
            warn "Blocklist seed step failed — backend will still start, but check the blocklist table manually."
    else
        warn "Can't seed — venv not found at $VENV_PATH. Skipping."
    fi
fi

# ── 4. Start frontend ─────────────────────────────────────────────
log "Starting Vite frontend on port $FRONTEND_PORT..."
(
    cd frontend
    npm run dev -- --port "$FRONTEND_PORT"
) &
FRONTEND_PID=$!
PIDS+=("$FRONTEND_PID")

log "─────────────────────────────────────────────"
log "Everything's up:"
log "  Backend:    http://localhost:$BACKEND_PORT  (docs at /docs)"
log "  Frontend:   http://localhost:$FRONTEND_PORT"
log "  Submit:     http://localhost:$FRONTEND_PORT/submit"
log "  Dashboard:  http://localhost:$FRONTEND_PORT/dashboard"
log "  Slideshow:  http://localhost:$FRONTEND_PORT/slideshow"
log "Press Ctrl+C to stop everything."
log "(Run with --seed to seed the blocklist on this launch: ./AskTB.sh --seed)"
log "─────────────────────────────────────────────"

# Keep script alive until Ctrl+C
wait
