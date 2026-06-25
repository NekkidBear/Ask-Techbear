"""
TechBear Async Pipeline — editorial_pass.py

Editorial annotation pass.
Model: llama3.1:8b.
Character context: character_identity.md + character_editorial.md.

Flags anomalies as possible_error vs intentional_voice.
Does NOT auto-correct — annotates only. Writer (Jason) has final say.
The "red squiggly" model: surface decisions, don't make them.

Output: artifact["drafts"]["editorial"] = voice draft with inline flag refs,
        artifact["flags"]["editorial_pass"] = list of annotation objects
"""

import json
import os
from pathlib import Path

import requests
from requests.exceptions import RequestException

# =============================================================
# Configuration
# =============================================================

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/chat"
EDITORIAL_MODEL = os.getenv("EDITORIAL_MODEL", "llama3.1:8b")

# backend/services/pipeline/editorial_pass.py
# parents[0]=pipeline/ [1]=services/ [2]=backend/ → backend/character/
CHARACTER_DIR = Path(__file__).resolve().parents[2] / "character"
IDENTITY_FILE = CHARACTER_DIR / "character_identity.md"
EDITORIAL_FILE = CHARACTER_DIR / "character_editorial.md"


# =============================================================
# Helpers
# =============================================================

def _load_character_editorial() -> tuple[str, str]:
    identity = (
        IDENTITY_FILE.read_text(encoding="utf-8")
        if IDENTITY_FILE.exists() else
        "(character_identity.md not found)"
    )
    editorial = (
        EDITORIAL_FILE.read_text(encoding="utf-8")
        if EDITORIAL_FILE.exists() else
        "(character_editorial.md not found — apply general editorial standards)"
    )
    return identity, editorial


def _build_messages(voice_draft: str, factual_draft: str) -> list[dict]:
    identity, editorial = _load_character_editorial()

    system = (
        identity
        + "\n\n---\n\n"
        + editorial
        + "\n\n---\n\n"
        "You are an editorial annotator, not a copy editor. "
        "Flag potential issues. Do not rewrite anything. "
        "Output ONLY valid JSON. No preamble. No markdown fences."
    )

    user = f"""Annotate the following TechBear draft. Flag anomalies as
possible_error or intentional_voice. Do NOT rewrite any text.

VOICE DRAFT:
\"\"\"
{voice_draft}
\"\"\"

FACTUAL DRAFT (reference for claim consistency):
\"\"\"
{factual_draft}
\"\"\"

Respond with this exact JSON structure:
{{
  "annotations": [
    {{
      "id": "<short unique id, e.g. ann_001>",
      "type": "possible_error" | "intentional_voice",
      "location": "<exact quoted text from the draft>",
      "note": "<brief explanation of what to check or confirm>",
      "suggested_fix": "<optional: only if type is possible_error and the fix is obvious>"
    }}
  ],
  "clean": <true|false>,
  "annotation_count": <int>,
  "summary": "<one-sentence overall editorial note>"
}}

clean: true if no possible_error annotations (intentional_voice annotations are fine)
Do not flag TechBear's established voice patterns (fragments, ALL CAPS, em-dashes,
comma splices for rhythm) as errors — these are intentional_voice.
"""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _call_ollama(messages: list[dict]) -> str:
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": EDITORIAL_MODEL,
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
    Execute the editorial annotation pass.

    Reads from:
        artifact["drafts"]["voice"]    — voice draft to annotate
        artifact["drafts"]["factual"]  — factual draft (claim consistency reference)

    Writes to:
        artifact["drafts"]["editorial"]      — same as voice draft (unchanged text)
        artifact["flags"]["editorial_pass"]  — list of annotation objects
        artifact["scores"]["editorial_pass"] — phase metadata
        artifact["passed"]                   — editorial pass is non-blocking
                                               (never sets passed=False; human decides)
    """
    voice_draft = artifact.get("drafts", {}).get("voice", "")
    factual_draft = artifact.get("drafts", {}).get("factual", "")

    if not voice_draft:
        artifact["passed"] = False
        artifact["failure_reason"] = "editorial_pass: no voice draft to annotate"
        return artifact

    messages = _build_messages(voice_draft, factual_draft)

    try:
        raw = _call_ollama(messages)
        result = _parse_response(raw)
    except (RequestException, json.JSONDecodeError, ValueError) as exc:
        # Editorial pass failure is non-fatal — pipeline continues
        # Human review will see the error flag
        artifact.setdefault("scores", {})["editorial_pass"] = {
            "status": "error",
            "error": str(exc),
        }
        artifact.setdefault("flags", {})["editorial_pass_error"] = str(exc)
        # The editorial draft is just the voice draft unchanged
        artifact.setdefault("drafts", {})["editorial"] = voice_draft
        return artifact

    annotations = result.get("annotations", [])

    # The editorial draft is the voice draft text — the annotations are
    # stored separately. We don't embed markup in the text itself.
    artifact.setdefault("drafts", {})["editorial"] = voice_draft
    artifact.setdefault("flags", {})["editorial_pass"] = annotations
    artifact.setdefault("scores", {})["editorial_pass"] = {
        "status": "complete",
        "model": EDITORIAL_MODEL,
        "clean": result.get("clean", True),
        "annotation_count": result.get("annotation_count", len(annotations)),
        "error_annotation_count": sum(
            1 for a in annotations if a.get("type") == "possible_error"
        ),
        "summary": result.get("summary"),
    }

    # Editorial pass is intentionally non-blocking — writer has final say
    # The human review UI surfaces these annotations for accept/reject decisions

    return artifact
