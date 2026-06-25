"""
TechBear Async Pipeline — orchestrator.py

Pipeline orchestrator. Runs all phases in sequence.
Sole module permitted to import multiple pipeline phases.
All inter-phase communication passes through the artifact dict.

Pipeline order:
    retrieval (pre-pipeline) →
    moderation → factual_pass → fact_critique →
    educational_pass → voice_pass →
    semantic_check → character_critique → editorial_pass →
    editorial_critique → educational_critique → handoff

Loop handling:
    If fact_critique recommends "loop_factual_pass", re-run factual_pass
    up to FACTUAL_LOOP_CAP times before escalating to human review.
"""

import os

from backend.services.pipeline import moderation
from backend.services.pipeline import factual_pass
from backend.services.pipeline import fact_critique
from backend.services.pipeline import educational_pass
from backend.services.pipeline import voice_pass
from backend.services.pipeline import semantic_check
from backend.services.pipeline import character_critique
from backend.services.pipeline import editorial_pass
from backend.services.pipeline import editorial_critique
from backend.services.pipeline import educational_critique
from backend.services.pipeline import handoff
from backend.services.rag import rag as rag_service

# Max times to retry factual_pass on a loopable critique result
FACTUAL_LOOP_CAP = int(os.getenv("FACTUAL_LOOP_CAP", "2"))


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
# RETRIEVAL (pre-pipeline)
# =========================================================

def _retrieve(artifact: dict) -> dict:
    """
    Dual-RAG retrieval: runs before the generation pipeline.
    Populates artifact["retrieval"] with facts and voice chunks.
    Non-fatal: pipeline continues with empty context and a flag set.
    """
    question = artifact["submission"].get("question", "")

    try:
        facts = rag_service.retrieve_facts(question)
        voice = rag_service.retrieve_voice(question)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # ChromaDB raises varied exceptions (connection, dimension, missing collection)
        # Intentionally broad — pipeline degrades gracefully with empty retrieval
        facts = []
        voice = []
        artifact.setdefault("flags", {})["retrieval_error"] = str(exc)

    artifact["retrieval"] = {
        "facts": facts,
        "voice": voice,
    }
    return artifact


# =========================================================
# ORCHESTRATOR
# =========================================================

def run_pipeline(submission: dict) -> dict:
    """
    Run a submission through the full async pipeline.

    Args:
        submission: dict with keys:
            id, attendee_name, question, source, expected_scope,
            rolling_context (optional), conversation_depth (optional)

    Returns:
        completed artifact dict ready for human review handoff
    """

    # Initialize pipeline state
    artifact = {
        "submission": submission,
        "scores": {},
        "flags": {},
        "drafts": {},
        "retrieval": {},
        "passed": True,
        "failure_reason": None,
        "loop_counts": {},
    }

    # ── Retrieval (pre-pipeline) ──────────────────────────
    artifact = _retrieve(artifact)

    # ── Phase 1: Moderation ───────────────────────────────
    artifact = moderation.run(artifact)
    _gate("moderation", artifact)

    # ── Phase 2 + Loop: Factual pass ─────────────────────
    factual_loop_count = 0
    while True:
        artifact = factual_pass.run(artifact)
        _gate("factual_pass", artifact)

        # ── Phase 3: Fact + safety critique ───────────────
        artifact = fact_critique.run(artifact)

        loop_requested = artifact.get("flags", {}).pop(
            "fact_critique_loop_requested", False
        )
        if loop_requested and factual_loop_count < FACTUAL_LOOP_CAP:
            factual_loop_count += 1
            artifact["loop_counts"]["factual"] = factual_loop_count
            artifact["drafts"].pop("factual", None)
            continue

        _gate("fact_critique", artifact)
        break

    artifact["loop_counts"]["factual"] = factual_loop_count

    # ── Phase 4: Educational structuring ─────────────────
    artifact = educational_pass.run(artifact)
    _gate("educational_pass", artifact)

    # ── Phase 5: Voice rewrite ────────────────────────────
    artifact = voice_pass.run(artifact)
    _gate("voice_pass", artifact)

    # ── Phase 6: Semantic fidelity check ──────────────────
    artifact = semantic_check.run(artifact)
    _gate("semantic_check", artifact)

    # ── Phase 7: Character fidelity critique ──────────────
    artifact = character_critique.run(artifact)
    _gate("character_critique", artifact)

    # ── Phase 8: Editorial annotation ────────────────────
    artifact = editorial_pass.run(artifact)
    _gate("editorial_pass", artifact)

    # ── Phase 9: Editorial critique ───────────────────────
    artifact = editorial_critique.run(artifact)
    _gate("editorial_critique", artifact)

    # ── Phase 10: Educational effectiveness critique ──────
    artifact = educational_critique.run(artifact)
    # Non-blocking — like editorial_critique, never halts pipeline

    # ── Phase 11: Human review handoff ───────────────────
    artifact = handoff.run(artifact)

    return artifact
