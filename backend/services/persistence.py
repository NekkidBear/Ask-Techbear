"""
backend/services/persistence.py — Pipeline Run Persistence
Ask TechBear — Gymnarctos Studios LLC

Writes completed pipeline artifacts into the v2.6 evaluation schema:
    PipelineRun      — one row per pipeline execution
    PipelineArtifact — generated text artifacts per phase
    LLMScore         — automated scores per phase dimension

Called by the orchestrator after handoff.run() completes.
Does not block the pipeline — failures are logged, not raised.

Score mapping:
    Each phase that produces scores gets one LLMScore row per
    numeric dimension. The full raw output is stored in raw_output
    for audit and recalibration purposes.

Usage:
    from backend.services.persistence import persist_pipeline_run
    await persist_pipeline_run(artifact, question_id, pipeline_version="v2.6")
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models_v26 import LLMScore, PipelineArtifact, PipelineRun

logger = logging.getLogger(__name__)


# =============================================================
# Score extraction — maps artifact scores dict to LLMScore rows
# =============================================================

# Maps phase name → list of (score_name, score_key) tuples
# score_key is the key in the phase's scores dict
SCORE_MAP: dict[str, list[tuple[str, str]]] = {
    "fact_critique": [
        ("accuracy_score", "accuracy_score"),
        ("safety_score", "safety_score"),
    ],
    "educational_pass": [
        ("markers_present", "markers_present"),
    ],
    "voice_pass": [
        ("word_count", "word_count"),
        ("word_count_ok", "word_count_ok"),
    ],
    "semantic_check": [
        ("pass", "pass"),
        ("changed_claim_count", "changed_claim_count"),
        ("removed_claim_count", "removed_claim_count"),
        ("added_claim_count", "added_claim_count"),
    ],
    "character_critique": [
        ("character_fidelity_score", "character_fidelity_score"),
        ("regurgitation_score", "regurgitation_score"),
        ("structure_compliance_score", "structure_compliance_score"),
        ("word_count_compliance_score", "word_count_compliance_score"),
        ("anti_formulaic_score", "anti_formulaic_score"),
    ],
    "editorial_critique": [
        ("clarity_score", "clarity_score"),
        ("formatting_score", "formatting_score"),
        ("flesch_kincaid_score", "flesch_kincaid.flesch_kincaid_score"),
    ],
    "educational_critique": [
        ("comprehension_confidence", "comprehension_confidence"),
        ("concept_clarity", "concept_clarity"),
        ("analogy_quality", "analogy_quality"),
        ("action_clarity", "action_clarity"),
        ("transfer_potential", "transfer_potential"),
    ],
}


def _get_nested(d: dict, dotted_key: str) -> Any:
    """Resolve a dotted key path into a nested dict."""
    keys = dotted_key.split(".")
    val = d
    for k in keys:
        if not isinstance(val, dict):
            return None
        val = val.get(k)
    return val


def _extract_scores(
    phase: str,
    phase_scores: dict,
    pipeline_run_id: uuid.UUID,
) -> list[LLMScore]:
    """Extract LLMScore rows from a single phase's scores dict."""
    rows = []
    dimensions = SCORE_MAP.get(phase, [])
    model = phase_scores.get("model")
    pass_rec = phase_scores.get("pass_recommendation")

    for score_name, score_key in dimensions:
        value = _get_nested(phase_scores, score_key)
        if value is None:
            continue

        # Convert bool to numeric for consistent storage
        if isinstance(value, bool):
            value = 1.0 if value else 0.0

        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue

        rows.append(
            LLMScore(
                id=uuid.uuid4(),
                pipeline_run_id=pipeline_run_id,
                score_type=phase,
                score_name=score_name,
                score_value=numeric_value,
                pass_recommendation=pass_rec,
                model=model,
                raw_output=phase_scores,
            )
        )

    return rows


# =============================================================
# Artifact extraction — maps artifact drafts to PipelineArtifact rows
# =============================================================

ARTIFACT_MAP = {
    "factual_draft": "factual_draft",
    "educational_structure": "educational_draft",
    "voice": "voice_draft",
    "editorial": "final_draft",
}


def _extract_artifacts(
    artifact: dict,
    pipeline_run_id: uuid.UUID,
) -> list[PipelineArtifact]:
    """Extract PipelineArtifact rows from the pipeline artifact dict."""
    rows = []
    drafts = artifact.get("drafts", {})

    for draft_key, artifact_type in ARTIFACT_MAP.items():
        content = drafts.get(draft_key)
        if not content:
            continue
        rows.append(
            PipelineArtifact(
                id=uuid.uuid4(),
                pipeline_run_id=pipeline_run_id,
                artifact_type=artifact_type,
                content=content,
                artifact_metadata={
                    "word_count": len(content.split()),
                    "phase_scores": artifact.get("scores", {}).get(
                        draft_key.replace("_draft", "_pass"), {}
                    ),
                },
            )
        )

    # Store the moderation result as an artifact too
    moderation = artifact.get("scores", {}).get("moderation")
    if moderation:
        rows.append(
            PipelineArtifact(
                id=uuid.uuid4(),
                pipeline_run_id=pipeline_run_id,
                artifact_type="moderation_result",
                content=moderation.get("decision", ""),
                artifact_metadata=moderation,
            )
        )

    return rows


# =============================================================
# Main persistence entry point
# =============================================================

async def persist_pipeline_run(
    artifact: dict,
    question_id: int,
    db: AsyncSession,
    pipeline_version: str = "v2.6",
    run_label: str | None = None,
) -> uuid.UUID | None:
    """
    Persist a completed pipeline artifact to PostgreSQL.

    Writes:
        - One PipelineRun row
        - PipelineArtifact rows for each generated draft
        - LLMScore rows for each numeric score dimension

    Args:
        artifact:         completed artifact dict from orchestrator
        question_id:      questions.id FK (integer)
        db:               async SQLAlchemy session
        pipeline_version: version label e.g. "v2.6"
        run_label:        optional human label e.g. "baseline"

    Returns:
        pipeline_run.id (UUID) if successful, None if failed.
        Failures are logged but not raised — pipeline output is not
        blocked by persistence errors.
    """
    run_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    try:
        # Determine run status from artifact
        passed = artifact.get("passed", True)
        failure_reason = artifact.get("failure_reason")
        status = "complete" if passed else "halted"

        # Build model_config snapshot from scores metadata
        scores = artifact.get("scores", {})
        model_config = {
            phase: scores[phase].get("model")
            for phase in scores
            if isinstance(scores[phase], dict) and "model" in scores[phase]
        }

        # Build retrieval_config snapshot
        retrieval = artifact.get("retrieval", {})
        retrieval_config = {
            "facts_retrieved": len(retrieval.get("facts", [])),
            "voice_retrieved": len(retrieval.get("voice", [])),
            "retrieval_error": artifact.get("flags", {}).get("retrieval_error"),
        }

        # Create the pipeline run record
        pipeline_run = PipelineRun(
            id=run_id,
            question_id=question_id,
            pipeline_version=pipeline_version,
            run_label=run_label,
            status=status,
            started_at=artifact.get("submission", {}).get("submitted_at", now),
            completed_at=now,
            model_config=model_config,
            retrieval_config=retrieval_config,
            error_message=failure_reason,
        )
        db.add(pipeline_run)

        # Create artifact rows
        for artifact_row in _extract_artifacts(artifact, run_id):
            db.add(artifact_row)

        # Create score rows
        for phase, phase_scores in scores.items():
            if not isinstance(phase_scores, dict):
                continue
            for score_row in _extract_scores(phase, phase_scores, run_id):
                db.add(score_row)

        await db.flush()
        logger.info(
            "Persisted pipeline run %s for question_id=%s (%s)",
            run_id,
            question_id,
            status,
        )
        return run_id

    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Persistence failure must not block pipeline output.
        # The handoff JSON is already written to disk at this point.
        logger.error(
            "Failed to persist pipeline run for question_id=%s: %s",
            question_id,
            exc,
        )
        await db.rollback()
        return None
