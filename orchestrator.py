"""
TechBear Async Pipeline — orchestrator.py

Pipeline orchestrator. Runs all phases in sequence.
Sole module permitted to import multiple pipeline phases.
All inter-phase communication passes through here.

Pipeline order:
    moderation → factual_pass → fact_critique → voice_pass →
    semantic_check → character_critique → editorial_pass →
    editorial_critique → handoff
"""

from backend.services.pipeline import moderation
from backend.services.pipeline import factual_pass
from backend.services.pipeline import fact_critique
from backend.services.pipeline import voice_pass
from backend.services.pipeline import semantic_check
from backend.services.pipeline import character_critique
from backend.services.pipeline import editorial_pass
from backend.services.pipeline import editorial_critique
from backend.services.pipeline import handoff


# =========================================================
# PIPELINE GATES
# =========================================================

def _gate(phase_name: str, artifact: dict) -> None:
    """
    Raise if a phase set a failure flag.
    Prevents downstream phases from running on bad input.
    """
    if not artifact.get("passed", True):
        reason = artifact.get("failure_reason", "no reason given")
        raise RuntimeError(f"Pipeline halted at {phase_name}: {reason}")


# =========================================================
# ORCHESTRATOR
# =========================================================

def run_pipeline(submission: dict) -> dict:
    """
    Run a submission through the full async pipeline.

    Args:
        submission: dict with keys:
            id, attendee_name, question, source, expected_scope

    Returns:
        completed artifact dict ready for human review handoff
    """

    # Initialize pipeline state
    artifact = {
        "submission": submission,
        "scores": {},
        "flags": {},
        "drafts": {},
        "passed": True,
        "failure_reason": None,
    }

    # ── Phase 1: Moderation ───────────────────────────────
    artifact = moderation.run(artifact)
    _gate("moderation", artifact)

    # ── Phase 2: Factual pass ─────────────────────────────
    artifact = factual_pass.run(artifact)
    _gate("factual_pass", artifact)

    # ── Phase 3: Fact + safety critique ───────────────────
    artifact = fact_critique.run(artifact)
    _gate("fact_critique", artifact)

    # ── Phase 4: Voice rewrite ────────────────────────────
    artifact = voice_pass.run(artifact)
    _gate("voice_pass", artifact)

    # ── Phase 5: Semantic fidelity check ──────────────────
    artifact = semantic_check.run(artifact)
    _gate("semantic_check", artifact)

    # ── Phase 6: Character fidelity critique ──────────────
    artifact = character_critique.run(artifact)
    _gate("character_critique", artifact)

    # ── Phase 7: Editorial annotation ────────────────────
    artifact = editorial_pass.run(artifact)
    _gate("editorial_pass", artifact)

    # ── Phase 8: Editorial critique ───────────────────────
    artifact = editorial_critique.run(artifact)
    _gate("editorial_critique", artifact)

    # ── Phase 9: Human review handoff ────────────────────
    artifact = handoff.run(artifact)

    return artifact
