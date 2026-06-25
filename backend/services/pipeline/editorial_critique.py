"""
TechBear Async Pipeline — editorial_critique.py

Editorial critique.
Flesch-Kincaid readability: deterministic Python (no LLM).
LLM scores: clarity (0-10), formatting compliance (0-10).
Grammar anomaly classification: possible_error vs intentional_voice.

The FK score is computed locally using syllable counting.
The LLM adds contextual clarity and formatting judgment.
Both run and their results are merged into the final critique.
"""

import json
import os
import re

import requests
from requests.exceptions import RequestException

# =============================================================
# Configuration
# =============================================================

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/chat"
EDITORIAL_CRITIQUE_MODEL = os.getenv("EDITORIAL_CRITIQUE_MODEL", "llama3.1:8b")

# FK targets per character_editorial.md
FK_TARGET_MIN = 60
FK_TARGET_MAX = 80
SENTENCE_LENGTH_FLAG = 35   # words
WORD_COUNT_MIN = 150
WORD_COUNT_MAX = 250


# =============================================================
# Flesch-Kincaid (deterministic)
# =============================================================

def _count_syllables(word: str) -> int:
    """Approximate syllable count for a word."""
    word = word.lower().strip(".,!?;:\"'()-")
    if not word:
        return 0
    # Count vowel groups as syllables
    vowels = "aeiouy"
    count = 0
    prev_vowel = False
    for char in word:
        is_vowel = char in vowels
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel
    # Silent e at end
    if word.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def _flesch_kincaid_reading_ease(text: str) -> dict:
    """
    Compute Flesch Reading Ease score.
    206.835 - 1.015*(words/sentences) - 84.6*(syllables/words)
    Higher = easier. 60-80 = standard conversational.
    """
    # Split into sentences
    sentences = re.split(r"[.!?]+", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    sentence_count = max(1, len(sentences))

    # Split into words
    words = re.findall(r"\b[a-zA-Z']+\b", text)
    word_count = max(1, len(words))

    syllable_count = sum(_count_syllables(w) for w in words)

    avg_sentence_length = word_count / sentence_count
    avg_syllables_per_word = syllable_count / word_count

    fk_score = (
        206.835
        - (1.015 * avg_sentence_length)
        - (84.6 * avg_syllables_per_word)
    )
    fk_score = round(fk_score, 1)

    # Flag long sentences
    long_sentences = []
    for s in sentences:
        w = s.split()
        if len(w) > SENTENCE_LENGTH_FLAG:
            long_sentences.append({
                "sentence": s[:80] + ("..." if len(s) > 80 else ""),
                "word_count": len(w),
            })

    return {
        "flesch_kincaid_score": fk_score,
        "target_range": f"{FK_TARGET_MIN}-{FK_TARGET_MAX}",
        "in_range": FK_TARGET_MIN <= fk_score <= FK_TARGET_MAX,
        "sentence_count": sentence_count,
        "word_count": word_count,
        "avg_sentence_length": round(avg_sentence_length, 1),
        "long_sentences": long_sentences,
    }


# =============================================================
# LLM editorial critique
# =============================================================

def _build_critique_messages(voice_draft: str, fk_result: dict) -> list[dict]:
    system = (
        "You are an editorial critic for an AI content pipeline. "
        "You evaluate a TechBear response for clarity, formatting compliance, "
        "and grammar anomaly classification. "
        "Output ONLY valid JSON. No preamble. No markdown fences."
    )

    user = f"""Evaluate this TechBear draft for clarity and formatting.

DRAFT:
\"\"\"
{voice_draft}
\"\"\"

READABILITY METRICS (already computed):
- Flesch-Kincaid Reading Ease: {fk_result['flesch_kincaid_score']} (target: {fk_result['target_range']}, in range: {fk_result['in_range']})
- Word count: {fk_result['word_count']} (target: {WORD_COUNT_MIN}-{WORD_COUNT_MAX})
- Avg sentence length: {fk_result['avg_sentence_length']} words
- Long sentences flagged: {len(fk_result['long_sentences'])}

FORMATTING RULES:
- No markdown headers (this is spoken word, not a document)
- No numbered lists (use prose or TechBear rhetorical structures)
- Em-dashes and ALL CAPS single words are permitted
- Max 2 parenthetical asides per response

GRAMMAR NOTE: TechBear's intentional voice includes fragments, comma splices for rhythm,
starting sentences with And/But, and second-person mid-answer address.
Classify these as intentional_voice, not possible_error.

Respond with this exact JSON structure:
{{
  "clarity_score": <int 0-10>,
  "formatting_score": <int 0-10>,
  "grammar_anomalies": [
    {{
      "text": "<quoted text>",
      "classification": "possible_error" | "intentional_voice",
      "reason": "<brief explanation>"
    }}
  ],
  "formatting_violations": [
    {{
      "type": "markdown_header" | "numbered_list" | "excessive_parentheticals" | "other",
      "location": "<quoted text>",
      "severity": "critical" | "moderate" | "minor"
    }}
  ],
  "summary": "<one-sentence editorial assessment>",
  "overall_pass": <true|false>
}}

overall_pass: true if clarity >= 7, formatting >= 7, no critical formatting violations
"""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _call_ollama(messages: list[dict]) -> str:
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": EDITORIAL_CRITIQUE_MODEL,
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
    Execute the editorial critique phase.

    Reads from:
        artifact["drafts"]["editorial"]  — editorial draft (same as voice draft text)

    Writes to:
        artifact["scores"]["editorial_critique"] — combined FK + LLM critique
        artifact["flags"]["editorial_critique"]  — grammar anomalies + formatting violations
        artifact["passed"]                       — editorial critique is non-blocking
                                                   (sets passed=False only on hard error)
    """
    editorial_draft = artifact.get("drafts", {}).get("editorial", "")

    # Fall back to voice draft if editorial pass errored
    if not editorial_draft:
        editorial_draft = artifact.get("drafts", {}).get("voice", "")

    if not editorial_draft:
        artifact.setdefault("scores", {})["editorial_critique"] = {
            "status": "skipped",
            "reason": "no draft available",
        }
        return artifact

    # ── Deterministic FK score (always runs) ──────────────
    fk_result = _flesch_kincaid_reading_ease(editorial_draft)

    # ── LLM editorial critique ─────────────────────────────
    messages = _build_critique_messages(editorial_draft, fk_result)

    try:
        raw = _call_ollama(messages)
        llm_result = _parse_response(raw)
    except (RequestException, json.JSONDecodeError, ValueError) as exc:
        # LLM failure is non-fatal — FK result still recorded
        artifact.setdefault("scores", {})["editorial_critique"] = {
            "status": "partial",
            "flesch_kincaid": fk_result,
            "llm_error": str(exc),
        }
        return artifact

    # Merge results
    all_flags = {
        "grammar_anomalies": llm_result.get("grammar_anomalies", []),
        "formatting_violations": llm_result.get("formatting_violations", []),
        "long_sentences": fk_result.get("long_sentences", []),
    }

    artifact.setdefault("flags", {})["editorial_critique"] = all_flags
    artifact.setdefault("scores", {})["editorial_critique"] = {
        "status": "complete",
        "model": EDITORIAL_CRITIQUE_MODEL,
        "flesch_kincaid": fk_result,
        "clarity_score": llm_result.get("clarity_score"),
        "formatting_score": llm_result.get("formatting_score"),
        "overall_pass": llm_result.get("overall_pass"),
        "summary": llm_result.get("summary"),
        "grammar_anomaly_count": len(llm_result.get("grammar_anomalies", [])),
        "possible_error_count": sum(
            1 for a in llm_result.get("grammar_anomalies", [])
            if a.get("classification") == "possible_error"
        ),
        "formatting_violation_count": len(llm_result.get("formatting_violations", [])),
    }

    # Editorial critique is intentionally non-blocking —
    # all flags go to human review, writer decides
    return artifact
