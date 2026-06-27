"""
backend/scripts/check_thresholds.py — Pipeline Threshold Reporter
Ask TechBear — Gymnarctos Studios LLC

Queries the evaluation schema and reports on count and age triggers
that indicate when a benchmark run or human review pass is warranted.

Triggers reported:
    COUNT — number of completed pipeline runs since last human review session
    AGE   — days since oldest unreviewed pipeline run
    SCORE — phases with average LLM scores below warning thresholds

Usage (from repo root):
    python -m backend.scripts.check_thresholds
    python -m backend.scripts.check_thresholds --version v2.6
    python -m backend.scripts.check_thresholds --json
    python -m backend.scripts.check_thresholds --fail-on-trigger

Exit codes:
    0 — no triggers fired
    1 — one or more triggers fired (use with --fail-on-trigger for CI)
    2 — database error
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db_context
from backend.models_v26 import HumanReview, LLMScore, PipelineRun

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# =============================================================
# Threshold definitions
# =============================================================

# Fire COUNT trigger when this many runs have no human review
COUNT_TRIGGER = int(10)

# Fire AGE trigger when the oldest unreviewed run is this many days old
AGE_TRIGGER_DAYS = int(7)

# Fire SCORE trigger when a phase's average score drops below this
SCORE_WARNING_THRESHOLD = float(6.0)

# Score dimensions worth tracking (score_type.score_name)
TRACKED_SCORES = [
    ("fact_critique", "accuracy_score"),
    ("fact_critique", "safety_score"),
    ("character_critique", "character_fidelity_score"),
    ("character_critique", "regurgitation_score"),
    ("character_critique", "anti_formulaic_score"),
    ("editorial_critique", "clarity_score"),
]


# =============================================================
# Query helpers
# =============================================================

async def _count_unreviewed(db: AsyncSession, pipeline_version: str) -> int:
    """Count completed runs with no associated human review."""
    reviewed_run_ids = select(HumanReview.pipeline_run_id)
    result = await db.execute(
        select(func.count(PipelineRun.id))  # pylint: disable=not-callable
        .where(PipelineRun.pipeline_version == pipeline_version)
        .where(PipelineRun.status == "complete")
        .where(PipelineRun.id.notin_(reviewed_run_ids))
    )
    return result.scalar() or 0


async def _oldest_unreviewed_age_days(
    db: AsyncSession, pipeline_version: str
) -> float | None:
    """Return age in days of the oldest unreviewed complete run, or None."""
    reviewed_run_ids = select(HumanReview.pipeline_run_id)
    result = await db.execute(
        select(func.min(PipelineRun.completed_at)
               )  # pylint: disable=not-callable
        .where(PipelineRun.pipeline_version == pipeline_version)
        .where(PipelineRun.status == "complete")
        .where(PipelineRun.id.notin_(reviewed_run_ids))
    )
    oldest = result.scalar()
    if oldest is None:
        return None
    now = datetime.now(timezone.utc)
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=timezone.utc)
    return (now - oldest).total_seconds() / 86400


async def _average_scores(
    db: AsyncSession, pipeline_version: str
) -> dict[tuple[str, str], float]:
    """
    Return average LLM score values for each tracked dimension,
    restricted to the given pipeline version.
    """
    run_ids_for_version = select(PipelineRun.id).where(
        PipelineRun.pipeline_version == pipeline_version
    )
    result = await db.execute(
        select(
            LLMScore.score_type,
            LLMScore.score_name,
            func.avg(LLMScore.score_value),  # pylint: disable=not-callable
        )
        .where(LLMScore.pipeline_run_id.in_(run_ids_for_version))
        .where(
            text(
                "(score_type || '.' || score_name) = ANY(:pairs)"
            ).bindparams(
                pairs=[f"{st}.{sn}" for st, sn in TRACKED_SCORES]
            )
        )
        .group_by(LLMScore.score_type, LLMScore.score_name)
    )
    return {
        (row[0], row[1]): float(row[2])
        for row in result.all()
        if row[2] is not None
    }


async def _loop_count_summary(
    db: AsyncSession, pipeline_version: str
) -> dict[str, int]:
    """
    Return total retry counts across all runs for this version.
    Reads from pipeline_artifacts where artifact_type = 'handoff_json'
    and the loop_counts field is present.
    """
    # Loop counts are stored in the handoff artifact's metadata JSONB.
    # We aggregate them here for reporting without a dedicated table.
    result = await db.execute(
        text("""
            SELECT
                SUM((pa.metadata -> 'loop_counts' ->> 'factual')::int)  AS factual_loops,
                SUM((pa.metadata -> 'loop_counts' ->> 'voice')::int)    AS voice_loops,
                COUNT(*)                                                 AS run_count
            FROM pipeline_artifacts pa
            JOIN pipeline_runs pr ON pa.pipeline_run_id = pr.id
            WHERE pr.pipeline_version = :version
              AND pa.artifact_type = 'handoff_json'
              AND pa.metadata ? 'loop_counts'
        """).bindparams(version=pipeline_version)
    )
    row = result.fetchone()
    if row is None:
        return {"factual_loops": 0, "voice_loops": 0, "run_count": 0}
    return {
        "factual_loops": int(row[0] or 0),
        "voice_loops": int(row[1] or 0),
        "run_count": int(row[2] or 0),
    }


# =============================================================
# Report builder
# =============================================================

async def build_report(pipeline_version: str) -> dict:
    """
    Query the database and return a structured threshold report.
    """
    async with get_db_context() as db:
        unreviewed_count = await _count_unreviewed(db, pipeline_version)
        oldest_age = await _oldest_unreviewed_age_days(db, pipeline_version)
        avg_scores = await _average_scores(db, pipeline_version)
        loop_summary = await _loop_count_summary(db, pipeline_version)

    triggers = []

    # COUNT trigger
    count_fired = unreviewed_count >= COUNT_TRIGGER
    triggers.append({
        "trigger": "COUNT",
        "fired": count_fired,
        "value": unreviewed_count,
        "threshold": COUNT_TRIGGER,
        "message": (
            f"{unreviewed_count} unreviewed runs (threshold: {COUNT_TRIGGER})"
        ),
    })

    # AGE trigger
    if oldest_age is not None:
        age_fired = oldest_age >= AGE_TRIGGER_DAYS
        triggers.append({
            "trigger": "AGE",
            "fired": age_fired,
            "value": round(oldest_age, 1),
            "threshold": AGE_TRIGGER_DAYS,
            "message": (
                f"Oldest unreviewed run is {oldest_age:.1f} days old "
                f"(threshold: {AGE_TRIGGER_DAYS} days)"
            ),
        })
    else:
        triggers.append({
            "trigger": "AGE",
            "fired": False,
            "value": None,
            "threshold": AGE_TRIGGER_DAYS,
            "message": "No unreviewed runs found",
        })

    # SCORE triggers — one per tracked dimension
    for (score_type, score_name), avg_value in avg_scores.items():
        score_fired = avg_value < SCORE_WARNING_THRESHOLD
        triggers.append({
            "trigger": "SCORE",
            "fired": score_fired,
            "dimension": f"{score_type}.{score_name}",
            "value": round(avg_value, 2),
            "threshold": SCORE_WARNING_THRESHOLD,
            "message": (
                f"{score_type}.{score_name} avg={avg_value:.2f} "
                f"({'BELOW' if score_fired else 'OK'} threshold {SCORE_WARNING_THRESHOLD})"
            ),
        })

    # Tracked dimensions with no data yet
    seen_dims = {(st, sn) for (st, sn) in avg_scores}
    for (score_type, score_name) in TRACKED_SCORES:
        if (score_type, score_name) not in seen_dims:
            triggers.append({
                "trigger": "SCORE",
                "fired": False,
                "dimension": f"{score_type}.{score_name}",
                "value": None,
                "threshold": SCORE_WARNING_THRESHOLD,
                "message": f"{score_type}.{score_name}: no data yet",
            })

    any_fired = any(t["fired"] for t in triggers)

    return {
        "pipeline_version": pipeline_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "any_trigger_fired": any_fired,
        "summary": {
            "unreviewed_runs": unreviewed_count,
            "oldest_unreviewed_days": round(oldest_age, 1) if oldest_age else None,
            "loop_counts": loop_summary,
        },
        "triggers": triggers,
    }


# =============================================================
# Output formatters
# =============================================================

def _print_report(report: dict) -> None:
    """Human-readable threshold report."""
    print()
    print("Ask TechBear — Pipeline Threshold Report")
    print(f"Version: {report['pipeline_version']}")
    print(f"Generated: {report['generated_at']}")
    print()
    print("Summary")
    print(f"  Unreviewed runs:       {report['summary']['unreviewed_runs']}")
    age = report['summary']['oldest_unreviewed_days']
    print(
        f"  Oldest unreviewed:     {f'{age} days' if age is not None else 'n/a'}")
    lc = report['summary']['loop_counts']
    print(
        f"  Factual retries total: {lc['factual_loops']} across {lc['run_count']} runs")
    print(
        f"  Voice retries total:   {lc['voice_loops']} across {lc['run_count']} runs")
    print()
    print("Triggers")
    for t in report["triggers"]:
        status = "🔴 FIRED " if t["fired"] else "✅ OK    "
        print(f"  {status} {t['message']}")
    print()
    if report["any_trigger_fired"]:
        print("⚠️  One or more triggers fired — a review pass is recommended.")
    else:
        print("All triggers clear.")
    print()


# =============================================================
# Entry point
# =============================================================

def main() -> None:
    """Run threshold checks for evaluation/reporting triggers."""
    parser = argparse.ArgumentParser(
        description="Ask TechBear pipeline threshold reporter"
    )
    parser.add_argument(
        "--version",
        default="v2.6",
        help="Pipeline version to report on (default: v2.6)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output report as JSON instead of human-readable text",
    )
    parser.add_argument(
        "--fail-on-trigger",
        action="store_true",
        help="Exit with code 1 if any trigger fires (useful in CI)",
    )
    args = parser.parse_args()

    try:
        report = asyncio.run(build_report(args.version))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Database error: %s", exc)
        sys.exit(2)

    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report)

    if args.fail_on_trigger and report["any_trigger_fired"]:
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
