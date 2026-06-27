"""
seed_safe_questions.py — Seed safe test questions
Gymnarctos Studios LLC
"""

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Question


SAFE_QUESTIONS = [
    (
        "What was the most chaotic system you ever had to untangle without completely breaking it?",
        "lore",
    ),
    (
        "What kind of tech problems make you pause before touching anything?",
        "lore",
    ),
    (
        "What are the earliest signs that a system is becoming unstable?",
        "observation",
    ),
    (
        "What does a system under stress look like from your perspective?",
        "observation",
    ),
    (
        "How do you stay calm when everything feels like it’s on fire?",
        "event",
    ),
]


async def seed_safe_questions(db: AsyncSession, session_id: int):
    """Insert the default safe test questions into the database."""
    for q, _category in SAFE_QUESTIONS:
        db.add(
            Question(
                attendee_name="Seeded",
                question_text=q,
                session_id=session_id,
                status="pending",
                highlight=False,
            )
        )

    await db.commit()
