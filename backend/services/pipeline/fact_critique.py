"""
TechBear Async Pipeline — fact_critique.py

Fact + safety critique.
Model: mistral:latest — analytical prompt, structured JSON output.
Sits between factual_pass and voice_pass.

If pass_recommendation is "loop_factual_pass", sets a flag that the
orchestrator reads to re-run factual_pass (up to FACTUAL_LOOP_CAP times).
If "escalate_human", sets artifact["passed"] = False.

Checks:
    - Technical accuracy against retrieved fact chunks
    - Hallucinations or unsupported claims
    - Missing critical information
    - Safety / dangerous advice
    - Intent-level issues that slipped past moderation

Output:
    artifact["scores"]["fact_critique"] — structured critique result
    artifact["flags"]["fact_critique"]  — list of specific flags
    artifact["passed"]                  — False if critique escalates
"""

import json
import os

import requests
from requests.exceptions import RequestException

# =============================================================
# Configuration
# =============================================================

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/chat"
CRITIQUE_MODEL = os.getenv("CRITIQUE_MODEL", "mistral:latest")

UNCERTAIN_FAIL_THRESHOLD = 3
ACCURACY_PASS_THRESHOLD = 6
SAFETY_PASS_THRESHOLD = 8


# =============================================================
# Helpers
# =============================================================

def _format_sources(chunks: list[dict]) -> str:
    if not chunks:
        return "(No RAG sources available — assess accuracy against general IT knowledge.)"
    return "\n\n".join(
        f"[SOURCE {i + 1}]\n{c['text']}"
        for i, c in enumerate(chunks)
    )


def _build_critique_messages(factual_draft: str, fact_chunks: list[dict]) -> list[dict]:
    sources_block = _format_sources(fact_chunks)

    system = (
        "You are a technical accuracy and safety auditor for an AI editorial pipeline. "
        "You receive a plain-language technical draft and the source material it should be "
        "grounded in. Your job is to identify factual errors, hallucinations, missing safety "
        "warnings, and dangerous or unsupported claims. "
        "Output ONLY valid JSON. No preamble. No markdown fences."
    )

    user = f"""Review this technical draft against the provided sources.

DRAFT TO REVIEW:
\"\"\"
{factual_draft}
\"\"\"

REFERENCE SOURCES:
{sources_block}

Respond with this exact JSON structure:
{{
  "accuracy_score": <int 0-10>,
  "safety_score": <int 0-10>,
  "pass": <true|false>,
  "flags": [
    {{
      "type": "hallucination" | "unsupported_claim" | "missing_step" | "dangerous_advice" | "missing_safety_warning" | "intent_concern",
      "location": "<quote or description of flagged text>",
      "reason": "<why this is a problem>",
      "severity": "critical" | "moderate" | "minor"
    }}
  ],
  "summary": "<one-sentence overall assessment>",
  "pass_recommendation": "pass" | "loop_factual_pass" | "escalate_human"
}}

Scoring guide:
- accuracy_score: 10 = every claim grounded in sources, 0 = fabricated throughout
- safety_score: 10 = no dangerous advice, 0 = actively dangerous
- pass: true only if accuracy_score >= {ACCURACY_PASS_THRESHOLD} AND safety_score >= {SAFETY_PASS_THRESHOLD} AND no critical flags
- pass_recommendation: "pass" if pass=true; "loop_factual_pass" if fixable by regenerating; "escalate_human" if critical flags present
"""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_response(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(
            line for line in cleaned.splitlines()
            if not line.strip().startswith("```")
        )
    return json.loads(cleaned)


def _call_ollama(messages: list[dict]) -> str:
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": CRITIQUE_MODEL,
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
    Execute the fact + safety critique pass.

    Reads from:
        artifact["drafts"]["factual"]       — factual draft from factual_pass
        artifact["retrieval"]["facts"]       — RAG chunks used in factual pass
        artifact["scores"]["factual_pass"]  — upstream metadata (uncertain count)

    Writes to:
        artifact["scores"]["fact_critique"] — structured critique result
        artifact["flags"]["fact_critique"]  — list of specific flags
        artifact["flags"]["fact_critique_loop_requested"] — signals orchestrator loop
        artifact["passed"]                  — False if escalation recommended
        artifact["failure_reason"]          — set if passed = False
    """
    factual_draft = artifact.get("drafts", {}).get("factual", "")
    fact_chunks = artifact.get("retrieval", {}).get("facts", [])
    upstream = artifact.get("scores", {}).get("factual_pass", {})

    # Pre-check: too many [UNCERTAIN] flags from factual_pass
    uncertain_count = upstream.get("uncertain_flags", 0)
    if uncertain_count >= UNCERTAIN_FAIL_THRESHOLD:
        artifact["passed"] = False
        artifact["failure_reason"] = (
            f"fact_critique: {uncertain_count} [UNCERTAIN] flags "
            f"(threshold: {UNCERTAIN_FAIL_THRESHOLD}) — escalating to human review"
        )
        artifact.setdefault("scores", {})["fact_critique"] = {
            "status": "pre_check_fail",
            "uncertain_count": uncertain_count,
            "pass_recommendation": "escalate_human",
        }
        return artifact

    messages = _build_critique_messages(factual_draft, fact_chunks)

    try:
        raw = _call_ollama(messages)
        critique = _parse_response(raw)
    except (RequestException, json.JSONDecodeError, ValueError) as exc:
        artifact["passed"] = False
        artifact["failure_reason"] = f"fact_critique: critique model failed — {exc}"
        artifact.setdefault("scores", {})["fact_critique"] = {
            "status": "error",
            "error": str(exc),
            "pass_recommendation": "escalate_human",
        }
        return artifact

    artifact.setdefault("flags", {})["fact_critique"] = critique.get("flags", [])
    artifact.setdefault("scores", {})["fact_critique"] = {
        "status": "complete",
        "model": CRITIQUE_MODEL,
        "accuracy_score": critique.get("accuracy_score"),
        "safety_score": critique.get("safety_score"),
        "pass": critique.get("pass"),
        "summary": critique.get("summary"),
        "pass_recommendation": critique.get("pass_recommendation"),
        "flag_count": len(critique.get("flags", [])),
        "critical_flag_count": sum(
            1 for f in critique.get("flags", [])
            if f.get("severity") == "critical"
        ),
    }

    recommendation = critique.get("pass_recommendation", "escalate_human")

    if recommendation == "pass":
        pass  # artifact["passed"] stays True

    elif recommendation == "loop_factual_pass":
        # Signal the orchestrator to retry — it enforces the loop cap
        artifact["flags"]["fact_critique_loop_requested"] = True

    else:  # escalate_human or unknown
        artifact["passed"] = False
        artifact["failure_reason"] = (
            f"fact_critique: escalating to human — "
            f"accuracy={critique.get('accuracy_score')}, "
            f"safety={critique.get('safety_score')}"
        )

    return artifact
