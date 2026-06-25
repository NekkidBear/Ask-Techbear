"""
TechBear Async Pipeline — semantic_check.py

Semantic fidelity check.
Model: mistral:latest — analytical prompt, not creative.
Diffs the fact artifact vs the voice artifact for material changes.

This is the rephrase-only enforcement gate. The voice pass is
instructed not to add or remove facts — this phase verifies that
constraint was actually honoured.

Flags: changed_claims[], removed_claims[], added_claims[]
Pass criteria: no material changes to factual content.
Blocks character critique if fails.
"""

import json
import os

import requests
from requests.exceptions import RequestException

# =============================================================
# Configuration
# =============================================================

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/chat"
SEMANTIC_MODEL = os.getenv("SEMANTIC_MODEL", "mistral:latest")


# =============================================================
# Helpers
# =============================================================

def _build_messages(factual_draft: str, voice_draft: str) -> list[dict]:
    system = (
        "You are a semantic fidelity auditor for an AI editorial pipeline. "
        "You receive a plain factual draft and a character-voice rewrite of that draft. "
        "Your job is to identify any factual claims that were changed, added, or removed "
        "during the voice rewrite. Style changes are acceptable. Fact changes are not. "
        "Output ONLY valid JSON. No preamble. No markdown fences."
    )

    user = f"""Compare these two drafts. The VOICE DRAFT must contain exactly the same
factual claims as the FACT DRAFT — only the style and phrasing should differ.

FACT DRAFT (source of truth):
\"\"\"
{factual_draft}
\"\"\"

VOICE DRAFT (to audit):
\"\"\"
{voice_draft}
\"\"\"

Respond with this exact JSON structure:
{{
  "pass": <true|false>,
  "changed_claims": [
    {{
      "original": "<claim from fact draft>",
      "rewritten": "<how it appears in voice draft>",
      "material": <true|false>,
      "reason": "<why this is or isn't a material change>"
    }}
  ],
  "removed_claims": [
    {{
      "claim": "<claim present in fact draft but absent from voice draft>",
      "severity": "critical" | "moderate" | "minor"
    }}
  ],
  "added_claims": [
    {{
      "claim": "<claim in voice draft not present in fact draft>",
      "severity": "critical" | "moderate" | "minor"
    }}
  ],
  "summary": "<one-sentence overall assessment>",
  "pass_recommendation": "pass" | "flag_for_review" | "escalate_human"
}}

pass: true only if no material changed_claims, no critical removed_claims, no critical added_claims
pass_recommendation: "pass" if clean; "flag_for_review" if minor issues only; "escalate_human" if critical issues
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
            "model": SEMANTIC_MODEL,
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
    Execute the semantic fidelity check.

    Reads from:
        artifact["drafts"]["factual"]  — source-of-truth fact draft
        artifact["drafts"]["voice"]    — voice rewrite to audit

    Writes to:
        artifact["scores"]["semantic_check"] — structured check result
        artifact["flags"]["semantic_check"]  — changed/removed/added claims
        artifact["passed"]                   — False if critical issues found
        artifact["failure_reason"]           — set if passed = False
    """
    factual_draft = artifact.get("drafts", {}).get("factual", "")
    voice_draft = artifact.get("drafts", {}).get("voice", "")

    if not voice_draft:
        artifact["passed"] = False
        artifact["failure_reason"] = "semantic_check: no voice draft to check"
        return artifact

    messages = _build_messages(factual_draft, voice_draft)

    try:
        raw = _call_ollama(messages)
        result = _parse_response(raw)
    except (RequestException, json.JSONDecodeError, ValueError) as exc:
        # Semantic check failure → flag but don't hard-fail; human reviews
        artifact.setdefault("scores", {})["semantic_check"] = {
            "status": "error",
            "error": str(exc),
            "pass_recommendation": "flag_for_review",
        }
        artifact.setdefault("flags", {})["semantic_check_error"] = str(exc)
        return artifact

    # Collect all flags for the handoff
    all_flags = {
        "changed_claims": result.get("changed_claims", []),
        "removed_claims": result.get("removed_claims", []),
        "added_claims": result.get("added_claims", []),
    }
    artifact.setdefault("flags", {})["semantic_check"] = all_flags

    recommendation = result.get("pass_recommendation", "flag_for_review")
    critical_removed = sum(
        1 for c in result.get("removed_claims", [])
        if c.get("severity") == "critical"
    )
    critical_added = sum(
        1 for c in result.get("added_claims", [])
        if c.get("severity") == "critical"
    )

    artifact.setdefault("scores", {})["semantic_check"] = {
        "status": "complete",
        "model": SEMANTIC_MODEL,
        "pass": result.get("pass"),
        "summary": result.get("summary"),
        "pass_recommendation": recommendation,
        "changed_claim_count": len(result.get("changed_claims", [])),
        "removed_claim_count": len(result.get("removed_claims", [])),
        "added_claim_count": len(result.get("added_claims", [])),
        "critical_removed": critical_removed,
        "critical_added": critical_added,
    }

    if recommendation == "escalate_human":
        artifact["passed"] = False
        artifact["failure_reason"] = (
            f"semantic_check: critical fact changes found — "
            f"{critical_added} added, {critical_removed} removed"
        )
    # "flag_for_review" flows through to human handoff with flags set

    return artifact
