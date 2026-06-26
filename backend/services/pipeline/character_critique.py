"""
TechBear Async Pipeline — character_critique.py

Character fidelity critique.
Scores: character fidelity (0-10), regurgitation check (0-10),
        structure compliance (0-10), word count compliance (0-10).
Anti-formulaic check: penalizes mad-libs pattern responses.
Contiguous-run check: flags verbatim chunks >= 8 words from voice corpus.
Cross-batch check: flags repeated metaphors/jokes vs other approved drafts
                   in this batch (checked via batch_context in submission).

Uses rapidfuzz for the contiguous-run verbatim check —
same library, third job alongside blocklist and STT name-matching.
"""

import json
import os
from pathlib import Path

import requests
from rapidfuzz import fuzz
from requests.exceptions import RequestException

# =============================================================
# Configuration
# =============================================================

OLLAMA_URL = os.getenv(
    "OLLAMA_BASE_URL", "http://localhost:11434") + "/api/chat"
CHARACTER_CRITIQUE_MODEL = os.getenv(
    "CHARACTER_CRITIQUE_MODEL", "mistral:latest")

# backend/services/pipeline/character_critique.py
# parents[0]=pipeline/ [1]=services/ [2]=backend/ → backend/character/
CHARACTER_DIR = Path(__file__).resolve().parents[2] / "character"
VOICE_FILE = CHARACTER_DIR / "character_voice.md"

# Contiguous-run check: flag any run >= this many words matching a corpus chunk
CONTIGUOUS_RUN_WORDS = int(os.getenv("CONTIGUOUS_RUN_WORDS", "8"))
# rapidfuzz score threshold for considering a run verbatim
VERBATIM_THRESHOLD = int(os.getenv("VERBATIM_THRESHOLD", "90"))


# =============================================================
# Contiguous-run verbatim check (deterministic, no LLM)
# =============================================================

def _find_verbatim_runs(
    voice_draft: str,
    voice_chunks: list[dict],
    run_length: int = CONTIGUOUS_RUN_WORDS,
) -> list[dict]:
    """
    Check for verbatim lifted runs of >= run_length words from the corpus.
    Returns a list of flagged run dicts with matched chunk reference.

    Recurring TechBear verbal tics and signature phrases are
    voice consistency, not regurgitation — they will match but the
    LLM critique scores this dimension contextually.
    This check flags structural sentence-level lifting.
    """
    draft_words = voice_draft.split()
    flags = []

    if len(draft_words) < run_length:
        return flags

    for i in range(len(draft_words) - run_length + 1):
        candidate = " ".join(draft_words[i: i + run_length])

        for chunk in voice_chunks:
            chunk_text = chunk.get("text", "")
            score = fuzz.partial_ratio(candidate.lower(), chunk_text.lower())
            if score >= VERBATIM_THRESHOLD:
                flags.append({
                    "run": candidate,
                    "score": score,
                    "chunk_source": chunk.get("meta", {}).get("source", "unknown"),
                })
                break  # one flag per run position is enough

    return flags


# =============================================================
# LLM character fidelity critique
# =============================================================

def _load_voice_character() -> str:
    if VOICE_FILE.exists():
        return VOICE_FILE.read_text(encoding="utf-8")
    return "(character_voice.md not found — critique against general TechBear voice standards)"


def _build_critique_messages(
    voice_draft: str,
    verbatim_flags: list[dict],
    word_count: int,
    batch_context: list[str],
) -> list[dict]:
    voice_char = _load_voice_character()

    verbatim_summary = (
        f"{len(verbatim_flags)} verbatim run(s) flagged by contiguous-run check."
        if verbatim_flags else "No verbatim runs flagged by contiguous-run check."
    )
    batch_context_block = (
        "\n\n".join(f"[PRIOR DRAFT {i+1}]\n{d}" for i,
                    d in enumerate(batch_context))
        if batch_context else "(No other batch drafts to compare against.)"
    )

    system = (
        "You are a character fidelity critic for an AI editorial pipeline. "
        "You evaluate TechBear voice drafts against character standards. "
        "Output ONLY valid JSON. No preamble. No markdown fences."
    )

    user = f"""Evaluate this TechBear voice draft against the character standards below.

CHARACTER STANDARDS:
{voice_char}

VOICE DRAFT TO EVALUATE:
\"\"\"
{voice_draft}
\"\"\"

WORD COUNT: {word_count} (target: 150-250)

CONTIGUOUS-RUN CHECK RESULT: {verbatim_summary}

OTHER APPROVED DRAFTS THIS BATCH (for cross-batch repetition check):
{batch_context_block}

Respond with this exact JSON structure:
{{
  "character_fidelity_score": <int 0-10>,
  "regurgitation_score": <int 0-10>,
  "structure_compliance_score": <int 0-10>,
  "word_count_compliance_score": <int 0-10>,
  "anti_formulaic_score": <int 0-10>,
  "overall_pass": <true|false>,
  "flags": [
    {{
      "type": "character_break" | "verbatim_lift" | "structural_echo" | "repeated_metaphor" | "mad_libs_pattern" | "word_count_violation" | "ai_disclosure",
      "location": "<quote or description>",
      "reason": "<explanation>",
      "severity": "critical" | "moderate" | "minor"
    }}
  ],
  "summary": "<one-sentence assessment>",
  "pass_recommendation": "pass" | "loop_voice_pass" | "flag_for_review" | "escalate_human"
}}

Scoring guide:
- character_fidelity: 10 = unmistakably TechBear, 0 = generic AI response
- regurgitation: 10 = fully original within TechBear's voice, 0 = lifted wholesale from corpus
- structure_compliance: 10 = reaction/read/gospel/close rhythm present, 0 = no structure
- word_count_compliance: 10 = 150-250 words, scale down proportionally outside range
- anti_formulaic: 10 = does something unexpected within the structure, 0 = pure mad-libs
- overall_pass: true if all scores >= 6 and no critical flags
- pass_recommendation:
    "pass" if overall_pass;
    "loop_voice_pass" if character_fidelity < 6 or regurgitation < 6 (likely fixable by regenerating the voice draft);
    "flag_for_review" if minor issues only (scores >= 6 but not confident);
    "escalate_human" if critical flags present or AI self-disclosure detected
"""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _call_ollama(messages: list[dict]) -> str:
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": CHARACTER_CRITIQUE_MODEL,
            "messages": messages,
            "stream": False,
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def _parse_response(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(
            line for line in cleaned.splitlines()
            if not line.strip().startswith("```")
        )
    return json.loads(cleaned)


# =============================================================
# Phase entry point
# =============================================================

def run(artifact: dict) -> dict:
    """
    Execute the character fidelity critique.

    Reads from:
        artifact["drafts"]["voice"]       — voice draft to critique
        artifact["retrieval"]["voice"]     — corpus chunks (for verbatim check)
        artifact["submission"]["batch_context"] — other drafts this batch (optional)

    Writes to:
        artifact["scores"]["character_critique"] — structured critique result
        artifact["flags"]["character_critique"]  — flagged issues
        artifact["passed"]                       — False if escalation needed
        artifact["failure_reason"]               — set if passed = False
    """
    voice_draft = artifact.get("drafts", {}).get("voice", "")
    voice_chunks = artifact.get("retrieval", {}).get("voice", [])
    batch_context = artifact.get("submission", {}).get("batch_context", [])

    if not voice_draft:
        artifact["passed"] = False
        artifact["failure_reason"] = "character_critique: no voice draft to evaluate"
        return artifact

    word_count = len(voice_draft.split())

    # ── Deterministic verbatim check (no LLM) ─────────────
    verbatim_flags = _find_verbatim_runs(voice_draft, voice_chunks)

    # ── LLM character fidelity critique ───────────────────
    messages = _build_critique_messages(
        voice_draft, verbatim_flags, word_count, batch_context
    )

    try:
        raw = _call_ollama(messages)
        critique = _parse_response(raw)
    except (RequestException, json.JSONDecodeError, ValueError) as exc:
        artifact.setdefault("scores", {})["character_critique"] = {
            "status": "error",
            "error": str(exc),
            "verbatim_flags": verbatim_flags,
        }
        artifact.setdefault("flags", {})["character_critique_error"] = str(exc)
        return artifact

    all_flags = critique.get("flags", [])
    if verbatim_flags:
        # Merge deterministic flags into the LLM flag list
        for vf in verbatim_flags:
            all_flags.append({
                "type": "verbatim_lift",
                "location": vf["run"],
                "reason": f"rapidfuzz score {vf['score']} against {vf['chunk_source']}",
                "severity": "moderate",
            })

    artifact.setdefault("flags", {})["character_critique"] = all_flags
    artifact.setdefault("scores", {})["character_critique"] = {
        "status": "complete",
        "model": CHARACTER_CRITIQUE_MODEL,
        "character_fidelity_score": critique.get("character_fidelity_score"),
        "regurgitation_score": critique.get("regurgitation_score"),
        "structure_compliance_score": critique.get("structure_compliance_score"),
        "word_count_compliance_score": critique.get("word_count_compliance_score"),
        "anti_formulaic_score": critique.get("anti_formulaic_score"),
        "overall_pass": critique.get("overall_pass"),
        "summary": critique.get("summary"),
        "pass_recommendation": critique.get("pass_recommendation"),
        "verbatim_run_count": len(verbatim_flags),
        "flag_count": len(all_flags),
        "critical_flag_count": sum(
            1 for f in all_flags if f.get("severity") == "critical"
        ),
        "word_count": word_count,
    }

    recommendation = critique.get("pass_recommendation", "flag_for_review")

    if recommendation == "loop_voice_pass":
        # Signal the orchestrator to retry the voice pass — it enforces the cap
        artifact.setdefault("flags", {})[
            "character_critique_loop_requested"] = True

    elif recommendation == "escalate_human":
        artifact["passed"] = False
        artifact["failure_reason"] = (
            f"character_critique: escalating to human — "
            f"character_fidelity={critique.get('character_fidelity_score')}, "
            f"critical_flags={artifact['scores']['character_critique']['critical_flag_count']}"
        )
    # "pass" and "flag_for_review" leave artifact["passed"] = True

    return artifact
