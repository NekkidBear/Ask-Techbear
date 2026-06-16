"""
services/llm.py — Ollama integration for TechBear draft generation
Gymnarctos Studios LLC
"""

import os
from pathlib import Path

import httpx

# =============================================================
# Configuration
# =============================================================

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

CHARACTER_FILE_PATH = (
    Path(__file__).resolve().parent.parent / "character_file.md"
)


# =============================================================
# Character file loading
# =============================================================

def load_character_template() -> str:
    """
    Loads the TechBear character file from disk.
    Raises if the file is missing — we never want to silently
    fall back to a generic prompt for a live performance tool.
    """
    if not CHARACTER_FILE_PATH.exists():
        raise FileNotFoundError(
            f"Character file not found at {CHARACTER_FILE_PATH}"
        )
    return CHARACTER_FILE_PATH.read_text(encoding="utf-8")


def build_prompt(
    sanitized_question: str,
    rolling_context: str = "",
    rag_context: str = "",
) -> str:
    """
    Fills in the character file template with the live question
    and any context (rolling session history, RAG results).
    """
    template = load_character_template()

    prompt = template.replace(
        "{SANITIZED_QUESTION}", sanitized_question
    ).replace(
        "{ROLLING_CONTEXT}",
        rolling_context if rolling_context else "(No prior questions this session.)"
    ).replace(
        "{RAG_CONTEXT}",
        rag_context if rag_context else "(No specific article context retrieved for this question.)"
    )

    return prompt


# =============================================================
# Ollama call
# =============================================================

async def generate_techbear_response(
    sanitized_question: str,
    rolling_context: str = "",
    rag_context: str = "",
) -> str:
    """
    Sends the assembled prompt to Ollama and returns TechBear's
    in-character draft response.
    """
    prompt = build_prompt(sanitized_question, rolling_context, rag_context)

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.9,  # higher = more creative/sassy
                    "top_p": 0.9,
                },
            },
        )
        response.raise_for_status()
        data = response.json()
        return data.get("response", "").strip()


# =============================================================
# Quick moderation check — uses the tiny fast model
# =============================================================

async def quick_topic_check(question_text: str) -> tuple[bool, str]:
    """
    Fast moderation pass using llama3.2:1b.
    Returns (is_appropriate, reason).
    """
    moderation_prompt = f"""Is this question appropriate for a family-friendly
tech support event? Answer with only YES or NO, then a one-sentence reason.

Question: "{question_text}"
"""

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": "llama3.2:1b",
                "prompt": moderation_prompt,
                "stream": False,
                "options": {"temperature": 0.1},
            },
        )
        response.raise_for_status()
        data = response.json()
        result = data.get("response", "").strip()

    is_appropriate = result.upper().startswith("YES")
    return is_appropriate, result