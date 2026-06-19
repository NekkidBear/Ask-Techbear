"""
routers/questions.py — Question submission and queue management
Gymnarctos Studios LLC
"""

import re
from datetime import datetime
from typing import Optional, Union
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Question, Session as EventSession, PresentationVersion
from backend.services.llm import generate_techbear_response
from backend.services.moderation import check_blocklist

router = APIRouter()

# =============================================================
# Pydantic schemas — request/response shapes
# =============================================================


class QuestionSubmit(BaseModel):
    """Schema for attendee question submission."""
    attendee_name: str
    question_text: str
    session_id: Optional[str] = None

    @field_validator('attendee_name')
    @classmethod
    def name_not_empty(cls, v):
        v = v.strip()
        if not v:
            raise ValueError('Name cannot be empty')
        return v[:100]  # enforce max length

    @field_validator('question_text')
    @classmethod
    def question_not_empty(cls, v):
        v = v.strip()
        if not v:
            raise ValueError('Question cannot be empty')
        # Basic prompt injection sanitization
        injection_patterns = [
            r'ignore previous instructions',
            r'ignore all instructions',
            r'new prompt',
            r'system:',
            r'assistant:',
            r'<\|.*?\|>',
        ]
        for pattern in injection_patterns:
            v = re.sub(pattern, '[removed]', v, flags=re.IGNORECASE)
        return v[:500]  # enforce max length


class QuestionResponse(BaseModel):
    """Schema for question data returned to clients."""
    id: int
    attendee_id: Optional[int] = None # Fallback flexibility
    session_id: Optional[Union[UUID,str]] = None  # Match database schema fields safely
    attendee_name: str
    question_text: str
    status: str
    submitted_at: datetime
    highlight: bool
    llm_draft: Optional[str] = None
    answered_at: Optional[datetime] = None
    presentation_text: Optional[str] = None

    class Config:
        from_attributes = True


class QuestionUpdate(BaseModel):
    """Schema for moderator updates to a question."""
    status: Optional[str] = None
    highlight: Optional[bool] = None
    llm_draft: Optional[str] = None
    answered_at: Optional[datetime] = None


# =============================================================
# Helper — get or create active session
# =============================================================

async def get_active_session(db: AsyncSession) -> EventSession:
    """
    Returns the currently active session.
    If none exists, creates one automatically.
    """
    result = await db.execute(
        select(EventSession).where(EventSession.active == True)
    )
    session = result.scalar_one_or_none()

    if not session:
        session = EventSession(event_name="TechBear Live")
        db.add(session)
        await db.flush()

    return session


# =============================================================
# Routes
# =============================================================

@router.post("/", response_model=QuestionResponse, status_code=201)
async def submit_question(
    payload: QuestionSubmit,
    db: AsyncSession = Depends(get_db)
):
    """
    Public endpoint — attendee submits a question.
    Sanitizes input, checks blocklist, assigns to active session.
    """
    session = await get_active_session(db)

    # Stage 1 moderation — fast blocklist check
    is_flagged, matched_term = await check_blocklist(
        payload.question_text, db
    )

    question = Question(
        session_id=session.id,
        attendee_name=payload.attendee_name,
        question_text=payload.question_text,
        status="rejected" if is_flagged else "pending",
        moderation_flag=matched_term if is_flagged else None,
    )
    db.add(question)
    await db.flush()
    await db.refresh(question)
    
    # Pack dictionary manually to bypass model relationship lazy-loading completely
    question_dict = {
        "id": question.id,
        "session_id": str(question.session_id) if question.session_id else None,
        "attendee_name": question.attendee_name,
        "question_text": question.question_text,
        "status": question.status,
        "submitted_at": question.submitted_at,
        "highlight": question.highlight,
        "llm_draft": question.llm_draft,
        "answered_at": question.answered_at,
        "presentation_text": None  # Intentionally empty; newly born questions have no approved text yet
    }
    
    return QuestionResponse.model_validate(question_dict)


@router.get("/", response_model=list[QuestionResponse])
async def get_questions(
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Dashboard endpoint — returns question queue.
    Optionally filter by status.
    """
    query = (
        select(Question)
        .options(selectinload(Question.presentation_version))
        .order_by(Question.submitted_at.asc())
    )
    if status:
        query = query.where(Question.status == status)
    result = await db.execute(query)
    questions = result.scalars().all()

    out = []
    for q in questions:
        data = QuestionResponse.model_validate(q)
        if q.presentation_version:
            data.presentation_text = q.presentation_version.display_text
        out.append(data)
    return out


@router.get("/highlighted", response_model=list[QuestionResponse])
async def get_highlighted_questions(
    db: AsyncSession = Depends(get_db)
):
    """
    Slideshow endpoint — returns all highlighted Q&As with their
    presentation versions (if seeded).
    """
    result = await db.execute(
        select(Question)
        .options(selectinload(Question.presentation_version))
        .where(Question.highlight == True)
        .order_by(
            Question.display_order.asc().nullslast(),
            Question.answered_at.desc(),
        )
    )
    questions = result.scalars().all()

    out = []
    for q in questions:
        data = QuestionResponse.model_validate(q)
        if q.presentation_version:
            data.presentation_text = q.presentation_version.display_text
        out.append(data)
    return out


@router.patch("/{question_id}", response_model=QuestionResponse)
async def update_question(
    question_id: int,
    payload: QuestionUpdate,
    db: AsyncSession = Depends(get_db)
):
    """
    Dashboard endpoint — update question status, highlight, draft.
    """
    result = await db.execute(
        select(Question).options(selectinload(Question.presentation_version)).where(Question.id == question_id)
    )
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    if payload.status is not None:
        question.status = payload.status
    if payload.highlight is not None:
        question.highlight = payload.highlight
    if payload.llm_draft is not None:
        question.llm_draft = payload.llm_draft
    if payload.answered_at is not None:
        question.answered_at = payload.answered_at

    await db.flush()
    await db.refresh(question)
    
    response_data = QuestionResponse.model_validate(question)
    if question.presentation_version:
        response_data.presentation_text = question.presentation_version.display_text
    return response_data


@router.post("/{question_id}/generate", response_model=QuestionResponse)
async def generate_draft(
    question_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    Triggers TechBear's LLM to draft a response for this question.
    """
    result = await db.execute(
        select(Question).options(selectinload(Question.presentation_version)).where(Question.id == question_id)
    )
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    draft = await generate_techbear_response(
        sanitized_question=question.question_text,
        rolling_context="",
        rag_context="",
    )

    question.llm_draft = draft
    question.draft_generated_at = datetime.utcnow()
    await db.flush()
    await db.refresh(question)
    
    response_data = QuestionResponse.model_validate(question)
    if question.presentation_version:
        response_data.presentation_text = question.presentation_version.display_text
    return response_data