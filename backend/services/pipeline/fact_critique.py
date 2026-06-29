"""
TechBear Async Pipeline — fact_critique.py

Fact + safety critique.
Model: mistral:latest — analytical prompt, structured JSON output.
Sits between factual_pass and voice_pass.

If pass_recommendation is "loop_factual_pass", sets a flag that the
orchestrator reads to re-run factual_pass (up to FACTUAL_LOOP_CAP times).
If "escalate_human", sets artifact["passed"] = False.

Two critique modes, selected by retrieval_mode:

  factual / hybrid — evaluate against real-world technical accuracy and
      retrieved fact chunks. Flags hallucinations, unsupported claims,
      missing safety warnings, and dangerous advice.

  lore / tall_tale — evaluate against TechBear canon consistency using
      retrieved lore chunks. Does NOT apply real-world accuracy standards.
      Fictional claims are valid when supported by retrieved lore. Flags
      canon contradictions, missing lore details, and character breaks.

Output:
    artifact["scores"]["fact_critique"] — structured critique result
    artifact["flags"]["fact_critique"]  — list of specific flags
    artifact["diagnostics"]["fact_critique_raw_response"] — raw LLM output
    artifact["diagnostics"]["fact_critique_parse_succeeded"] — bool
    artifact["passed"]                  — False if critique escalates
"""

import json
import os

import requests
from requests.exceptions import RequestException

# =============================================================
# Configuration
# =============================================================

OLLAMA_URL = os.getenv(
    "OLLAMA_BASE_URL", "http://localhost:11434") + "/api/chat"
CRITIQUE_MODEL = os.getenv("CRITIQUE_MODEL", "mistral:latest")

UNCERTAIN_FAIL_THRESHOLD = 3
ACCURACY_PASS_THRESHOLD = 6
SAFETY_PASS_THRESHOLD = 8


# =============================================================
# Helpers
# =============================================================

def _format_sources(chunks: list[dict], label: str = "SOURCE") -> str:
    if not chunks:
        return f"(No {label.lower()} chunks retrieved — evaluate based on general knowledge.)"
    return "\n\n".join(
        f"[{label} {i + 1}]\n{c['text']}"
        for i, c in enumerate(chunks)
    )


def _build_factual_critique_messages(
    factual_draft: str,
    fact_chunks: list[dict],
) -> list[dict]:
    """Critique prompt for factual / hybrid modes — real-world accuracy standards."""
    sources_block = _format_sources(fact_chunks, label="SOURCE")

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


def _build_lore_critique_messages(
    factual_draft: str,
    lore_chunks: list[dict],
) -> list[dict]:
    """
    Critique prompt for lore / tall_tale modes — canon consistency standards.

    Key difference from factual critique: fictional claims are VALID when supported
    by retrieved lore. Do not penalize TechBear for having met Captain Janeway,
    visited Discworld, or fixed the Millennium Falcon's hyperdrive. These are
    established TechBear canon. The question is whether the draft is CONSISTENT
    with the canon, not whether the events could occur in the real world.
    """
    lore_block = _format_sources(lore_chunks, label="LORE CHUNK")

    system = (
        "You are a canon consistency auditor for an AI character pipeline. "
        "TechBear is a fictional interdimensional IT consultant who has visited "
        "many fictional universes (Star Trek Voyager, Jurassic Park, Star Wars, "
        "Discworld, Deep Space Nine, and others). These are ESTABLISHED CANON — "
        "do not penalize the draft for containing fictional events. "
        "Your job is to check whether the draft is CONSISTENT with the retrieved "
        "lore chunks and TechBear's established character. "
        "If no lore chunks were retrieved, flag that as a retrieval gap, not a "
        "factual error in the draft. "
        "Output ONLY valid JSON. No preamble. No markdown fences."
    )

    user = f"""Review this TechBear lore draft for canon consistency.

DRAFT TO REVIEW:
\"\"\"
{factual_draft}
\"\"\"

RETRIEVED LORE CHUNKS (TechBear canon — use these as the reference):
{lore_block}

Respond with this exact JSON structure:
{{
  "accuracy_score": <int 0-10>,
  "safety_score": <int 0-10>,
  "pass": <true|false>,
  "flags": [
    {{
      "type": "canon_contradiction" | "missing_lore_detail" | "character_break" | "retrieval_gap" | "unsupported_claim",
      "location": "<quote or description of flagged text>",
      "reason": "<why this is a consistency problem>",
      "severity": "critical" | "moderate" | "minor"
    }}
  ],
  "summary": "<one-sentence overall assessment>",
  "pass_recommendation": "pass" | "loop_factual_pass" | "escalate_human"
}}

Scoring guide:
- accuracy_score: 10 = draft is fully consistent with retrieved lore, 0 = contradicts canon throughout
- safety_score: 10 = no harmful content, 0 = actively harmful (applies even in fictional framing)
- pass: true if accuracy_score >= {ACCURACY_PASS_THRESHOLD} AND safety_score >= {SAFETY_PASS_THRESHOLD} AND no critical canon contradictions
- pass_recommendation:
    "pass" if consistent with retrieved lore or lore is silent on the details
    "loop_factual_pass" if the draft denies TechBear's canon experiences (e.g. claims Janeway is fictional)
    "escalate_human" if there are critical canon contradictions or the retrieval gap is severe

IMPORTANT: If the retrieved lore chunks are empty or sparse, score accuracy_score 5
(neutral — cannot confirm or deny) and flag type "retrieval_gap". Do NOT escalate
to human solely because lore chunks were not retrieved. That is a pipeline gap, not
a content error.

IMPORTANT: If the draft states that TechBear has NOT met a character or visited a
location that IS established TechBear canon (e.g. "Captain Janeway is just a TV
character"), that is a critical canon_contradiction and pass_recommendation should
be "loop_factual_pass".
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

    Routes to lore_critique or factual_critique based on retrieval_mode.

    Reads from:
        artifact["drafts"]["factual"]       — factual draft from factual_pass
        artifact["retrieval"]["facts"]       — RAG chunks (factual mode)
        artifact["retrieval"]["lore"]        — RAG chunks (lore/tall_tale mode)
        artifact["retrieval"]["retrieval_mode"] — routing key
        artifact["scores"]["factual_pass"]  — upstream metadata (uncertain count)
        artifact["submission"]["diagnostic_mode"] — if True, capture raw LLM output

    Writes to:
        artifact["scores"]["fact_critique"] — structured critique result
        artifact["flags"]["fact_critique"]  — list of specific flags
        artifact["flags"]["fact_critique_loop_requested"] — signals orchestrator loop
        artifact["diagnostics"]["fact_critique_raw_response"] — raw LLM output
        artifact["diagnostics"]["fact_critique_parse_succeeded"] — bool
        artifact["passed"]                  — False if escalation recommended
        artifact["failure_reason"]          — set if passed = False
    """
    factual_draft = artifact.get("drafts", {}).get("factual", "")
    retrieval = artifact.get("retrieval", {})
    retrieval_mode = retrieval.get("retrieval_mode", "factual")
    upstream = artifact.get("scores", {}).get("factual_pass", {})
    diagnostic_mode = artifact.get(
        "submission", {}).get("diagnostic_mode", False)

    # Route chunks based on mode
    if retrieval_mode in ("lore", "tall_tale"):
        primary_chunks = retrieval.get("lore", [])
        is_lore_mode = True
    elif retrieval_mode == "hybrid":
        # Hybrid: check both, prefer lore for critique context
        primary_chunks = retrieval.get("lore", []) + retrieval.get("facts", [])
        is_lore_mode = False  # apply factual standards with lore context
    else:
        primary_chunks = retrieval.get("facts", [])
        is_lore_mode = False

    # Pre-check: too many [UNCERTAIN] flags from factual_pass
    # Only applies in factual mode — lore drafts are expected to be uncertain
    # about real-world details by design.
    if not is_lore_mode:
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
                "retrieval_mode": retrieval_mode,
            }
            return artifact

    # Build mode-appropriate critique messages
    if is_lore_mode:
        messages = _build_lore_critique_messages(factual_draft, primary_chunks)
    else:
        messages = _build_factual_critique_messages(
            factual_draft, primary_chunks)

    # Call Ollama
    raw = None
    try:
        raw = _call_ollama(messages)

        # Capture raw response for diagnostics before parse attempt
        if diagnostic_mode:
            artifact.setdefault("diagnostics", {})[
                "fact_critique_raw_response"] = raw
            artifact["diagnostics"]["fact_critique_parse_succeeded"] = False

        critique = _parse_response(raw)

        if diagnostic_mode:
            artifact["diagnostics"]["fact_critique_parse_succeeded"] = True

    except (RequestException, json.JSONDecodeError, ValueError) as exc:
        if diagnostic_mode and raw is not None:
            artifact.setdefault("diagnostics", {})[
                "fact_critique_raw_response"] = raw
            artifact["diagnostics"]["fact_critique_parse_succeeded"] = False

        artifact["passed"] = False
        artifact["failure_reason"] = f"fact_critique: critique model failed — {exc}"
        artifact.setdefault("scores", {})["fact_critique"] = {
            "status": "error",
            "error": str(exc),
            "retrieval_mode": retrieval_mode,
            "pass_recommendation": "escalate_human",
        }
        return artifact

    artifact.setdefault("flags", {})[
        "fact_critique"] = critique.get("flags", [])
    artifact.setdefault("scores", {})["fact_critique"] = {
        "status": "complete",
        "model": CRITIQUE_MODEL,
        "retrieval_mode": retrieval_mode,
        "critique_mode": "lore" if is_lore_mode else "factual",
        "chunks_evaluated": len(primary_chunks),
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
        artifact["flags"]["fact_critique_loop_requested"] = True

    else:  # escalate_human or unknown
        artifact["passed"] = False
        artifact["failure_reason"] = (
            f"fact_critique: escalating to human — "
            f"accuracy={critique.get('accuracy_score')}, "
            f"safety={critique.get('safety_score')}"
        )

    return artifact
