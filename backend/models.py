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

    # Relationship back to session
    session = relationship("Session", back_populates="questions")


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