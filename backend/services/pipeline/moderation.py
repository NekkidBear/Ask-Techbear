"""
TechBear Async Pipeline — moderation.py

Moderation layer for the async batch pipeline.
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

OLLAMA_URL = os.getenv(
    "OLLAMA_BASE_URL", "http://localhost:11434"
) + "/api/chat"

MODERATION_MODEL = os.getenv("MODERATION_MODEL", "llama3.1:8b")
BLOCKLIST_THRESHOLD = int(os.getenv("BLOCKLIST_THRESHOLD", "85"))

# Submission source keys that carry a known retrieval_mode hint.
# When LLM classification fails, these are checked in order before
# falling back to "factual". This keeps lore/tall_tale questions
# correctly routed even when the moderation model returns bad JSON.
_RETRIEVAL_MODE_HINT_KEYS = (
    "expected_retrieval_mode",  # set by test harness
    "retrieval_mode_hint",      # optional production override
)


@lru_cache(maxsize=1)
def _load_blocklist() -> list[tuple[str, str]]:
    """Load blocklist terms from Postgres via psycopg2."""
    if _psycopg2 is None:
        return []

    try:
        db_url = os.getenv(
            "DATABASE_URL",
            "postgresql://localhost/ask_techbear"
        )
        sync_url = db_url.replace(
            "postgresql+asyncpg://",
            "postgresql://",
        )

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
    """Check text against exact, prefix, and fuzzy blocklist matches."""
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


def _extract_first_json_object(raw: str) -> str:
    """
    Extract the first complete JSON object from an LLM response.

    This tolerates cases where the model returns valid JSON followed by
    extra commentary, while avoiding false brace matches inside strings.
    """
    raw = raw.strip()
    start = raw.find("{")

    if start == -1:
        raise ValueError("No JSON object found in moderation response")

    depth = 0
    in_string = False
    escape = False

    for i, ch in enumerate(raw[start:], start=start):
        if escape:
            escape = False
            continue

        if ch == "\\":
            escape = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1

            if depth == 0:
                return raw[start:i + 1]

    raise ValueError("Unclosed JSON object in moderation response")


def _parse_llm_json(raw: str) -> dict:
    """Parse LLM JSON, repairing common trailing-text failures."""
    raw = raw.strip()

    if raw.startswith("```"):
        raw = "\n".join(
            line for line in raw.splitlines()
            if not line.strip().startswith("```")
        ).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        extracted = _extract_first_json_object(raw)
        return json.loads(extracted)


def _character_dir() -> Path:
    """Resolve backend/character/ relative to this file."""
    return Path(__file__).resolve().parents[2] / "character"


def _fallback_retrieval_mode(submission: dict) -> str:
    """
    Determine the best retrieval_mode to use when LLM classification fails.

    Checks submission hint keys in priority order before defaulting to
    "factual". This ensures lore/tall_tale questions are not silently
    downgraded to factual retrieval on a moderation parse failure.
    """
    for key in _RETRIEVAL_MODE_HINT_KEYS:
        hint = submission.get(key)
        if hint in ("lore", "tall_tale", "hybrid", "factual"):
            return hint
    return "factual"


def _classify_intent(question: str, submission: dict) -> dict:
    """Ask the moderation model to classify intent and scope."""
    character_moderation_path = (
        _character_dir() / "character_moderation.md"
    )

    if character_moderation_path.exists():
        template = character_moderation_path.read_text(encoding="utf-8")

        submission_json = json.dumps(
            {
                "question": question,
                "attendee_name": submission.get("attendee_name", ""),
                "conversation_depth": submission.get(
                    "conversation_depth", 0
                ),
                "source": submission.get("source", ""),
            },
            indent=2,
        )

        system_prompt = template.replace(
            "{SUBMISSION_JSON}",
            submission_json,
        )
    else:
        system_prompt = (
            "You are a moderation classifier for a community tech Q&A event. "
            "Classify the submission. "
            "Output ONLY valid JSON, no preamble.\n\n"
            "Required fields: decision (pass|reject|funnel|redirect), "
            "scope "
            "(IN_SCOPE|FUNNEL|OFF_TOPIC_FUN|OFF_TOPIC_PERSONAL|"
            "OFF_TOPIC_INAPPROPRIATE), "
            "intent "
            "(genuine_question|frustration|directed_attack|spam|"
            "jailbreak_attempt), "
            "confidence (0.0-1.0), flag_reason (string or null), "
            "conversation_depth_action "
            "(proceed|redirect_to_consultation)"
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                "Classify this submission:\n\n"
                f"Question: {question}\n"
                f"Attendee: {submission.get('attendee_name', '')}\n"
                "Conversation depth: "
                f"{submission.get('conversation_depth', 0)}\n\n"
                "Output only valid JSON."
            ),
        },
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
        return _parse_llm_json(raw)

    except (RequestException, json.JSONDecodeError, ValueError) as exc:
        # LLM classification failed — return a safe pass with the flag set.
        # retrieval_mode is intentionally omitted here: run() will recover
        # it from the submission context via _fallback_retrieval_mode(),
        # preventing lore/tall_tale questions from being silently downgraded
        # to factual retrieval on a parse failure.
        return {
            "decision": "pass",
            "scope": "IN_SCOPE",
            "intent": "genuine_question",
            "confidence": 0.5,
            "flag_reason": f"llm_classification_failed: {exc}",
            "conversation_depth_action": "proceed",
        }


def run(artifact: dict) -> dict:
    """Execute the moderation phase."""
    submission = artifact.get("submission", {})
    question = submission.get("question", "")
    depth = submission.get("conversation_depth", 0)

    if depth >= 2:
        artifact.setdefault("scores", {})["moderation"] = {
            "decision": "redirect",
            "scope": "IN_SCOPE",
            "intent": "genuine_question",
            "conversation_depth_action": "redirect_to_consultation",
            "blocklist_flagged": False,
            "flag_reason": (
                f"conversation_depth={depth} — consultation redirect"
            ),
        }

        artifact["passed"] = False
        artifact["failure_reason"] = (
            f"moderation: conversation_depth={depth} — "
            "route to consultation redirect generator, not main pipeline"
        )
        return artifact

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

    diagnostic_mode = submission.get("diagnostic_mode", False)
    classification = _classify_intent(question, submission)

    # Recover retrieval_mode when LLM classification failed.
    # _classify_intent() omits retrieval_mode from its fallback dict so
    # that this recovery point is the single place that handles it.
    # Without this, a parse failure silently routes lore questions to
    # factual retrieval, producing cross-episode contaminated answers
    # that pass fact_critique with accuracy=0.
    parse_failed = "llm_classification_failed" in (
        classification.get("flag_reason") or ""
    )
    if parse_failed or "retrieval_mode" not in classification:
        classification["retrieval_mode"] = _fallback_retrieval_mode(submission)

    if diagnostic_mode:
        artifact.setdefault("diagnostics", {})[
            "moderation_raw_response"
        ] = json.dumps(classification)

        artifact["diagnostics"]["moderation_parse_succeeded"] = (
            not parse_failed
        )

    decision = classification.get("decision", "pass")
    flag_reason = classification.get("flag_reason")
    retrieval_mode = classification.get("retrieval_mode", "factual")

    if retrieval_mode in ("lore", "hybrid", "tall_tale") and decision == "funnel":
        decision = "pass"
        classification["decision"] = "pass"
        classification["scope"] = (
            classification.get("scope") or "OFF_TOPIC_FUN"
        )
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
    print(
        "submission.retrieval_mode:",
        artifact["submission"].get("retrieval_mode"),
    )
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
        artifact["passed"] = False
        artifact["failure_reason"] = (
            "moderation: FUNNEL question — route to consultation redirect"
        )

    return artifact
