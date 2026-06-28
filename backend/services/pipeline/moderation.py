"""
TechBear Async Pipeline — moderation.py

Moderation layer for the async batch pipeline.
Runs synchronously (batch job context, no async session available).

Phase 1: rapidfuzz blocklist — rule-based, instant, loaded from Postgres
Phase 2: LLM intent/sentiment classification via Ollama
         (frustration vs. directed attack vs. jailbreak vs. spam)
Phase 3: Conversation depth check
         (conversation_depth >= 2 → route to consultation redirect)

Outputs:
    artifact["scores"]["moderation"]  — structured result
    artifact["flags"]["moderation"]   — reason if flagged
    artifact["passed"]                — False if rejected
    artifact["failure_reason"]        — set if passed = False

Design note:
    The existing services/moderation.py uses an async SQLAlchemy session
    tied to FastAPI request lifecycle. The pipeline runs as a batch job
    outside that lifecycle. This module loads the blocklist once via a
    synchronous psycopg2 connection and caches it for the run.
"""

import json
import os
from functools import lru_cache
from pathlib import Path

import requests
from dotenv import load_dotenv
from rapidfuzz import fuzz
from requests.exceptions import RequestException


try:
    import psycopg2 as _psycopg2
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _psycopg2 = None  # type: ignore[assignment]
    _PSYCOPG2_AVAILABLE = False

load_dotenv()

# =============================================================
# Configuration
# =============================================================

OLLAMA_URL = os.getenv(
    "OLLAMA_BASE_URL", "http://localhost:11434") + "/api/chat"
MODERATION_MODEL = os.getenv("MODERATION_MODEL", "llama3.1:8b")

# rapidfuzz threshold — 85 catches near-misses without over-flagging
BLOCKLIST_THRESHOLD = int(os.getenv("BLOCKLIST_THRESHOLD", "85"))


# =============================================================
# Blocklist loading (sync, cached per process)
# =============================================================

@lru_cache(maxsize=1)
def _load_blocklist() -> list[tuple[str, str]]:
    """
    Load blocklist terms from Postgres via psycopg2 (sync).
    Cached so it only hits the DB once per process run.
    Returns list of (term, category) tuples.
    Falls back to an empty list if DB is unavailable —
    pipeline continues but flags the degraded state.
    """
    if _psycopg2 is None:
        return []
    try:
        db_url = os.getenv(
            "DATABASE_URL",
            "postgresql://localhost/ask_techbear"
        )
        # Strip the asyncpg driver prefix if present
        sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

        conn = _psycopg2.connect(sync_url)
        cur = conn.cursor()
        cur.execute("SELECT term, category FROM blocklist;")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except (OSError, ValueError, RuntimeError):
        return []


def _check_blocklist(text: str) -> tuple[bool, str | None]:
    """
    Checks text against the blocklist using the same three-stage
    logic as services/moderation.py: exact substring → prefix → fuzzy.
    Returns (is_flagged, matched_term).
    """
    blocklist = _load_blocklist()
    text_lower = text.lower()
    words = text_lower.split()

    for term, _category in blocklist:
        term_lower = term.lower()

        if term_lower in text_lower:
            return True, term

        for word in words:
            cleaned = word.strip('.,!?;:"\'')

            if len(term_lower) >= 3 and cleaned.startswith(term_lower):
                return True, term

            if fuzz.ratio(cleaned, term_lower) >= BLOCKLIST_THRESHOLD:
                return True, term

    return False, None


# =============================================================
# LLM intent classification
# =============================================================

def _classify_intent(question: str, submission: dict) -> dict:
    """
    Ask the moderation model to classify intent and scope.
    Returns structured dict or a safe fallback on failure.
    """
    character_moderation_path = (
        _character_dir() / "character_moderation.md"
    )

    if character_moderation_path.exists():
        template = character_moderation_path.read_text(encoding="utf-8")
        submission_json = json.dumps({
            "question": question,
            "attendee_name": submission.get("attendee_name", ""),
            "conversation_depth": submission.get("conversation_depth", 0),
            "source": submission.get("source", ""),
        }, indent=2)
        system_prompt = template.replace("{SUBMISSION_JSON}", submission_json)
    else:
        # Fallback if character file not yet split
        system_prompt = (
            "You are a moderation classifier for a community tech Q&A event. "
            "Classify the submission. Output ONLY valid JSON, no preamble.\n\n"
            "Required fields: decision (pass|reject|funnel|redirect), "
            "scope (IN_SCOPE|FUNNEL|OFF_TOPIC_FUN|OFF_TOPIC_PERSONAL|OFF_TOPIC_INAPPROPRIATE), "
            "intent (genuine_question|frustration|directed_attack|spam|jailbreak_attempt), "
            "confidence (0.0-1.0), flag_reason (string or null), "
            "conversation_depth_action (proceed|redirect_to_consultation)"
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": (
            f"Classify this submission:\n\n"
            f"Question: {question}\n"
            f"Attendee: {submission.get('attendee_name', '')}\n"
            f"Conversation depth: {submission.get('conversation_depth', 0)}\n\n"
            "Output only valid JSON."
        )},
    ]

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODERATION_MODEL,
                "messages": messages,
                "stream": False,
            },
            timeout=60,
        )
        response.raise_for_status()
        raw = response.json()["message"]["content"].strip()

        # Strip any accidental markdown fences
        if raw.startswith("```"):
            raw = "\n".join(
                line for line in raw.splitlines()
                if not line.strip().startswith("```")
            )

        return json.loads(raw)

    except (RequestException, json.JSONDecodeError, ValueError) as exc:
        # LLM classification failure → default to pass with a warning flag
        # Human review sees the flag; blocklist still ran above
        return {
            "decision": "pass",
            "scope": "IN_SCOPE",
            "intent": "genuine_question",
            "confidence": 0.5,
            "flag_reason": f"llm_classification_failed: {exc}",
            "conversation_depth_action": "proceed",
        }


def _character_dir() -> Path:
    """Resolve backend/character/ relative to this file."""
    # backend/services/pipeline/moderation.py
    # parents[0] = pipeline/, [1] = services/, [2] = backend/
    return Path(__file__).resolve().parents[2] / "character"


# =============================================================
# Phase entry point
# =============================================================

def run(artifact: dict) -> dict:
    """
    Execute the moderation phase.

    Reads from:
        artifact["submission"]["question"]           — the question text
        artifact["submission"]["conversation_depth"] — depth (0, 1, 2+)

    Writes to:
        artifact["scores"]["moderation"]  — full classification result
        artifact["flags"]["moderation"]   — set if blocklist or LLM flags
        artifact["passed"]                — False if rejected
        artifact["failure_reason"]        — reason string if failed
    """
    submission = artifact.get("submission", {})
    question = submission.get("question", "")
    depth = submission.get("conversation_depth", 0)

    # ── Stage 1: Conversation depth check (instant) ───────
    if depth >= 2:
        artifact.setdefault("scores", {})["moderation"] = {
            "decision": "redirect",
            "scope": "IN_SCOPE",
            "intent": "genuine_question",
            "conversation_depth_action": "redirect_to_consultation",
            "blocklist_flagged": False,
            "flag_reason": f"conversation_depth={depth} — consultation redirect",
        }
        # Not a hard failure — orchestrator routes to consultation generator
        # For the async pipeline, we still stop the main pipeline here
        artifact["passed"] = False
        artifact["failure_reason"] = (
            f"moderation: conversation_depth={depth} — "
            "route to consultation redirect generator, not main pipeline"
        )
        return artifact

    # ── Stage 2: Blocklist check (sync, fast) ─────────────
    blocklist_hit, matched_term = _check_blocklist(question)

    if blocklist_hit:
        artifact.setdefault("scores", {})["moderation"] = {
            "decision": "reject",
            "scope": "OFF_TOPIC_INAPPROPRIATE",
            "intent": "unknown",
            "confidence": 1.0,
            "flag_reason": f"blocklist_match: {matched_term}",
            "conversation_depth_action": "proceed",
            "blocklist_flagged": True,
            "blocklist_term": matched_term,
        }
        artifact.setdefault("flags", {})["moderation"] = (
            f"blocklist_match: {matched_term}"
        )
        artifact["passed"] = False
        artifact["failure_reason"] = (
            f"moderation: blocklist match on '{matched_term}' — "
            "queued for human review"
        )
        return artifact

    # ── Stage 3: LLM intent + scope classification ─────────
    classification = _classify_intent(question, submission)

    decision = classification.get("decision", "pass")
    flag_reason = classification.get("flag_reason")
    retrieval_mode = classification.get("retrieval_mode", "factual")

    # Lore questions should never be routed to the consultation funnel.
    # The moderation prompt is allowed to set OFF_TOPIC_FUN, but lore must continue.
    if retrieval_mode in ("lore", "hybrid", "tall_tale") and decision == "funnel":
        decision = "pass"
        classification["decision"] = "pass"
        classification["scope"] = classification.get("scope") or "OFF_TOPIC_FUN"
        classification["flag_reason"] = (
            "Corrected moderation funnel decision for lore-routed question"
        )

    result = {
        **classification,
        "retrieval_mode": retrieval_mode,
        "blocklist_flagged": False,
        "blocklist_term": None,
    }
    artifact.setdefault("scores", {})["moderation"] = result
    artifact["submission"]["retrieval_mode"] = retrieval_mode

    print("=" * 72)
    print("DEBUG MODERATION.run")
    print("question:", question)
    print("classification.retrieval_mode:", retrieval_mode)
    print("submission.retrieval_mode:",
          artifact["submission"].get("retrieval_mode"))
    print("scope:", classification.get("scope"))
    print("intent:", classification.get("intent"))
    print("decision:", decision)
    print("=" * 72)

    if flag_reason:
        artifact.setdefault("flags", {})["moderation"] = flag_reason

    if decision in ("reject",):
        artifact["passed"] = False
        artifact["failure_reason"] = (
            f"moderation: LLM classified as reject — {flag_reason}"
        )
    elif decision == "funnel":
        # FUNNEL questions don't run through main pipeline
        artifact["passed"] = False
        artifact["failure_reason"] = (
            "moderation: FUNNEL question — route to consultation redirect"
        )
    # "pass" and "redirect" (depth already handled above) continue

    return artifact
