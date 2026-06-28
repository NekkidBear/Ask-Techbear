"""Add attendee_email to questions

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-27

Adds optional attendee_email to the questions table to support
async answer delivery. Email is nullable — collection is opt-in
at submission time.

Privacy note: attendee_email is PII and must be excluded from
all benchmark exports, XLSX outputs, and corpus pipeline queries.
The approval action in pipeline_review.py consumes it directly
and logs delivery as a pipeline_artifact row (no email address
stored in the artifact).
"""
# pylint: disable=no-member,invalid-name
# E1101 (no-member): alembic.op members are injected at runtime.
# C0103 (invalid-name): Alembic version files use lowercase module-level
#   constants (revision, down_revision, etc.) and numeric filename prefixes
#   by convention — these are not standard Python constants.
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add attendee_email column to questions."""
    op.add_column(
        "questions",
        sa.Column(
            "attendee_email",
            sa.Text(),    # Fernet ciphertext of a 254-char email is ~400 chars;
                          # TEXT avoids a length constraint on the encrypted value.
                          # The 254-char max is enforced in the application layer
                          # (QuestionSubmit.email_valid validator) before encryption.
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Remove attendee_email column from questions."""
    op.drop_column("questions", "attendee_email")
