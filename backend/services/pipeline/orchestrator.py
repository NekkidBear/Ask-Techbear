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
    _run_phase_with_retry() implements a batch-cohort retry pattern.
    fact_critique may signal "loop_factual_pass" → retries factual_pass
        up to FACTUAL_LOOP_CAP times before escalating.
    character_critique may signal "loop_voice_pass" → retries voice_pass
        up to VOICE_LOOP_CAP times before escalating.
    All loop counts are recorded in artifact["loop_counts"] for calibration.
"""

import logging
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

logger = logging.getLogger(__name__)

# Max retry attempts per loop pair (does not count the initial run)
FACTUAL_LOOP_CAP = int(os.getenv("FACTUAL_LOOP_CAP", "2"))
VOICE_LOOP_CAP = int(os.getenv("VOICE_LOOP_CAP", "2"))


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
    Routed RAG retrieval: runs before the generation pipeline.
    Reads retrieval_mode from submission (set by moderation) to route
    to the appropriate collection(s).
    Non-fatal: pipeline continues with empty context and a flag set.
    """
    question = artifact["submission"].get("question", "")
    retrieval_mode = artifact["submission"].get("retrieval_mode", "factual")

    try:
        chunks = rag_service.retrieve_for_mode(question, retrieval_mode)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # ChromaDB raises varied exceptions (connection, dimension, missing collection)
        # Intentionally broad — pipeline degrades gracefully with empty retrieval
        chunks = {"facts": [], "voice": [], "lore": []}
        artifact.setdefault("flags", {})["retrieval_error"] = str(exc)

    artifact["retrieval"] = {
        "facts": chunks.get("facts", []),
        "voice": chunks.get("voice", []),
        "lore": chunks.get("lore", []),
        "retrieval_mode": retrieval_mode,
    }
    return artifact


# =========================================================
# RETRY FRAMEWORK
# =========================================================

def _run_phase_with_retry(
    artifact: dict,
    *,
    generation_phase,
    generation_phase_name: str,
    critique_phase,
    critique_phase_name: str,
    loop_flag: str,
    draft_key: str,
    loop_count_key: str,
    loop_cap: int,
    notify: callable,
) -> dict:
    """
    Batch-cohort retry pattern: run a generation phase, then its critique.
    If the critique signals a retry (via artifact["flags"][loop_flag]),
    clear the draft and re-run the generation phase, up to loop_cap times.

    All loop counts are persisted to artifact["loop_counts"][loop_count_key]
    for calibration analysis — failures are training signal, not just noise.

    Args:
        artifact:              pipeline artifact dict (mutated in place)
        generation_phase:      module with a run(artifact) -> artifact function
        generation_phase_name: label used in notify() calls and logging
        critique_phase:        module with a run(artifact) -> artifact function
        critique_phase_name:   label used in notify() calls
        loop_flag:             artifact["flags"] key the critique sets to signal retry
        draft_key:             artifact["drafts"] key to clear before each retry
        loop_count_key:        artifact["loop_counts"] key to record retry depth
        loop_cap:              maximum number of retries (not counting initial run)
        notify:                callable(stage_name: str) for progress reporting

    Returns:
        artifact (same object, mutated)

    Raises:
        RuntimeError via _gate() if a phase sets passed=False
    """
    loop_count = 0

    while True:
        stage_label = (
            generation_phase_name
            if loop_count == 0
            else f"{generation_phase_name} (retry {loop_count})"
        )
        notify(stage_label)
        artifact = generation_phase.run(artifact)
        _gate(generation_phase_name, artifact)

        notify(critique_phase_name)
        artifact = critique_phase.run(artifact)

        loop_requested = artifact.get("flags", {}).pop(loop_flag, False)

        if loop_requested and loop_count < loop_cap:
            loop_count += 1
            artifact["loop_counts"][loop_count_key] = loop_count
            artifact["drafts"].pop(draft_key, None)
            logger.info(
                "%s retry %d/%d requested by %s",
                generation_phase_name, loop_count, loop_cap, critique_phase_name,
            )
            continue

        if loop_requested and loop_count >= loop_cap:
            # Cap reached — escalate rather than loop indefinitely
            logger.warning(
                "%s loop cap (%d) reached — escalating to human review",
                generation_phase_name, loop_cap,
            )
            artifact["passed"] = False
            artifact["failure_reason"] = (
                f"{critique_phase_name}: loop cap ({loop_cap}) reached "
                f"after {loop_count} retries — escalating to human review"
            )

        _gate(critique_phase_name, artifact)
        break

    artifact["loop_counts"][loop_count_key] = loop_count
    return artifact


# =========================================================
# ORCHESTRATOR
# =========================================================

def run_pipeline(
    submission: dict,
    on_stage: callable = None,
) -> dict:
    """
    Run a submission through the full async pipeline.

    Args:
        submission: dict with keys:
            id, attendee_name, question, source, expected_scope,
            rolling_context (optional), conversation_depth (optional)
        on_stage: optional callable(stage_name: str) called before each phase.
            Used by the test harness for progress output. No-op in production.

    Returns:
        completed artifact dict ready for human review handoff
    """

    def _notify(stage: str) -> None:
        if on_stage is not None:
            on_stage(stage)

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
    _notify("retrieval")
    artifact = _retrieve(artifact)

    # ── Phase 1: Moderation ───────────────────────────────
    _notify("moderation")
    artifact = moderation.run(artifact)
    _gate("moderation", artifact)

    # ── Phases 2–3: Factual pass + fact critique (with retry) ──
    artifact = _run_phase_with_retry(
        artifact,
        generation_phase=factual_pass,
        generation_phase_name="factual_pass",
        critique_phase=fact_critique,
        critique_phase_name="fact_critique",
        loop_flag="fact_critique_loop_requested",
        draft_key="factual",
        loop_count_key="factual",
        loop_cap=FACTUAL_LOOP_CAP,
        notify=_notify,
    )

    # ── Phase 4: Educational structuring ─────────────────
    _notify("educational_pass")
    artifact = educational_pass.run(artifact)
    _gate("educational_pass", artifact)

    # ── Phases 5–7: Voice pass + semantic check + character critique (with retry) ──
    #
    # The voice retry loop wraps all three downstream checks so that a
    # character_critique retry re-runs the voice pass with a fresh draft,
    # then re-checks semantic fidelity and character fidelity on the new draft.
    # semantic_check is non-halting on its own but is re-run as part of the
    # cohort so the retry artifacts are consistent.
    voice_loop_count = 0
    while True:
        # Voice pass
        voice_stage = (
            "voice_pass"
            if voice_loop_count == 0
            else f"voice_pass (retry {voice_loop_count})"
        )
        _notify(voice_stage)
        artifact = voice_pass.run(artifact)
        _gate("voice_pass", artifact)

        # Semantic fidelity check (non-halting — always continues)
        _notify("semantic_check")
        artifact = semantic_check.run(artifact)

        # Character fidelity critique
        _notify("character_critique")
        artifact = character_critique.run(artifact)

        loop_requested = artifact.get("flags", {}).pop(
            "character_critique_loop_requested", False
        )

        if loop_requested and voice_loop_count < VOICE_LOOP_CAP:
            voice_loop_count += 1
            artifact["loop_counts"]["voice"] = voice_loop_count
            artifact["drafts"].pop("voice", None)
            logger.info(
                "voice_pass retry %d/%d requested by character_critique",
                voice_loop_count, VOICE_LOOP_CAP,
            )
            continue

        if loop_requested and voice_loop_count >= VOICE_LOOP_CAP:
            logger.warning(
                "voice_pass loop cap (%d) reached — escalating to human review",
                VOICE_LOOP_CAP,
            )
            artifact["passed"] = False
            artifact["failure_reason"] = (
                f"character_critique: loop cap ({VOICE_LOOP_CAP}) reached "
                f"after {voice_loop_count} retries — escalating to human review"
            )

        _gate("character_critique", artifact)
        break

    artifact["loop_counts"]["voice"] = voice_loop_count

    # ── Phase 8: Editorial annotation ────────────────────
    _notify("editorial_pass")
    artifact = editorial_pass.run(artifact)
    _gate("editorial_pass", artifact)

    # ── Phase 9: Editorial critique ───────────────────────
    _notify("editorial_critique")
    artifact = editorial_critique.run(artifact)
    _gate("editorial_critique", artifact)

    # ── Phase 10: Educational effectiveness critique ──────
    _notify("educational_critique")
    artifact = educational_critique.run(artifact)
    # Non-blocking — never halts pipeline

    # ── Phase 11: Human review handoff ───────────────────
    _notify("handoff")
    artifact = handoff.run(artifact)

    return artifact
