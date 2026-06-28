"""
routers/pipeline_review.py — Batch pipeline review and approval
Ask TechBear — Gymnarctos Studios LLC

Endpoints for the async batch review workflow:

    GET  /api/review/runs                     queue of runs pending review
    GET  /api/review/runs/{run_id}            single run with artifacts + scores
    PATCH /api/review/runs/{run_id}           save scores, flags, draft edits
    POST  /api/review/runs/{run_id}/approve   atomic publish + optional email
    GET  /api/review/runs/{run_id}/export     single-run export (JSON/CSV)
    GET  /api/review/export                   bulk export (date range / status)

Design notes:
    - PATCH is save-in-progress. Idempotent. No email triggered.
      Sets review_status = 'in_progress' if currently 'pending'.
    - POST /approve is the atomic commit:
        1. Validates final_answer is populated
        2. Sets publishable = True, review_status = 'complete'
        3. Sends email if attendee_email present
        4. Logs delivery as a pipeline_artifact row
        5. Does NOT store email address in artifact (PII boundary)
    - Flag booleans in PATCH body translate to ReviewNote upserts.
      flag=True → create ReviewNote row if absent.
      flag=False → delete ReviewNote row if present.
    - Export endpoints exclude attendee_email (PII) from all output.

Notes on ORM false positives:
    This module uses SQLAlchemy classic Column() declarations (same as
    models.py and models_v26.py). Pylance/Pyright reportAttributeAccessIssue
    and reportGeneralTypeIssues on ORM field access are expected false
    positives — see routers/questions.py for the same pattern and rationale.
"""
# pylint: disable=not-callable
# E1102: sqlalchemy.sql.func members are dynamically generated SQL proxies.

import csv
import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.database import get_db
from backend.models_v26 import (
    HumanReview,
    PipelineArtifact,
    PipelineRun,
    ReviewNote,
)
from backend.services.email_service import get_email_service
from backend.services.encryption import decrypt_email, DecryptionError, has_encrypted_email
from backend.services.score_deltas import get_run_delta, get_delta_summary

logger = logging.getLogger(__name__)

router = APIRouter()

# =============================================================
# Flag taxonomy — must stay in sync with ReviewNote.note_type
# and the batch dashboard UI checkbox labels.
# When adding a new flag type, add it here AND update the
# frontend flag config in the batch dashboard component.
# =============================================================

FLAG_NOTE_TYPES = [
    "missed_claim",
    "unsupported_claim",
    "wrong_retrieval",
    "moderation_false_positive",
    "moderation_false_negative",
    "too_formulaic",
    "voice_break",
    "too_salesy",
    "lore_recall_failure",
    "verbatim_regurgitation",
    "excellent_response",
    "publishable_with_minor_edits",
]

# Default severity per flag type — used when creating ReviewNote rows
# from boolean flags (no explicit severity supplied by the UI).
FLAG_DEFAULT_SEVERITY: Dict[str, str] = {
    "missed_claim": "moderate",
    "unsupported_claim": "critical",
    "wrong_retrieval": "moderate",
    "moderation_false_positive": "moderate",
    "moderation_false_negative": "critical",
    "too_formulaic": "minor",
    "voice_break": "moderate",
    "too_salesy": "minor",
    "lore_recall_failure": "moderate",
    "verbatim_regurgitation": "moderate",
    "excellent_response": "positive",
    "publishable_with_minor_edits": "positive",
}

# =============================================================
# Pydantic schemas
# =============================================================


class ReviewPatchRequest(BaseModel):
    """
    Save-in-progress payload for PATCH /runs/{run_id}.

    All fields optional — partial updates are supported.
    Flag fields translate to ReviewNote upserts (see _apply_flags).
    """
    # Human scores (0-10, same rubric as LLMScore)
    fact_score: Optional[float] = None
    character_score: Optional[float] = None
    editorial_score: Optional[float] = None
    semantic_score: Optional[float] = None
    educational_score: Optional[float] = None

    # Review metadata
    moderation_correct: Optional[bool] = None
    publishable: Optional[bool] = None
    edit_effort: Optional[int] = None        # 0-4 scale
    final_answer: Optional[str] = None       # edited response text (Markdown)
    review_notes: Optional[str] = None       # free-text reviewer notes

    # Structured flags — each True/False maps to a ReviewNote row
    flag_missed_claim: Optional[bool] = None
    flag_unsupported_claim: Optional[bool] = None
    flag_wrong_retrieval: Optional[bool] = None
    flag_moderation_false_positive: Optional[bool] = None
    flag_moderation_false_negative: Optional[bool] = None
    flag_too_formulaic: Optional[bool] = None
    flag_voice_break: Optional[bool] = None
    flag_too_salesy: Optional[bool] = None
    flag_lore_recall_failure: Optional[bool] = None
    flag_verbatim_regurgitation: Optional[bool] = None
    flag_excellent_response: Optional[bool] = None
    flag_publishable_with_minor_edits: Optional[bool] = None

    @field_validator("edit_effort")
    @classmethod
    def effort_in_range(cls, v):
        if v is not None and v not in range(5):
            raise ValueError("edit_effort must be 0-4")
        return v

    @field_validator("fact_score", "character_score", "editorial_score",
                     "semantic_score", "educational_score")
    @classmethod
    def score_in_range(cls, v):
        if v is not None and not (0.0 <= v <= 10.0):
            raise ValueError("Score must be between 0.0 and 10.0")
        return v

    def flag_items(self) -> Dict[str, Optional[bool]]:
        """Return {note_type: bool_or_None} for all flag fields."""
        return {
            "missed_claim": self.flag_missed_claim,
            "unsupported_claim": self.flag_unsupported_claim,
            "wrong_retrieval": self.flag_wrong_retrieval,
            "moderation_false_positive": self.flag_moderation_false_positive,
            "moderation_false_negative": self.flag_moderation_false_negative,
            "too_formulaic": self.flag_too_formulaic,
            "voice_break": self.flag_voice_break,
            "too_salesy": self.flag_too_salesy,
            "lore_recall_failure": self.flag_lore_recall_failure,
            "verbatim_regurgitation": self.flag_verbatim_regurgitation,
            "excellent_response": self.flag_excellent_response,
            "publishable_with_minor_edits": self.flag_publishable_with_minor_edits,
        }


class ApproveRequest(BaseModel):
    """
    Payload for POST /runs/{run_id}/approve.

    final_answer is required — approval without a confirmed answer
    text is rejected to prevent empty corpus entries.
    """
    final_answer: str
    review_notes: Optional[str] = None

    @field_validator("final_answer")
    @classmethod
    def answer_not_empty(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("final_answer cannot be empty")
        return v


# =============================================================
# Helpers
# =============================================================


async def _get_run_or_404(
    run_id: uuid.UUID,
    db: AsyncSession,
) -> PipelineRun:
    """Load a PipelineRun with all relationships, or raise 404."""
    result = await db.execute(
        select(PipelineRun)
        .where(PipelineRun.id == run_id)
        .options(
            selectinload(PipelineRun.artifacts),
            selectinload(PipelineRun.llm_scores),
            selectinload(PipelineRun.human_review).selectinload(
                HumanReview.notes
            ),
            selectinload(PipelineRun.question),
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    return run


async def _get_or_create_review(
    run_id: uuid.UUID,
    db: AsyncSession,
) -> HumanReview:
    """
    Return the HumanReview for this run, creating it if absent.

    Called by PATCH before saving scores/flags, so reviewers don't
    need to explicitly create a review before editing it.
    """
    result = await db.execute(
        select(HumanReview)
        .where(HumanReview.pipeline_run_id == run_id)
        .options(selectinload(HumanReview.notes))
    )
    review = result.scalar_one_or_none()
    if review is None:
        review = HumanReview(
            pipeline_run_id=run_id,
            reviewer_name="Jason",
            review_status="in_progress",
        )
        db.add(review)
        await db.flush()   # get the id without committing
    return review


async def _apply_flags(
    review: HumanReview,
    flag_items: Dict[str, Optional[bool]],
    db: AsyncSession,
) -> None:
    """
    Translate flag booleans into ReviewNote upserts.

    True  → create ReviewNote row if not already present
    False → delete ReviewNote row if present
    None  → no change (field not included in this PATCH)
    """
    # Index existing notes by note_type for fast lookup
    existing: Dict[str, ReviewNote] = {
        note.note_type: note for note in (review.notes or [])
    }

    for note_type, flag_value in flag_items.items():
        if flag_value is None:
            continue  # not included in this PATCH — leave as-is

        if flag_value is True and note_type not in existing:
            new_note = ReviewNote(
                human_review_id=review.id,
                note_type=note_type,
                severity=FLAG_DEFAULT_SEVERITY.get(note_type, "moderate"),
                note=f"Flagged by reviewer: {note_type.replace('_', ' ')}",
            )
            db.add(new_note)

        elif flag_value is False and note_type in existing:
            await db.delete(existing[note_type])


def _serialize_run(run: PipelineRun) -> Dict[str, Any]:
    """
    Serialize a PipelineRun to a dict for API responses.

    Excludes attendee_email (PII boundary).
    Includes LLM scores, artifacts (content), and human review with flags.
    """
    question = run.question

    # Build flag state from ReviewNote rows
    review = run.human_review
    active_flags: List[str] = []
    human_scores: Dict[str, Any] = {}
    review_data: Dict[str, Any] = {}

    if review:
        active_flags = [note.note_type for note in (review.notes or [])]
        human_scores = {
            "fact_score": float(review.fact_score) if review.fact_score else None,
            "character_score": float(review.character_score) if review.character_score else None,
            "editorial_score": float(review.editorial_score) if review.editorial_score else None,
            "semantic_score": float(review.semantic_score) if review.semantic_score else None,
            "educational_score": (
                float(review.educational_score) if review.educational_score else None
            ),
        }
        review_data = {
            "id": str(review.id),
            "review_status": review.review_status,
            "publishable": review.publishable,
            "edit_effort": review.edit_effort,
            "moderation_correct": review.moderation_correct,
            "final_answer": review.final_answer,
            "review_notes": review.review_notes,
            "active_flags": active_flags,
            "human_scores": human_scores,
            "updated_at": review.updated_at.isoformat() if review.updated_at else None,
        }

    # Group LLM scores by phase
    llm_scores: Dict[str, Any] = {}
    for score in (run.llm_scores or []):
        phase = score.score_type
        if phase not in llm_scores:
            llm_scores[phase] = {}
        llm_scores[phase][score.score_name] = {
            "value": float(score.score_value) if score.score_value else None,
            "pass_recommendation": score.pass_recommendation,
            "model": score.model,
            "notes": score.notes,
        }

    # Artifacts keyed by type
    artifacts: Dict[str, Any] = {}
    for artifact in (run.artifacts or []):
        artifacts[artifact.artifact_type] = {
            "content": artifact.content,
            "metadata": artifact.artifact_metadata,
            "created_at": artifact.created_at.isoformat(),
        }

    return {
        "run_id": str(run.id),
        "pipeline_version": run.pipeline_version,
        "run_label": run.run_label,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "question": {
            "id": question.id,
            "attendee_name": question.attendee_name,
            # attendee_email intentionally excluded (PII)
            "question_text": question.question_text,
            "submitted_at": question.submitted_at.isoformat() if question.submitted_at else None,
            "has_email": has_encrypted_email(question.attendee_email),
        },
        "llm_scores": llm_scores,
        "artifacts": artifacts,
        "human_review": review_data if review_data else None,
    }


# =============================================================
# Endpoints
# =============================================================


@router.get("/runs")
async def list_review_queue(
    status: Optional[str] = Query(
        None,
        description="Filter by review status: pending | in_progress | complete | skipped"
    ),
    pipeline_version: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """
    Return pipeline runs queued for human review.

    Default: all runs that have no HumanReview row, or where
    review_status is 'pending' or 'in_progress'.
    Filter by status, pipeline_version, or paginate with limit/offset.
    """
    query = (
        select(PipelineRun)
        .options(
            selectinload(PipelineRun.question),
            selectinload(PipelineRun.human_review),
            selectinload(PipelineRun.llm_scores),
        )
        .order_by(PipelineRun.completed_at.asc().nulls_last())
        .offset(offset)
        .limit(limit)
    )

    if pipeline_version:
        query = query.where(PipelineRun.pipeline_version == pipeline_version)

    result = await db.execute(query)
    runs = result.scalars().all()

    # Filter by review status post-query (simpler than a join for now)
    if status:
        if status == "pending":
            runs = [
                r for r in runs
                if r.human_review is None
                or r.human_review.review_status == "pending"
            ]
        else:
            runs = [
                r for r in runs
                if r.human_review
                and r.human_review.review_status == status
            ]

    return {
        "count": len(runs),
        "offset": offset,
        "limit": limit,
        "runs": [
            {
                "run_id": str(r.id),
                "pipeline_version": r.pipeline_version,
                "run_label": r.run_label,
                "status": r.status,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "question_id": r.question_id,
                "attendee_name": r.question.attendee_name if r.question else None,
                "question_preview": (
                    r.question.question_text[:100] + "..."
                    if r.question and len(r.question.question_text) > 100
                    else (r.question.question_text if r.question else None)
                ),
                "has_email": bool(r.question and has_encrypted_email(r.question.attendee_email)),
                "review_status": (
                    r.human_review.review_status if r.human_review else "pending"
                ),
                "publishable": (
                    r.human_review.publishable if r.human_review else None
                ),
            }
            for r in runs
        ],
    }


@router.get("/runs/{run_id}")
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Return a single pipeline run with all artifacts, scores, and review state.

    This is the full payload the batch dashboard loads when a reviewer
    opens a run for editing.
    """
    run = await _get_run_or_404(run_id, db)
    return _serialize_run(run)


@router.patch("/runs/{run_id}")
async def patch_review(
    run_id: uuid.UUID,
    body: ReviewPatchRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Save review progress. Idempotent. Does not trigger email.

    Creates a HumanReview row if one doesn't exist yet.
    Translates flag booleans to ReviewNote upserts.
    Sets review_status to 'in_progress' if currently 'pending'.
    """
    # Confirm run exists
    await _get_run_or_404(run_id, db)
    review = await _get_or_create_review(run_id, db)

    # Apply scalar fields — only update fields that were explicitly sent
    if body.fact_score is not None:
        review.fact_score = body.fact_score
    if body.character_score is not None:
        review.character_score = body.character_score
    if body.editorial_score is not None:
        review.editorial_score = body.editorial_score
    if body.semantic_score is not None:
        review.semantic_score = body.semantic_score
    if body.educational_score is not None:
        review.educational_score = body.educational_score
    if body.moderation_correct is not None:
        review.moderation_correct = body.moderation_correct
    if body.publishable is not None:
        review.publishable = body.publishable
    if body.edit_effort is not None:
        review.edit_effort = body.edit_effort
    if body.final_answer is not None:
        review.final_answer = body.final_answer
    if body.review_notes is not None:
        review.review_notes = body.review_notes

    # Advance status from pending → in_progress on first edit
    if review.review_status == "pending":
        review.review_status = "in_progress"

    # Apply flags → ReviewNote upserts
    await _apply_flags(review, body.flag_items(), db)

    await db.commit()
    await db.refresh(review)

    return {
        "status": "saved",
        "run_id": str(run_id),
        "review_status": review.review_status,
    }


@router.post("/runs/{run_id}/approve")
async def approve_run(
    run_id: uuid.UUID,
    body: ApproveRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Atomic approval: publish the run, optionally email the attendee.

    Steps (all or nothing on DB side):
        1. Validate final_answer is populated (enforced by schema)
        2. Get or create HumanReview, set publishable=True, status=complete
        3. Write final_answer and review_notes
        4. Commit DB changes
        5. If attendee has email: attempt delivery, log artifact
           (email send failure does NOT roll back the approval)

    Email delivery is best-effort. A failed send logs an error and
    records a 'email_delivery' artifact with status='failed' — the
    approval itself is not rolled back.

    The attendee_email address is consumed here and NOT stored in
    the artifact. The artifact records delivery status and timestamp only.
    """
    run = await _get_run_or_404(run_id, db)
    review = await _get_or_create_review(run_id, db)

    # Set approval state
    review.publishable = True
    review.review_status = "complete"
    review.final_answer = body.final_answer
    if body.review_notes:
        review.review_notes = body.review_notes

    await db.commit()
    await db.refresh(review)

    # Attempt email delivery if attendee provided an address
    email_sent = False
    attendee_email: Optional[str] = None

    if run.question and has_encrypted_email(run.question.attendee_email):
        try:
            attendee_email = decrypt_email(run.question.attendee_email)
        except DecryptionError as exc:
            logger.error(
                "Could not decrypt email for run %s: %s — skipping delivery.",
                run_id, exc,
            )

    if attendee_email:
        attendee_name = run.question.attendee_name or "there"
        question_preview = (
            run.question.question_text[:80] + "..."
            if run.question and len(run.question.question_text) > 80
            else (run.question.question_text if run.question else "your question")
        )

        subject = "TechBear answered your question! 🐻"
        body_md = (
            f"Hi {attendee_name}!\n\n"
            f"You asked: *{question_preview}*\n\n"
            f"TechBear says:\n\n"
            f"{body.final_answer}\n\n"
            f"---\n"
            f"*TechBear is the sassy, warmhearted IT expert alter ego of Jason "
            f"at Gymnarctos Studios. Have more questions? "
            f"Email ask-techbear@gymnarctosstudiosllc.com*"
        )

        try:
            email_service = get_email_service()
            email_sent = await email_service.send(
                to=attendee_email,
                subject=subject,
                body_markdown=body_md,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Email delivery error for run %s: %s", run_id, exc)
            email_sent = False

        # Log delivery as a pipeline artifact (no email address stored)
        delivery_artifact = PipelineArtifact(
            pipeline_run_id=run_id,
            artifact_type="email_delivery",
            content=None,
            artifact_metadata={
                "delivered": email_sent,
                "attempted_at": datetime.now(timezone.utc).isoformat(),
                "subject": subject,
                # email address intentionally not stored here (PII boundary)
            },
        )
        db.add(delivery_artifact)
        await db.commit()

        if not email_sent:
            logger.warning(
                "Approval recorded for run %s but email delivery failed. "
                "Check EMAIL_BACKEND configuration.",
                run_id,
            )

    return {
        "status": "approved",
        "run_id": str(run_id),
        "publishable": True,
        "email_attempted": bool(attendee_email),
        "email_sent": email_sent,
    }


@router.get("/runs/{run_id}/export")
async def export_run(
    run_id: uuid.UUID,
    fmt: str = Query("json", description="Export format: json | csv"),
    db: AsyncSession = Depends(get_db),
):
    """
    Export a single run's review data.

    Excludes attendee_email (PII). Includes LLM scores, human scores,
    flags, and final_answer.

    Useful for archiving individual runs or feeding into external
    reporting tools.
    """
    run = await _get_run_or_404(run_id, db)
    data = _serialize_run(run)

    if fmt == "csv":
        # Flatten to a single row for CSV
        review = data.get("human_review") or {}
        scores = review.get("human_scores") or {}
        llm = data.get("llm_scores") or {}

        def _llm_score(phase: str, name: str) -> Optional[float]:
            return llm.get(phase, {}).get(name, {}).get("value")

        row = {
            "run_id": data["run_id"],
            "pipeline_version": data["pipeline_version"],
            "run_label": data["run_label"],
            "status": data["status"],
            "question_id": data["question"]["id"],
            "attendee_name": data["question"]["attendee_name"],
            "question_preview": data["question"]["question_text"][:120],
            "has_email": data["question"]["has_email"],
            "llm_fact_score": _llm_score("fact_critique", "accuracy_score"),
            "llm_character_score": _llm_score("character_critique", "character_fidelity_score"),
            "llm_editorial_score": _llm_score("editorial_critique", "clarity_score"),
            "human_fact_score": scores.get("fact_score"),
            "human_character_score": scores.get("character_score"),
            "human_editorial_score": scores.get("editorial_score"),
            "human_semantic_score": scores.get("semantic_score"),
            "human_educational_score": scores.get("educational_score"),
            "review_status": review.get("review_status"),
            "publishable": review.get("publishable"),
            "edit_effort": review.get("edit_effort"),
            "moderation_correct": review.get("moderation_correct"),
            "active_flags": "|".join(review.get("active_flags") or []),
            "review_notes": review.get("review_notes"),
            "final_answer": review.get("final_answer"),
        }

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)
        output.seek(0)

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=run_{run_id}.csv"
            },
        )

    return data


@router.get("/export")
async def export_bulk(
    fmt: str = Query("csv", description="Export format: json | csv"),
    status: Optional[str] = Query(None, description="Filter by review_status"),
    pipeline_version: Optional[str] = Query(None),
    after: Optional[datetime] = Query(None, description="ISO datetime — runs completed after"),
    before: Optional[datetime] = Query(None, description="ISO datetime — runs completed before"),
    db: AsyncSession = Depends(get_db),
):
    """
    Bulk export of reviewed runs.

    Excludes attendee_email (PII) from all output.
    Useful for benchmark reporting, score delta analysis, and
    corpus ingestion eligibility queries.

    Corpus eligibility filter: status=complete + publishable=true
    can be applied downstream — this endpoint returns the raw data.
    """
    query = (
        select(PipelineRun)
        .options(
            selectinload(PipelineRun.question),
            selectinload(PipelineRun.llm_scores),
            selectinload(PipelineRun.human_review).selectinload(
                HumanReview.notes
            ),
            selectinload(PipelineRun.artifacts),
        )
        .order_by(PipelineRun.completed_at.asc().nulls_last())
    )

    if pipeline_version:
        query = query.where(PipelineRun.pipeline_version == pipeline_version)
    if after:
        query = query.where(PipelineRun.completed_at >= after)
    if before:
        query = query.where(PipelineRun.completed_at <= before)

    result = await db.execute(query)
    runs = result.scalars().all()

    # Filter by review status post-query
    if status:
        runs = [
            r for r in runs
            if r.human_review and r.human_review.review_status == status
        ]

    serialized = [_serialize_run(r) for r in runs]

    if fmt == "json":
        return {"count": len(serialized), "runs": serialized}

    # CSV — one row per run
    if not serialized:
        return StreamingResponse(
            iter(["run_id,pipeline_version,status\n"]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=export.csv"},
        )

    def _flatten(data: Dict[str, Any]) -> Dict[str, Any]:
        review = data.get("human_review") or {}
        scores = review.get("human_scores") or {}
        llm = data.get("llm_scores") or {}

        def _llm_score(phase: str, name: str) -> Optional[float]:
            return llm.get(phase, {}).get(name, {}).get("value")

        return {
            "run_id": data["run_id"],
            "pipeline_version": data["pipeline_version"],
            "run_label": data["run_label"],
            "pipeline_status": data["status"],
            "question_id": data["question"]["id"],
            "attendee_name": data["question"]["attendee_name"],
            "question_preview": data["question"]["question_text"][:120],
            "has_email": data["question"]["has_email"],
            "llm_fact_score": _llm_score("fact_critique", "accuracy_score"),
            "llm_character_score": _llm_score("character_critique", "character_fidelity_score"),
            "llm_editorial_score": _llm_score("editorial_critique", "clarity_score"),
            "human_fact_score": scores.get("fact_score"),
            "human_character_score": scores.get("character_score"),
            "human_editorial_score": scores.get("editorial_score"),
            "human_semantic_score": scores.get("semantic_score"),
            "human_educational_score": scores.get("educational_score"),
            "review_status": review.get("review_status"),
            "publishable": review.get("publishable"),
            "edit_effort": review.get("edit_effort"),
            "moderation_correct": review.get("moderation_correct"),
            "active_flags": "|".join(review.get("active_flags") or []),
            "review_notes": review.get("review_notes"),
            "final_answer_length": len(review.get("final_answer") or ""),
        }
        # Note: final_answer text is excluded from bulk CSV export to keep
        # file sizes manageable. Use single-run /export for full text.

    rows = [_flatten(d) for d in serialized]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=review_export_{timestamp}.csv"
        },
    )


# =============================================================
# Delta endpoints
# =============================================================


@router.get("/runs/{run_id}/delta")
async def get_run_score_delta(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Return LLM-vs-human score deltas for a single pipeline run.

    Requires a completed or in-progress HumanReview with at least
    some scores populated. Returns 404 if no scored review exists.

    Positive delta = LLM scored higher than human (overconfident).
    Negative delta = LLM scored lower than human (underconfident).
    """
    delta = await get_run_delta(run_id, db)
    if delta is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No scored human review found for this run. "
                "Complete at least a partial review before requesting deltas."
            ),
        )
    return delta


@router.get("/delta/summary")
async def get_delta_summary_endpoint(
    pipeline_version: Optional[str] = Query(
        None,
        description="Filter to a specific pipeline version e.g. 'v2.6'"
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Aggregate LLM-vs-human delta summary across all reviewed runs.

    This is the primary judge calibration signal. Large mean deltas
    on a dimension indicate systematic judge miscalibration.

    Also returns publishable rate, mean edit effort, moderation
    accuracy, and flag frequency — the full evaluation analytics
    picture for the benchmark report.
    """
    summary = await get_delta_summary(db, pipeline_version=pipeline_version)
    return summary
