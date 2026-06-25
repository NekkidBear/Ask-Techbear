"""
TechBear Async Pipeline — educational_pass.py

Educational structuring pass.
Model: llama3.1:8b.
Character context: character_education.md.

Sits between fact_critique and voice_pass.
Receives the accuracy-validated factual draft and restructures it
for teaching effectiveness — lesson arc, analogy scaffolding,
concept sequencing, audience calibration.

Does NOT add or remove technical claims.
Does NOT apply TechBear's voice (that is voice_pass's job).
Produces a plain-prose restructured draft with section markers
([ORIENT], [STAKES], [CONCEPT], [ACTION], [TRANSFER]) that
voice_pass uses as structural scaffolding.

Pipeline position:
    fact_critique → educational_pass → voice_pass
"""

import os
from pathlib import Path

import json
import requests
from requests.exceptions import RequestException

# =============================================================
# Configuration
# =============================================================

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/chat"
EDUCATION_MODEL = os.getenv("EDUCATION_MODEL", "llama3.1:8b")

# backend/services/pipeline/educational_pass.py
# parents[0]=pipeline/ [1]=services/ [2]=backend/ → backend/character/
CHARACTER_DIR = Path(__file__).resolve().parents[2] / "character"
EDUCATION_FILE = CHARACTER_DIR / "character_education.md"


# =============================================================
# Helpers
# =============================================================

def _load_education_character() -> str:
    if not EDUCATION_FILE.exists():
        raise FileNotFoundError(
            f"character_education.md not found at {EDUCATION_FILE}. "
            "Run the character file split before using the pipeline."
        )
    return EDUCATION_FILE.read_text(encoding="utf-8")


def _build_messages(factual_draft: str) -> list[dict]:
    template = _load_education_character()
    system_prompt = template.replace("{FACTUAL_DRAFT}", factual_draft)

    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                "Restructure the factual draft above for teaching effectiveness. "
                "Use the five section markers: [ORIENT], [STAKES], [CONCEPT], "
                "[ACTION], [TRANSFER]. "
                "Do not add or remove technical claims — only resequence and "
                "reframe for a general public audience at a live tabling event. "
                "No TechBear voice. No sass. Plain, clear prose under each marker. "
                "Keep it concise — the voice pass will expand the performance."
            ),
        },
    ]


def _call_ollama(messages: list[dict]) -> str:
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": EDUCATION_MODEL,
            "messages": messages,
            "stream": False,
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def _validate_markers(draft: str) -> dict:
    """
    Check that all five section markers are present.
    Returns a dict of which markers are present/absent.
    """
    markers = ["[ORIENT]", "[STAKES]", "[CONCEPT]", "[ACTION]", "[TRANSFER]"]
    return {
        marker: marker in draft
        for marker in markers
    }


# =============================================================
# Phase entry point
# =============================================================

def run(artifact: dict) -> dict:
    """
    Execute the educational structuring pass.

    Reads from:
        artifact["drafts"]["factual"]  — accuracy-validated factual draft

    Writes to:
        artifact["drafts"]["educational_structure"] — restructured draft with markers
        artifact["scores"]["educational_pass"]      — phase metadata
        artifact["passed"]                          — False only if Ollama fails
                                                      (non-fatal if markers incomplete)
    """
    factual_draft = artifact.get("drafts", {}).get("factual", "")

    if not factual_draft:
        artifact["passed"] = False
        artifact["failure_reason"] = (
            "educational_pass: no factual draft available to restructure"
        )
        return artifact

    messages = _build_messages(factual_draft)

    try:
        structured_draft = _call_ollama(messages)
    except (RequestException, json.JSONDecodeError, ValueError) as exc:
        artifact["passed"] = False
        artifact["failure_reason"] = f"educational_pass: Ollama call failed — {exc}"
        artifact.setdefault("scores", {})["educational_pass"] = {
            "status": "error",
            "error": str(exc),
        }
        return artifact

    marker_check = _validate_markers(structured_draft)
    markers_present = sum(1 for v in marker_check.values() if v)
    all_markers_present = all(marker_check.values())

    artifact.setdefault("drafts", {})["educational_structure"] = structured_draft
    artifact.setdefault("scores", {})["educational_pass"] = {
        "status": "complete",
        "model": EDUCATION_MODEL,
        "markers_present": markers_present,
        "markers_complete": all_markers_present,
        "marker_detail": marker_check,
    }

    # Incomplete markers are a quality flag, not a hard failure —
    # voice_pass will work with whatever structure is present
    if not all_markers_present:
        missing = [k for k, v in marker_check.items() if not v]
        artifact.setdefault("flags", {})["educational_pass_incomplete"] = (
            f"Missing markers: {missing}"
        )

    return artifact
