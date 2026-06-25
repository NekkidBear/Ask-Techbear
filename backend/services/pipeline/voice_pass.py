"""
TechBear Async Pipeline — voice_pass.py

Voice rewrite pass.
Model: qwen2.5:7b + rag_voice (techbear_voice collection only).
Character context: character_identity.md + character_voice.md.
Hard constraint: rephrase only — cannot introduce new facts.

Rewrites the critic-approved factual draft in TechBear's voice
using retrieved personality corpus chunks as live few-shot examples.
Output: character artifact written into artifact["drafts"]["voice"].
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
VOICE_MODEL = os.getenv("VOICE_MODEL", "qwen2.5:7b")

# backend/services/pipeline/voice_pass.py
# parents[0]=pipeline/ [1]=services/ [2]=backend/ → backend/character/
CHARACTER_DIR = Path(__file__).resolve().parents[2] / "character"
IDENTITY_FILE = CHARACTER_DIR / "character_identity.md"
VOICE_FILE = CHARACTER_DIR / "character_voice.md"


# =============================================================
# Helpers
# =============================================================

def _load_character_voice() -> tuple[str, str]:
    for path in (IDENTITY_FILE, VOICE_FILE):
        if not path.exists():
            raise FileNotFoundError(
                f"Character file not found: {path}. "
                "Run the character file split before using the pipeline."
            )
    return (
        IDENTITY_FILE.read_text(encoding="utf-8"),
        VOICE_FILE.read_text(encoding="utf-8"),
    )


def _format_voice_context(chunks: list[dict]) -> str:
    if not chunks:
        return "(No voice corpus examples retrieved — rely on character file guidance.)"
    return "\n\n".join(
        f"[VOICE EXAMPLE {i + 1}]\n{c['text']}"
        for i, c in enumerate(chunks)
    )


def _build_messages(
    factual_draft: str,
    voice_chunks: list[dict],
    rolling_context: str,
) -> list[dict]:
    identity, voice_template = _load_character_voice()
    voice_context = _format_voice_context(voice_chunks)
    rolling = rolling_context if rolling_context else "(No prior questions this session.)"

    voice_template = (
        voice_template
        .replace("{ROLLING_CONTEXT}", rolling)
        .replace("{VOICE_CONTEXT}", voice_context)
        .replace("{FACTUAL_DRAFT}", factual_draft)
    )
    system_prompt = identity + "\n\n---\n\n" + voice_template

    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                "Rewrite the factual draft above in TechBear's voice, following all character "
                "and structural guidance. Remember: you may only rephrase, not add or remove "
                "technical claims. Keep the response between 150 and 250 words. "
                "Respond only with the TechBear response — no preamble, no labels."
            )
        }
    ]


def _call_ollama(messages: list[dict]) -> str:
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": VOICE_MODEL,
            "messages": messages,
            "stream": False,
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


# =============================================================
# Phase entry point
# =============================================================

def run(artifact: dict) -> dict:
    """
    Execute the voice rewrite pass.

    Reads from:
        artifact["drafts"]["factual"]            — critic-approved factual draft
        artifact["retrieval"]["voice"]            — RAG chunks from techbear_voice
        artifact["submission"]["rolling_context"] — session history (optional)

    Writes to:
        artifact["drafts"]["voice"]         — TechBear character draft
        artifact["scores"]["voice_pass"]    — phase metadata
        artifact["passed"]                  — False if Ollama call fails
    """
    # Prefer the educationally-structured draft if the educational pass ran
    factual_draft = (
        artifact.get("drafts", {}).get("educational_structure")
        or artifact.get("drafts", {}).get("factual", "")
    )
    voice_chunks = artifact.get("retrieval", {}).get("voice", [])
    rolling_context = artifact.get("submission", {}).get("rolling_context", "")

    if not factual_draft:
        artifact["passed"] = False
        artifact["failure_reason"] = "voice_pass: no factual draft available to rewrite"
        return artifact

    messages = _build_messages(factual_draft, voice_chunks, rolling_context)

    try:
        voice_draft = _call_ollama(messages)
    except (RequestException, json.JSONDecodeError, ValueError) as exc:
        artifact["passed"] = False
        artifact["failure_reason"] = f"voice_pass: Ollama call failed — {exc}"
        artifact.setdefault("scores", {})["voice_pass"] = {
            "status": "error",
            "error": str(exc),
        }
        return artifact

    word_count = len(voice_draft.split())

    artifact.setdefault("drafts", {})["voice"] = voice_draft
    artifact.setdefault("scores", {})["voice_pass"] = {
        "status": "complete",
        "model": VOICE_MODEL,
        "voice_chunks_used": len(voice_chunks),
        "word_count": word_count,
        "word_count_ok": 150 <= word_count <= 250,
    }

    return artifact
