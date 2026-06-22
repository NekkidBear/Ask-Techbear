"""
Data loading layer for TechBear benchmarking system.

This module provides a clean abstraction over the underlying PostgreSQL
database and is responsible for retrieving evaluation datasets used in
model benchmarking.

Primary responsibilities:
- Load benchmark questions from the Ask TechBear database
- Normalize database schema into a consistent internal format
- Provide a stable interface for downstream benchmarking pipelines

The module intentionally avoids any evaluation logic, prompt construction,
or model inference responsibilities in order to maintain separation of
concerns between data access and experiment execution.

Expected schema (questions table):
    id (int)
    attendee_name (text)
    question_text (text)

All returned records are normalized to:
    {
        "id": int,
        "attendee_name": str,
        "question": str,
        "source": "db"
    }
"""
from sqlalchemy import create_engine, text

DB_URL = "postgresql+psycopg2://postgres:@localhost:5432/ask_techbear"
engine = create_engine(DB_URL)


def load_questions(limit=None):
    """
    Loads benchmark questions from DB.
    Matches schema:
        id | attendee_name | question_text
    """
    query = """
        SELECT id, attendee_name, question_text
        FROM questions
        ORDER BY id
    """

    if limit:
        query += f" LIMIT {limit}"

    with engine.connect() as conn:
        rows = conn.execute(text(query)).fetchall()

    return [
        {
            "id": r.id,
            "attendee_name": r.attendee_name,
            "question": r.question_text,
            "source": "db"
        }
        for r in rows
    ]
