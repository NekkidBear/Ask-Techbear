"""
TechBear Async Pipeline — factual_pass.py

Factual generation pass.
Model: llama3.1:8b + rag_facts (techbear_facts collection only).
Character context: character_facts.md only — no voice instructions.
Constraint: technical accuracy over performance. No character voice.
Output: plain-text fact artifact written into artifact["drafts"]["factual"].
"""

import json
import os
from pathlib import Path

import requests
from requests.exceptions import RequestException

# =============================================================
# Configuration
# =============================================================

OLLAMA_URL = os.getenv(
    "OLLAMA_BASE_URL", "http://localhost:11434") + "/api/chat"
FACTS_MODEL = os.getenv("FACTUAL_MODEL", "llama3.1:8b")

# backend/services/pipeline/factual_pass.py
# parents[0]=pipeline/ [1]=services/ [2]=backend/ → backend/character/
CHARACTER_DIR = Path(__file__).resolve().parents[2] / "character"
FACTS_CHARACTER = CHARACTER_DIR / "character_facts.md"


# =============================================================
# Helpers
# =============================================================

def _load_character_facts() -> str:
    if not FACTS_CHARACTER.exists():
        raise FileNotFoundError(
            f"character_facts.md not found at {FACTS_CHARACTER}. "
            "Run the character file split before using the pipeline."
        )
    return FACTS_CHARACTER.read_text(encoding="utf-8")


def _format_rag_context(chunks: list[dict]) -> str:
    if not chunks:
        return "(No specific article context retrieved for this question.)"
    return "\n\n".join(
        f"[SOURCE {i + 1}]\n{c['text']}"
        for i, c in enumerate(chunks)
    )


def _build_messages(
    sanitized_question: str,
    rag_context: str,
    episode_context_block: str = "",
) -> list[dict]:
    template = _load_character_facts()
    system_prompt = (
        template
        .replace("{EPISODE_CONTEXT}", episode_context_block)
        .replace("{RAG_CONTEXT}", rag_context)
        .replace("{SANITIZED_QUESTION}", sanitized_question)
    )

    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                "Generate a plain-language, technically accurate answer to the question above. "
                "No TechBear character voice. No sass. No metaphor. "
                "Only what is factually correct and supported by the sources provided. "
                "Flag any claim you are uncertain about with [UNCERTAIN]. "
                "Keep the answer focused and complete."
            )
        }
    ]


def _call_ollama(messages: list[dict]) -> str:
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": FACTS_MODEL,
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
    Execute the factual generation pass.

    Reads from:
        artifact["submission"]["question"]       — sanitized question
        artifact["retrieval"]["facts"]            — RAG chunks from techbear_facts
        artifact["retrieval"]["lore"]             — RAG chunks from techbear_lore
        artifact["retrieval"]["retrieval_mode"]   — routing key
        artifact["retrieval"]["episode_context"]  — episode isolation result (lore mode)

    Writes to:
        artifact["drafts"]["factual"]       — plain-text fact draft
        artifact["scores"]["factual_pass"]  — phase metadata
        artifact["passed"]                  — False if Ollama call fails
    """
    submission = artifact.get("submission", {})
    question = submission.get("question", "")
    retrieval = artifact.get("retrieval", {})
    retrieval_mode = retrieval.get("retrieval_mode", "factual")

    # Episode context — populated by retrieval phase when a dominant episode
    # was identified and contaminating chunks were removed.
    # to_prompt_block() is reconstructed here from the stored dict rather than
    # passing the EpisodeContext object, keeping the phase boundary clean.
    episode_context = retrieval.get("episode_context", {})
    episode_context_block = _build_episode_context_block(episode_context)

    # Use lore chunks for lore/tall_tale modes, fact chunks for factual/hybrid
    if retrieval_mode in ("lore", "tall_tale"):
        primary_chunks = retrieval.get("lore", [])
        chunk_label = "lore"
    elif retrieval_mode == "hybrid":
        primary_chunks = retrieval.get("facts", []) + retrieval.get("lore", [])
        chunk_label = "facts+lore"
    else:
        primary_chunks = retrieval.get("facts", [])
        chunk_label = "facts"

    rag_context = _format_rag_context(primary_chunks)
    messages = _build_messages(question, rag_context, episode_context_block)

    try:
        draft = _call_ollama(messages)
    except (RequestException, json.JSONDecodeError, ValueError) as exc:
        artifact["passed"] = False
        artifact["failure_reason"] = f"factual_pass: Ollama call failed — {exc}"
        artifact.setdefault("scores", {})["factual_pass"] = {
            "status": "error",
            "error": str(exc),
        }
        return artifact

    artifact.setdefault("drafts", {})["factual"] = draft
    artifact.setdefault("scores", {})["factual_pass"] = {
        "status": "complete",
        "model": FACTS_MODEL,
        "retrieval_mode": retrieval_mode,
        "chunk_source": chunk_label,
        "rag_chunks_used": len(primary_chunks),
        "uncertain_flags": draft.count("[UNCERTAIN]"),
    }

    return artifact


# =============================================================
# Episode context block builder
# =============================================================

def _build_episode_context_block(episode_context: dict) -> str:
    """
    Reconstruct the episode scope prompt block from the stored episode_context dict.

    Mirrors EpisodeContext.to_prompt_block() without requiring the dataclass import.
    Keeps the phase boundary clean — the retrieval phase stores a plain dict in the
    artifact; phases downstream reconstruct what they need from it.

    Returns an empty string when no episode was isolated, causing the
    {EPISODE_CONTEXT} placeholder in character_facts.md to resolve to empty.
    The "### Episode Scope" header remains visible in the prompt but is
    followed by a blank line, which is acceptable.
    """
    if not episode_context.get("episode_isolated"):
        return ""

    post_id = episode_context.get("post_id")
    title = episode_context.get("title", "")

    return (
        f"This question is specifically about the following TechBear "
        f"Multiverse episode:\n\n"
        f"  Title: {title}\n"
        f"  Post ID: {post_id}\n\n"
        f"Answer using ONLY details from this episode. Do not reference "
        f"events, clients, or technical details from other Multiverse "
        f"episodes. If the retrieved context contains details from other "
        f"episodes, ignore them.\n"
    )
