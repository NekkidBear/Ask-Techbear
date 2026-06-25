"""Initial schema — existing v2.5 tables

Revision ID: 0001
Revises:
Create Date: 2026-06-25

Captures the existing schema created by SQLAlchemy create_all()
prior to Alembic being introduced. This migration is a baseline
snapshot — if the tables already exist, it will no-op cleanly.

Tables captured:
    sessions
    questions
    presentation_versions
    session_context
    blocklist
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # sessions
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("event_name", sa.String(200), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=True),
    )

    # questions
    op.create_table(
        "questions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column("attendee_name", sa.String(100), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("moderation_flag", sa.String(50), nullable=True),
        sa.Column("llm_draft", sa.Text(), nullable=True),
        sa.Column(
            "draft_generated_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("highlight", sa.Boolean(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=True),
    )

    # presentation_versions
    op.create_table(
        "presentation_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "question_id",
            sa.Integer(),
            sa.ForeignKey("questions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("display_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # session_context
    op.create_table(
        "session_context",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column(
            "answered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )

    # blocklist
    op.create_table(
        "blocklist",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("term", sa.String(200), nullable=False, unique=True),
        sa.Column("category", sa.String(50), nullable=True),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("blocklist")
    op.drop_table("session_context")
    op.drop_table("presentation_versions")
    op.drop_table("questions")
    op.drop_table("sessions")
