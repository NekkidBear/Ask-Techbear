"""
backend/scripts/run_batch.py — Async Pipeline Batch Runner
Ask TechBear — Gymnarctos Studios LLC

Queries the database for approved questions that don't yet have a pipeline
run for the current version, runs each through the async pipeline, and
persists the results to the evaluation schema.

This is the production batch job — run nightly or on demand.

Usage (from repo root):
    python -m backend.scripts.run_batch
    python -m backend.scripts.run_batch --version v2.6
    python -m backend.scripts.run_batch --limit 10
    python -m backend.scripts.run_batch --question-id 42
    python -m backend.scripts.run_batch --dry-run

Exit codes:
    0 — all questions processed successfully
    1 — one or more questions failed
    2 — no questions to process
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

from sqlalchemy import select, not_, exists
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db_context
from backend.models import Question
from backend.models_v26 import PipelineRun
from backend.services.pipeline.orchestrator import run_pipeline
from backend.services.persistence import persist_pipeline_run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DEFAULT_VERSION = "v2.6"


# =============================================================
# Query helpers
# =============================================================

async def get_pending_questions(
    db: AsyncSession,
    pipeline_version: str,
    limit: int | None = None,
    question_id: int | None = None,
) -> list[Question]:
    """
    Return approved questions that don't yet have a pipeline run
    for the given pipeline_version.

    Filters:
        - status = 'approved'
        - no existing pipeline_runs row for this version
        - optionally constrained to a single question_id
    """
    already_run = select(PipelineRun.question_id).where(
        PipelineRun.pipeline_version == pipeline_version
    )

    query = (
        select(Question)
        .where(Question.status == "approved")
        .where(not_(Question.id.in_(already_run)))
        .order_by(Question.submitted_at.asc())
    )

    if question_id is not None:
        query = query.where(Question.id == question_id)

    if limit is not None:
        query = query.limit(limit)

    result = await db.execute(query)
    return list(result.scalars().all())


# =============================================================
# Single question runner
# =============================================================

async def process_question(
    question: Question,
    db: AsyncSession,
    pipeline_version: str,
    dry_run: bool = False,
) -> bool:
    """
    Run one question through the async pipeline and persist the result.

    Returns True if successful, False if the pipeline or persistence failed.
    """
    logger.info(
        "[%d] %s — %s",
        question.id,
        question.attendee_name,
        question.question_text[:60],
    )

    submission = {
        "id": question.id,
        "attendee_name": question.attendee_name,
        "question": question.question_text,
        "source": "batch",
        "expected_scope": "IN_SCOPE",
        "conversation_depth": 0,
        "rolling_context": "",
        "batch_context": [],
        "submitted_at": question.submitted_at,
    }

    if dry_run:
        logger.info("  [DRY RUN] skipping pipeline execution")
        return True

    # Run the pipeline
    try:
        artifact = run_pipeline(submission)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("  Pipeline error: %s", exc)
        return False

    status = "complete" if artifact.get("passed") else "halted"
    logger.info(
        "  Pipeline %s — voice draft: %d words",
        status,
        len((artifact.get("drafts", {}).get("voice") or "").split()),
    )

    # Persist the result
    run_id = await persist_pipeline_run(
        artifact=artifact,
        question_id=question.id,
        db=db,
        pipeline_version=pipeline_version,
    )

    if run_id:
        logger.info("  Persisted as pipeline_run %s", run_id)
        return True

    logger.warning("  Persistence failed — artifact saved to handoff_output/")
    return False


# =============================================================
# Main batch loop
# =============================================================

async def run_batch(
    pipeline_version: str,
    limit: int | None,
    question_id: int | None,
    dry_run: bool,
) -> int:
    """
    Main batch loop. Returns exit code.
    """
    async with get_db_context() as db:
        questions = await get_pending_questions(
            db, pipeline_version, limit, question_id
        )

        if not questions:
            logger.info(
                "No approved questions pending for pipeline version %s",
                pipeline_version,
            )
            return 2

        total = len(questions)
        logger.info(
            "Found %d question(s) to process [version=%s]%s",
            total,
            pipeline_version,
            " [DRY RUN]" if dry_run else "",
        )

        succeeded = 0
        failed = 0
        started_at = datetime.now(timezone.utc)

        for i, question in enumerate(questions, 1):
            logger.info("[%d/%d]", i, total)
            success = await process_question(
                question, db, pipeline_version, dry_run
            )
            if success:
                succeeded += 1
            else:
                failed += 1

        elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
        logger.info(
            "Batch complete — %d succeeded, %d failed, %.1fs elapsed",
            succeeded,
            failed,
            elapsed,
        )

        return 0 if failed == 0 else 1


# =============================================================
# Entry point
# =============================================================

def main() -> None:
    """Parse arguments and run the batch pipeline."""
    parser = argparse.ArgumentParser(
        description="Ask TechBear async pipeline batch runner"
    )
    parser.add_argument(
        "--version",
        default=DEFAULT_VERSION,
        help=f"Pipeline version label (default: {DEFAULT_VERSION})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of questions to process in this run",
    )
    parser.add_argument(
        "--question-id",
        type=int,
        default=None,
        dest="question_id",
        help="Process a single question by ID",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Query and log questions without running the pipeline",
    )
    args = parser.parse_args()

    exit_code = asyncio.run(
        run_batch(
            pipeline_version=args.version,
            limit=args.limit,
            question_id=args.question_id,
            dry_run=args.dry_run,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
