"""
backend/services/score_deltas.py — LLM-vs-human score delta queries
Ask TechBear — Gymnarctos Studios LLC

Computes deltas between automated LLM scores and human reviewer scores
for calibration analysis. This is the core of v2.6's evaluation loop:
the delta tells you where the judges are overconfident, underconfident,
or systematically wrong relative to human judgment.

All functions return plain dicts — no SQLAlchemy models in return values
so callers (routers, report generators) don't need to manage ORM sessions.

Delta sign convention:
    positive delta = LLM scored HIGHER than human  (overconfident judge)
    negative delta = LLM scored LOWER than human   (underconfident judge)
    near-zero      = judge is well-calibrated

LLM score dimensions map to human score dimensions:
    fact_critique.accuracy_score      → human_reviews.fact_score
    character_critique.*_score        → human_reviews.character_score
    editorial_critique.clarity_score  → human_reviews.editorial_score
    (semantic and educational have no direct LLM counterpart yet)

Usage:
    from backend.services.score_deltas import (
        get_run_delta,
        get_aggregate_deltas,
        get_delta_summary,
    )

    # Single run
    delta = await get_run_delta(run_id, db)

    # Aggregate across all reviewed runs
    summary = await get_delta_summary(db, pipeline_version="v2.6")
"""

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models_v26 import (
    HumanReview,
    PipelineRun,
)

logger = logging.getLogger(__name__)

# =============================================================
# Dimension mapping — LLM score_type.score_name → human field
# =============================================================

# Maps (score_type, score_name) → HumanReview column name
# Only dimensions that have a direct human counterpart are listed.
LLM_TO_HUMAN_MAP: dict[tuple[str, str], str] = {
    ("fact_critique", "accuracy_score"):              "fact_score",
    ("fact_critique", "safety_score"):                "fact_score",   # averaged
    ("character_critique", "character_fidelity_score"): "character_score",
    ("character_critique", "anti_formulaic_score"):   "character_score",
    ("editorial_critique", "clarity_score"):          "editorial_score",
    ("editorial_critique", "formatting_score"):       "editorial_score",
}

# Human score columns available for delta computation
HUMAN_SCORE_COLUMNS = {
    "fact_score":        HumanReview.fact_score,
    "character_score":   HumanReview.character_score,
    "editorial_score":   HumanReview.editorial_score,
    "semantic_score":    HumanReview.semantic_score,
    "educational_score": HumanReview.educational_score,
}

# The primary LLM score used to represent each human dimension
# (when multiple LLM scores map to the same human dimension, this
# is the one used for the single-value delta comparison)
PRIMARY_LLM_FOR_DIMENSION: dict[str, tuple[str, str]] = {
    "fact_score":        ("fact_critique", "accuracy_score"),
    "character_score":   ("character_critique", "character_fidelity_score"),
    "editorial_score":   ("editorial_critique", "clarity_score"),
}


# =============================================================
# Single-run delta
# =============================================================


async def get_run_delta(
    run_id: uuid.UUID,
    db: AsyncSession,
) -> Optional[dict[str, Any]]:
    """
    Compute LLM-vs-human score deltas for a single pipeline run.

    Returns None if the run has no completed human review with scores.

    Return shape:
    {
        "run_id": str,
        "pipeline_version": str,
        "question_id": int,
        "review_status": str,
        "dimensions": {
            "fact_score": {
                "llm_score": float | None,
                "human_score": float | None,
                "delta": float | None,    # llm - human (positive = LLM overconfident)
                "llm_source": str,        # score_type.score_name
            },
            ...
        },
        "mean_absolute_delta": float | None,
        "has_complete_scores": bool,
    }
    """
    result = await db.execute(
        select(PipelineRun)
        .where(PipelineRun.id == run_id)
        .options(
            selectinload(PipelineRun.llm_scores),
            selectinload(PipelineRun.human_review),
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        return None

    review = run.human_review
    if review is None or review.review_status not in ("complete", "in_progress"):
        return None

    # Index LLM scores by (score_type, score_name)
    llm_index: dict[tuple[str, str], float] = {}
    for score in (run.llm_scores or []):
        if score.score_value is not None:
            llm_index[(score.score_type, score.score_name)] = float(score.score_value)

    dimensions = {}
    deltas = []

    for dim, human_col in [
        ("fact_score",        review.fact_score),
        ("character_score",   review.character_score),
        ("editorial_score",   review.editorial_score),
        ("semantic_score",    review.semantic_score),
        ("educational_score", review.educational_score),
    ]:
        human_val = float(human_col) if human_col is not None else None
        primary = PRIMARY_LLM_FOR_DIMENSION.get(dim)
        llm_val = llm_index.get(primary) if primary else None
        llm_source = f"{primary[0]}.{primary[1]}" if primary else None

        delta = None
        if llm_val is not None and human_val is not None:
            delta = round(llm_val - human_val, 3)
            deltas.append(abs(delta))

        dimensions[dim] = {
            "llm_score": llm_val,
            "human_score": human_val,
            "delta": delta,
            "llm_source": llm_source,
        }

    mean_abs_delta = (
        round(sum(deltas) / len(deltas), 3) if deltas else None
    )
    has_complete = all(
        dimensions[d]["delta"] is not None
        for d in ("fact_score", "character_score", "editorial_score")
    )

    return {
        "run_id": str(run_id),
        "pipeline_version": run.pipeline_version,
        "question_id": run.question_id,
        "review_status": review.review_status,
        "dimensions": dimensions,
        "mean_absolute_delta": mean_abs_delta,
        "has_complete_scores": has_complete,
    }


# =============================================================
# Aggregate deltas across many runs
# =============================================================


async def get_aggregate_deltas(
    db: AsyncSession,
    pipeline_version: Optional[str] = None,
    min_runs: int = 1,
) -> list[dict[str, Any]]:
    """
    Return per-run delta records for all runs with completed reviews.

    Used to build the aggregate summary and identify systematic bias.

    Args:
        pipeline_version: filter to one version (None = all versions)
        min_runs: minimum number of scored runs required before
                  returning results (guards against spurious stats
                  on tiny samples)

    Returns list of run delta dicts (same shape as get_run_delta).
    """
    query = (
        select(PipelineRun)
        .join(HumanReview, HumanReview.pipeline_run_id == PipelineRun.id)
        .where(
            HumanReview.review_status.in_(("complete", "in_progress"))
        )
        .options(
            selectinload(PipelineRun.llm_scores),
            selectinload(PipelineRun.human_review),
        )
    )

    if pipeline_version:
        query = query.where(PipelineRun.pipeline_version == pipeline_version)

    result = await db.execute(query)
    runs = result.scalars().all()

    if len(runs) < min_runs:
        return []

    deltas = []
    for run in runs:
        delta = await get_run_delta(run.id, db)
        if delta:
            deltas.append(delta)

    return deltas


async def get_delta_summary(
    db: AsyncSession,
    pipeline_version: Optional[str] = None,
) -> dict[str, Any]:
    """
    Compute aggregate delta statistics across all reviewed runs.

    This is the primary calibration signal: large mean deltas on a
    dimension indicate the judge is systematically miscalibrated.

    Return shape:
    {
        "pipeline_version": str | None,
        "run_count": int,
        "scored_run_count": int,
        "dimensions": {
            "fact_score": {
                "mean_llm_score": float | None,
                "mean_human_score": float | None,
                "mean_delta": float | None,         # positive = LLM overconfident
                "mean_absolute_delta": float | None,
                "max_delta": float | None,
                "min_delta": float | None,
                "scored_count": int,                # runs with both scores populated
                # "overconfident" | "underconfident" | "calibrated" | "insufficient_data"
                "bias_direction": str,
            },
            ...
        },
        "overall_mean_absolute_delta": float | None,
        "publishable_rate": float | None,
        "mean_edit_effort": float | None,
        "moderation_accuracy": float | None,
        "flag_frequency": {note_type: count},
        "calibration_verdict": str,
    }
    """
    run_deltas = await get_aggregate_deltas(db, pipeline_version=pipeline_version)
    run_count = len(run_deltas)

    # Aggregate per dimension
    dim_data: dict[str, dict[str, list]] = {
        dim: {"llm": [], "human": [], "delta": []}
        for dim in ("fact_score", "character_score", "editorial_score",
                    "semantic_score", "educational_score")
    }

    for rd in run_deltas:
        for dim, vals in rd["dimensions"].items():
            if vals["llm_score"] is not None:
                dim_data[dim]["llm"].append(vals["llm_score"])
            if vals["human_score"] is not None:
                dim_data[dim]["human"].append(vals["human_score"])
            if vals["delta"] is not None:
                dim_data[dim]["delta"].append(vals["delta"])

    def _mean(lst):
        return round(sum(lst) / len(lst), 3) if lst else None

    def _bias(mean_delta):
        if mean_delta is None:
            return "insufficient_data"
        if abs(mean_delta) < 0.5:
            return "calibrated"
        return "overconfident" if mean_delta > 0 else "underconfident"

    dimensions = {}
    all_abs_deltas = []
    for dim, data in dim_data.items():
        deltas = data["delta"]
        abs_deltas = [abs(d) for d in deltas]
        all_abs_deltas.extend(abs_deltas)
        mean_d = _mean(deltas)
        dimensions[dim] = {
            "mean_llm_score":       _mean(data["llm"]),
            "mean_human_score":     _mean(data["human"]),
            "mean_delta":           mean_d,
            "mean_absolute_delta":  _mean(abs_deltas),
            "max_delta":            round(max(deltas), 3) if deltas else None,
            "min_delta":            round(min(deltas), 3) if deltas else None,
            "scored_count":         len(deltas),
            "bias_direction":       _bias(mean_d),
        }

    # Editorial metrics from HumanReview rows directly
    review_query = (
        select(HumanReview)
        .join(PipelineRun, PipelineRun.id == HumanReview.pipeline_run_id)
        .where(HumanReview.review_status.in_(("complete", "in_progress")))
    )
    if pipeline_version:
        review_query = review_query.where(
            PipelineRun.pipeline_version == pipeline_version
        )
    review_result = await db.execute(
        review_query.options(selectinload(HumanReview.notes))
    )
    reviews = review_result.scalars().all()

    publishable = [r for r in reviews if r.publishable is True]
    efforts = [r.edit_effort for r in reviews if r.edit_effort is not None]
    mod_judgments = [
        r.moderation_correct for r in reviews
        if r.moderation_correct is not None
    ]

    publishable_rate = (
        round(len(publishable) / len(reviews), 3) if reviews else None
    )
    mean_effort = _mean(efforts)
    moderation_accuracy = (
        round(sum(1 for m in mod_judgments if m) / len(mod_judgments), 3)
        if mod_judgments else None
    )

    # Flag frequency across all reviews
    flag_frequency: dict[str, int] = {}
    for review in reviews:
        for note in (review.notes or []):
            flag_frequency[note.note_type] = (
                flag_frequency.get(note.note_type, 0) + 1
            )

    # Overall calibration verdict
    bias_directions = [
        dimensions[d]["bias_direction"]
        for d in ("fact_score", "character_score", "editorial_score")
        if dimensions[d]["bias_direction"] != "insufficient_data"
    ]
    if not bias_directions:
        verdict = "Insufficient data — score more runs before drawing conclusions."
    elif all(b == "calibrated" for b in bias_directions):
        verdict = "Judges appear well-calibrated across primary dimensions."
    elif bias_directions.count("overconfident") > bias_directions.count("underconfident"):
        verdict = (
            "Judges are systematically overconfident — "
            "LLM scores tend to exceed human scores. "
            "Consider tightening critique prompts."
        )
    else:
        verdict = (
            "Judges are systematically underconfident — "
            "LLM scores tend to fall below human scores. "
            "Consider reviewing critique prompt severity calibration."
        )

    return {
        "pipeline_version": pipeline_version,
        "run_count": run_count,
        "scored_run_count": len([
            rd for rd in run_deltas if rd["has_complete_scores"]
        ]),
        "dimensions": dimensions,
        "overall_mean_absolute_delta": _mean(all_abs_deltas),
        "publishable_rate": publishable_rate,
        "mean_edit_effort": mean_effort,
        "moderation_accuracy": moderation_accuracy,
        "flag_frequency": dict(
            sorted(flag_frequency.items(), key=lambda x: x[1], reverse=True)
        ),
        "calibration_verdict": verdict,
    }
