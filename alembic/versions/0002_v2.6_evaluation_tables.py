"""v2.6 evaluation and reporting tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-25

Adds persistent evaluation infrastructure:
    pipeline_runs       — one execution per question per pipeline version
    pipeline_artifacts  — generated text artifacts per run
    llm_scores          — automated phase scores per run
    human_reviews       — Jason's evaluation per run
    review_notes        — structured issue notes per human review
"""
# pylint: disable=no-member,invalid-name
# E1101 (no-member): alembic.op members are injected at runtime.
# C0103 (invalid-name): Alembic version files use lowercase module-level
#   constants (revision, down_revision, etc.) and numeric filename prefixes
#   by convention — these are not standard Python constants.
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create v2.6 evaluation and reporting tables."""
    # pipeline_runs
    op.create_table(
        "pipeline_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "question_id",
            sa.Integer(),
            sa.ForeignKey("questions.id"),
            nullable=False,
        ),
        sa.Column("pipeline_version", sa.String(20), nullable=False),
        sa.Column("run_label", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_config", postgresql.JSONB(), nullable=True),
        sa.Column("retrieval_config", postgresql.JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_pipeline_runs_question_id",
                    "pipeline_runs", ["question_id"])

    # pipeline_artifacts
    op.create_table(
        "pipeline_artifacts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipeline_runs.id"),
            nullable=False,
        ),
        sa.Column("artifact_type", sa.String(50), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("artifact_metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_pipeline_artifacts_run_id",
        "pipeline_artifacts",
        ["pipeline_run_id"],
    )

    # llm_scores
    op.create_table(
        "llm_scores",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipeline_runs.id"),
            nullable=False,
        ),
        sa.Column("score_type", sa.String(50), nullable=False),
        sa.Column("score_name", sa.String(100), nullable=False),
        sa.Column("score_value", sa.Numeric(5, 2), nullable=True),
        sa.Column("pass_recommendation", sa.String(30), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("raw_output", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_llm_scores_run_id", "llm_scores", ["pipeline_run_id"]
    )

    # human_reviews
    op.create_table(
        "human_reviews",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipeline_runs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("reviewer_name", sa.String(100), nullable=True),
        sa.Column(
            "review_status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("publishable", sa.Boolean(), nullable=True),
        sa.Column("edit_effort", sa.Integer(), nullable=True),
        sa.Column("fact_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("character_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("editorial_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("semantic_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("educational_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("moderation_correct", sa.Boolean(), nullable=True),
        sa.Column("final_answer", sa.Text(), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_human_reviews_run_id", "human_reviews", ["pipeline_run_id"]
    )

    # review_notes
    op.create_table(
        "review_notes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "human_review_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("human_reviews.id"),
            nullable=False,
        ),
        sa.Column("note_type", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("suggested_action", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_review_notes_human_review_id",
        "review_notes",
        ["human_review_id"],
    )


def downgrade() -> None:
    """Drop v2.6 evaluation and reporting tables in reverse dependency order."""
    op.drop_table("review_notes")
    op.drop_table("human_reviews")
    op.drop_table("llm_scores")
    op.drop_table("pipeline_artifacts")
    op.drop_table("pipeline_runs")
