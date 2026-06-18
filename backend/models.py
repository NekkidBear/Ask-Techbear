# =============================================================
# models.py — SQLAlchemy database models for Ask TechBear
# Gymnarctos Studios LLC
# =============================================================

from sqlalchemy import (
    Column, Integer, String, Text, Boolean,
    DateTime, ForeignKey
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func
import uuid


class Base(DeclarativeBase):
    pass


class Session(Base):
    """
    Represents one tabling event / live show session.
    A new session is created at the start of each event.
    """
    __tablename__ = "sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_name = Column(String(200), nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)
    active = Column(Boolean, default=True)

    # Relationship — all questions belonging to this session
    questions = relationship("Question", back_populates="session")
    context_entries = relationship("SessionContext", back_populates="session")


class Question(Base):
    """
    A question submitted by an attendee via the public form.
    Tracks the full lifecycle: submitted → moderated → drafted → answered → highlighted
    """
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False)
    attendee_name = Column(String(100), nullable=False)
    question_text = Column(Text, nullable=False)
    submitted_at = Column(DateTime(timezone=True), server_default=func.now())

    # Moderation
    status = Column(
        String(20),
        default="pending"
        # pending | approved | rejected | answered | highlighted
    )
    moderation_flag = Column(String(50), nullable=True)  # reason if rejected

    # LLM draft response
    llm_draft = Column(Text, nullable=True)
    draft_generated_at = Column(DateTime(timezone=True), nullable=True)

    # Performance tracking
    answered_at = Column(DateTime(timezone=True), nullable=True)

    # Slideshow
    highlight = Column(Boolean, default=False)
    display_order = Column(Integer, nullable=True)

    # Relationships
    session = relationship("Session", back_populates="questions")
    presentation_version = relationship(
        "PresentationVersion",
        back_populates="question",
        uselist=False,   # one-to-one
        cascade="all, delete-orphan",
    )

    @property
    def presentation_text(self):
        """
        Returns the display-optimized text if one exists,
        otherwise None (callers fall back to llm_draft).
        """
        if self.presentation_version:
            return self.presentation_version.display_text
        return None


class PresentationVersion(Base):
    """
    Display-optimized version of a TechBear response for slideshow mode.

    Linked to the original question via FK — never modifies source text.
    Profanity is replaced with asterisk sequences; long responses are
    summarized while preserving TechBear's voice and factual accuracy.
    Formatted with line breaks and visual hierarchy for walk-by readability.

    Created manually via seed script or moderator dashboard; updated
    whenever a highlighted response needs re-editing for display.
    """
    __tablename__ = "presentation_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    question_id = Column(
        Integer,
        ForeignKey("questions.id"),
        nullable=False,
        unique=True,   # one presentation version per question
    )
    display_text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    question = relationship("Question", back_populates="presentation_version")


class SessionContext(Base):
    """
    Rolling context window for the LLM.
    Stores the last N answered Q&As so TechBear stays consistent
    within a session and doesn't contradict earlier answers.
    """
    __tablename__ = "session_context"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False)
    question_text = Column(Text, nullable=False)
    # Note: stores the PERFORMED response, not the LLM draft
    # Jason may ad-lib during performance — we capture what was actually said
    response_text = Column(Text, nullable=False)
    answered_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("Session", back_populates="context_entries")


class Blocklist(Base):
    """
    Moderation blocklist — terms that trigger automatic rejection.
    Editable at runtime via the moderator dashboard.
    """
    __tablename__ = "blocklist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    term = Column(String(200), nullable=False, unique=True)
    category = Column(
        String(50),
        nullable=True
        # profanity | competitor | topic | custom
    )
    added_at = Column(DateTime(timezone=True), server_default=func.now())