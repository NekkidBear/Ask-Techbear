"""
TechBear Async Pipeline — handoff.py

Human review handoff formatter.
Assembles complete review package and writes to disk as JSON.
This is the final pipeline stage — artifact["passed"] is not modified here.

Output structure:
    - submission: original question and metadata
    - before_draft: raw factual artifact (pre-voice)
    - after_draft: final editorial artifact (post-voice, post-editorial)
    - scores: all automated scores per phase
    - flags: all flags from all phases
    - editorial_decisions: accept/reject fields for human to complete
    - human_scores: same 0-10 rubric fields for human scoring
    - delta: automated vs human score per dimension (filled post-review)
    - pipeline_metadata: loop counts, models used, retrieval stats
"""

import json
from datetime import datetime, timezone
from pathlib import Path

HANDOFF_DIR = Path("handoff_output")

# Dimensions that receive both automated and human scoring
HUMAN_SCORE_DIMENSIONS = [
    "accuracy",
    "safety",
    "character_fidelity",
    "structure_compliance",
    "regurgitation",
    "anti_formulaic",
    "clarity",
    "formatting",
    "word_count_compliance",
    "comprehension_confidence",
    "concept_clarity",
    "analogy_quality",
    "action_clarity",
    "transfer_potential",
]


# =============================================================
# Package assembly
# =============================================================

def _build_handoff(artifact: dict) -> dict:
    submission = artifact.get("submission", {})
    drafts = artifact.get("drafts", {})
    scores = artifact.get("scores", {})
    flags = artifact.get("flags", {})

    # Editorial annotations with empty decision fields for the human UI
    editorial_annotations = flags.get("editorial_pass", [])
    editorial_decisions = [
        {
            "annotation": ann,
            "human_decision": None,   # "accept" | "reject"
            "human_note": None,
        }
        for ann in editorial_annotations
    ]

    # Empty human score fields (same 0-10 rubric as automated)
    human_scores = {dim: None for dim in HUMAN_SCORE_DIMENSIONS}

    # Delta fields — computed once human fills in scores
    delta = {dim: None for dim in HUMAN_SCORE_DIMENSIONS}

    # Distill automated scores into the matching rubric dimensions
    automated_scores = {
        "accuracy": scores.get("fact_critique", {}).get("accuracy_score"),
        "safety": scores.get("fact_critique", {}).get("safety_score"),
        "character_fidelity": scores.get("character_critique", {}).get("character_fidelity_score"),
        "structure_compliance": scores.get("character_critique", {}).get("structure_compliance_score"),
        "regurgitation": scores.get("character_critique", {}).get("regurgitation_score"),
        "anti_formulaic": scores.get("character_critique", {}).get("anti_formulaic_score"),
        "clarity": scores.get("editorial_critique", {}).get("clarity_score"),
        "formatting": scores.get("editorial_critique", {}).get("formatting_score"),
        "word_count_compliance": scores.get("character_critique", {}).get("word_count_compliance_score"),
        "comprehension_confidence": scores.get("educational_critique", {}).get("comprehension_confidence"),
        "concept_clarity": scores.get("educational_critique", {}).get("concept_clarity"),
        "analogy_quality": scores.get("educational_critique", {}).get("analogy_quality"),
        "action_clarity": scores.get("educational_critique", {}).get("action_clarity"),
        "transfer_potential": scores.get("educational_critique", {}).get("transfer_potential"),
    }

    # Flags stripped of internal pipeline signals
    clean_flags = {
        k: v for k, v in flags.items()
        if k not in ("fact_critique_loop_requested",)
    }

    return {
        "handoff_at": datetime.now(timezone.utc).isoformat(),
        "submission_id": str(submission.get("id", "unknown")),
        "submission": submission,
        "before_draft": drafts.get("factual", ""),
        "after_draft": drafts.get("editorial", drafts.get("voice", "")),
        "automated_scores": automated_scores,
        "all_phase_scores": scores,
        "flags": clean_flags,
        "editorial_decisions": editorial_decisions,
        "human_scores": human_scores,
        "delta": delta,
        "pipeline_metadata": {
            "loop_counts": artifact.get("loop_counts", {}),
            "retrieval": {
                "facts_retrieved": len(
                    artifact.get("retrieval", {}).get("facts", [])
                ),
                "voice_retrieved": len(
                    artifact.get("retrieval", {}).get("voice", [])
                ),
                "retrieval_error": flags.get("retrieval_error"),
            },
            "pipeline_passed": artifact.get("passed", True),
            "failure_reason": artifact.get("failure_reason"),
        },
    }


# =============================================================
# Phase entry point
# =============================================================

def run(artifact: dict) -> dict:
    """
    Execute the human review handoff phase.

    Writes to:
        handoff_output/handoff_{submission_id}.json — the review package
        artifact["handoff"]                          — path and status
    """
    HANDOFF_DIR.mkdir(parents=True, exist_ok=True)

    submission_id = str(
        artifact.get("submission", {}).get("id", "unknown")
    )
    output_path = HANDOFF_DIR / f"handoff_{submission_id}.json"

    handoff_package = _build_handoff(artifact)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(handoff_package, f, indent=2, ensure_ascii=False)

    artifact["handoff"] = {
        "output_path": str(output_path),
        "submission_id": submission_id,
        "status": "written",
    }

    return artifact
