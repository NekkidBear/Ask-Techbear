"""
TechBear Async Pipeline — orchestrator.py

Pipeline orchestrator. Runs all phases in sequence.
Sole module permitted to import multiple pipeline phases.
All inter-phase communication passes through the artifact dict.

Pipeline order:
    moderation → retrieval →
    factual_pass → fact_critique →
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

Logging:
    configure_logging() is called by the test harness or CLI entrypoint
    before run_pipeline() is invoked. The orchestrator does not call it
    directly — callers control verbosity.

    Import path for callers:
        from backend.services.pipeline.logging_config import configure_logging
"""

import logging
import os

from collections.abc import Callable
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
from backend.services.pipeline.episode_isolation import isolate_episode_chunks
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

    Two-stage lore retrieval (v2.8 item 8):
    Stage 1 — broad semantic search across lore collection (current behavior)
    Stage 2 — episode-targeted query filtered by dominant post_id after
               episode isolation identifies the dominant episode.
               Ensures episode-specific facts are available to the factual
               pass regardless of semantic similarity rank.

    Episode isolation runs between Stage 1 and Stage 2:
    If a dominant Multiverse episode is present in the Stage 1 lore chunks,
    contaminating chunks from other episodes are removed before Stage 2 runs.
    Stage 2 results are merged with Stage 1, deduplicated by chunk text.

    Non-fatal: pipeline continues with empty context and a flag set.
    """
    question = artifact["submission"].get("question", "")
    retrieval_mode = artifact["submission"].get("retrieval_mode", "factual")

    logger.debug(
        "retrieval | question=%r retrieval_mode=%r submission_keys=%s",
        question,
        retrieval_mode,
        sorted(artifact["submission"].keys()),
    )

    try:
        chunks = rag_service.retrieve_for_mode(question, retrieval_mode)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # ChromaDB raises varied exceptions (connection, dimension, missing collection)
        # Intentionally broad — pipeline degrades gracefully with empty retrieval
        logger.warning(
            "retrieval | failed — pipeline will continue with empty context | "
            "error=%r retrieval_mode=%r question=%r",
            str(exc),
            retrieval_mode,
            question,
        )
        chunks = {"facts": [], "voice": [], "lore": []}
        artifact.setdefault("flags", {})["retrieval_error"] = str(exc)

    lore_chunks = chunks.get("lore", [])

    # Stage 1 complete — run episode isolation to identify dominant episode
    # and remove cross-episode contaminating chunks.
    lore_chunks, episode_context = isolate_episode_chunks(lore_chunks)

    # Stage 2 — episode-targeted secondary retrieval.
    # Only runs in lore/tall_tale modes when a dominant episode was identified.
    # Pulls all chunks for the dominant post_id to supplement Stage 1 results
    # with full episode coverage regardless of semantic similarity ranking.
    if (
        retrieval_mode in ("lore", "tall_tale")
        and episode_context.episode_isolated
        and episode_context.post_id is not None
    ):
        stage2_chunks = rag_service.retrieve_lore_targeted(
            question, episode_context.post_id
        )
        pre_merge_count = len(lore_chunks)
        lore_chunks = rag_service.merge_lore_chunks(lore_chunks, stage2_chunks)
        logger.debug(
            "retrieval | stage2 complete | post_id=%d "
            "stage1=%d stage2=%d merged=%d",
            episode_context.post_id,
            pre_merge_count,
            len(stage2_chunks),
            len(lore_chunks),
        )

    artifact["retrieval"] = {
        "facts": chunks.get("facts", []),
        "voice": chunks.get("voice", []),
        "lore": lore_chunks,
        "retrieval_mode": retrieval_mode,
        "episode_context": episode_context.to_dict(),
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
    notify: Callable[[str], None],
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
    # Item 3: retry history — records score before/after and trigger reason
    # for each retry. Stored in artifact["retry_history"][loop_count_key].
    retry_history: list[dict] = []

    while True:
        stage_label = (
            generation_phase_name
            if loop_count == 0
            else f"{generation_phase_name} (retry {loop_count})"
        )
        notify(stage_label)

        # Capture pre-retry score for history (attempt 2+)
        pre_scores = None
        if loop_count > 0:
            fc = artifact.get("scores", {}).get("fact_critique", {})
            pre_scores = {
                "accuracy": fc.get("accuracy_score"),
                "safety": fc.get("safety_score"),
            }

        artifact = generation_phase.run(artifact)
        _gate(generation_phase_name, artifact)

        notify(critique_phase_name)
        artifact = critique_phase.run(artifact)

        loop_requested = artifact.get("flags", {}).pop(loop_flag, False)

        if loop_requested and loop_count < loop_cap:
            loop_count += 1
            artifact["loop_counts"][loop_count_key] = loop_count

            # Capture post-critique scores before clearing the draft
            fc_after = artifact.get("scores", {}).get("fact_critique", {})
            history_entry: dict = {
                "retry": loop_count,
                "trigger": critique_phase_name,
                "accuracy_before": pre_scores.get("accuracy") if pre_scores else None,
                "accuracy_after": fc_after.get("accuracy_score"),
                "safety_before": pre_scores.get("safety") if pre_scores else None,
                "safety_after": fc_after.get("safety_score"),
                "flags": [
                    f.get("type") for f in
                    artifact.get("flags", {}).get("fact_critique", [])
                    if isinstance(f, dict)
                ],
            }
            retry_history.append(history_entry)

            logger.info(
                "%s retry %d/%d | trigger=%s accuracy=%s→%s",
                generation_phase_name,
                loop_count,
                loop_cap,
                critique_phase_name,
                history_entry["accuracy_before"],
                history_entry["accuracy_after"],
            )

            artifact["drafts"].pop(draft_key, None)
            continue

        if loop_requested and loop_count >= loop_cap:
            # Cap reached — escalate rather than loop indefinitely
            logger.warning(
                "%s loop cap reached | cap=%d retries=%d — escalating to human review",
                generation_phase_name,
                loop_cap,
                loop_count,
            )
            artifact["passed"] = False
            artifact["failure_reason"] = (
                f"{critique_phase_name}: loop cap ({loop_cap}) reached "
                f"after {loop_count} retries — escalating to human review"
            )

        _gate(critique_phase_name, artifact)
        break

    artifact["loop_counts"][loop_count_key] = loop_count
    # Store retry history in artifact for test harness surfacing
    if retry_history:
        artifact.setdefault("retry_history", {})[
            loop_count_key] = retry_history
    return artifact


# =========================================================
# ORCHESTRATOR
# =========================================================

def run_pipeline(
    submission: dict,
    on_stage: Callable[[str], None] | None = None,
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
        logger.debug("orchestrator | phase_start='%s'", stage)
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
        "retry_history": {},
    }

    # ── Phase 1: Moderation ───────────────────────────────
    _notify("moderation")
    artifact = moderation.run(artifact)
    _gate("moderation", artifact)

    # ── Retrieval (post-moderation) ────────────────────────
    _notify("retrieval")
    artifact = _retrieve(artifact)

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
                "voice_pass retry %d/%d | trigger=character_critique",
                voice_loop_count,
                VOICE_LOOP_CAP,
            )
            continue

        if loop_requested and voice_loop_count >= VOICE_LOOP_CAP:
            logger.warning(
                "voice_pass loop cap reached | cap=%d retries=%d "
                "— escalating to human review",
                VOICE_LOOP_CAP,
                voice_loop_count,
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

    logger.info(
        "orchestrator | pipeline_complete | "
        "passed=%s failure_reason=%r loop_counts=%s",
        artifact.get("passed"),
        artifact.get("failure_reason"),
        artifact.get("loop_counts"),
    )

    return artifact
