"""
backend/models_v26.py — v2.6 Evaluation and Reporting Models
Ask TechBear — Gymnarctos Studios LLC

Adds persistent evaluation infrastructure on top of the v2.5 pipeline:

    PipelineRun      — one execution of the async pipeline against one question
    PipelineArtifact — generated text artifacts from each pipeline run
    LLMScore         — automated scores from each pipeline phase
    HumanReview      — Jason's evaluation of a pipeline run
    ReviewNote       — structured notes and error categories on a human review

Design principles:
    - One question can have many pipeline runs (v2.5, v2.6, v2.7 results)
    - LLM scores and human scores are stored separately for delta tracking
    - XLSX workbooks are exports, not the source of truth
    - questions.llm_draft is legacy — new answers persist through pipeline_runs
"""
# pylint: disable=not-callable
# E1102 (not-callable): sqlalchemy.sql.func members (e.g. func.now()) are
#   dynamically generated SQL function proxies — Pylint cannot determine
#   they are callable at static analysis time.

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from backend.models import Base


class PipelineRun(Base):
    """
    One execution of the async pipeline against one question.

    Multiple runs per question are expected and supported —
    this is the mechanism for tracking improvement across versions.

    pipeline_version: e.g. "v2.5", "v2.6"
    run_label: optional human label e.g. "baseline", "after_moderation_fix"
    status: pending | running | complete | halted | error
    model_config: JSONB snapshot of which models ran which phases
    retrieval_config: JSONB snapshot of RAG settings at run time
    """

    __tablename__ = "pipeline_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_id = Column(
        Integer,
        ForeignKey("questions.id"),
        nullable=False,
        index=True,
    )
    pipeline_version = Column(String(20), nullable=False)
    run_label = Column(String(100), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    model_config = Column(JSONB, nullable=True)
    retrieval_config = Column(JSONB, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    question = relationship("Question", backref="pipeline_runs")
    artifacts = relationship(
        "PipelineArtifact",
        back_populates="pipeline_run",
        cascade="all, delete-orphan",
    )
    llm_scores = relationship(
        "LLMScore",
        back_populates="pipeline_run",
        cascade="all, delete-orphan",
    )
    human_review = relationship(
        "HumanReview",
        back_populates="pipeline_run",
        uselist=False,
        cascade="all, delete-orphan",
    )


class PipelineArtifact(Base):
    """
    A major generated text artifact from one pipeline run.

    artifact_type values:
        moderation_result | factual_draft | educational_draft |
        voice_draft | semantic_report | character_report |
        editorial_report | handoff_json | final_draft

    metadata: JSONB for phase-specific metadata (word count,
              marker completeness, retrieval chunk count, etc.)
    """

    __tablename__ = "pipeline_artifacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pipeline_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_runs.id"),
        nullable=False,
        index=True,
    )
    artifact_type = Column(String(50), nullable=False)
    content = Column(Text, nullable=True)
    artifact_metadata = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    pipeline_run = relationship("PipelineRun", back_populates="artifacts")


class LLMScore(Base):
    """
    An automated score from one pipeline phase for one pipeline run.

    One row per score dimension per run. Examples:
        score_type="fact_critique"  score_name="accuracy_score"  score_value=8
        score_type="fact_critique"  score_name="safety_score"    score_value=9
        score_type="character_critique" score_name="fidelity"    score_value=7

    pass_recommendation: pass | flag_for_review | escalate_human
    raw_output: full JSONB critique output from the model
    """

    __tablename__ = "llm_scores"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pipeline_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_runs.id"),
        nullable=False,
        index=True,
    )
    score_type = Column(String(50), nullable=False)
    score_name = Column(String(100), nullable=False)
    score_value = Column(Numeric(5, 2), nullable=True)
    pass_recommendation = Column(String(30), nullable=True)
    model = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    raw_output = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    pipeline_run = relationship("PipelineRun", back_populates="llm_scores")


class HumanReview(Base):
    """
    Jason's evaluation of one pipeline run.

    review_status: pending | in_progress | complete | skipped
    publishable: True if the answer is ready to publish as-is or with minor edits
    edit_effort scale:
        0 = publish unchanged
        1 = minor edits (typos, rhythm)
        2 = moderate edits (restructure sections)
        3 = major rewrite (keep facts, redo voice)
        4 = reject / unusable

    Scores use the same 0-10 rubric as LLMScore for delta calculation.
    final_answer: the approved, edited response text (if publishable)
    """

    __tablename__ = "human_reviews"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pipeline_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_runs.id"),
        nullable=False,
        unique=True,  # one human review per pipeline run
        index=True,
    )
    reviewer_name = Column(String(100), nullable=True, default="Jason")
    review_status = Column(String(20), nullable=False, default="pending")
    publishable = Column(Boolean, nullable=True)
    edit_effort = Column(Integer, nullable=True)

    # Human scores — same dimensions as LLMScore for delta tracking
    fact_score = Column(Numeric(5, 2), nullable=True)
    character_score = Column(Numeric(5, 2), nullable=True)
    editorial_score = Column(Numeric(5, 2), nullable=True)
    semantic_score = Column(Numeric(5, 2), nullable=True)
    educational_score = Column(Numeric(5, 2), nullable=True)
    moderation_correct = Column(Boolean, nullable=True)

    final_answer = Column(Text, nullable=True)
    review_notes = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    pipeline_run = relationship("PipelineRun", back_populates="human_review")
    notes = relationship(
        "ReviewNote",
        back_populates="human_review",
        cascade="all, delete-orphan",
    )


class ReviewNote(Base):
    """
    A structured note on a specific issue in a human review.

    note_type values (from v2.6 roadmap):
        missed_claim | unsupported_claim | wrong_retrieval |
        moderation_false_positive | moderation_false_negative |
        too_formulaic | voice_break | too_salesy |
        lore_recall_failure | verbatim_regurgitation |
        excellent_response | publishable_with_minor_edits

    severity: critical | moderate | minor | positive
    suggested_action: free-text recommendation for pipeline improvement
    """

    __tablename__ = "review_notes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    human_review_id = Column(
        UUID(as_uuid=True),
        ForeignKey("human_reviews.id"),
        nullable=False,
        index=True,
    )
    note_type = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=True)
    note = Column(Text, nullable=False)
    suggested_action = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    human_review = relationship("HumanReview", back_populates="notes")
