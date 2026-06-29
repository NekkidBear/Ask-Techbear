"""
Data loading layer for TechBear benchmarking system.
"""

from sqlalchemy import create_engine, text

DB_URL = "postgresql+psycopg2://postgres:@localhost:5432/ask_techbear"
engine = create_engine(DB_URL)


def load_questions(limit=None, randomize=False):
    """
    Loads benchmark questions from DB in a reproducible way.
    """

    query = """
        SELECT id, attendee_name, question_text
        FROM questions
    """

    if randomize:
        query += " ORDER BY RANDOM()"
    else:
        query += " ORDER BY id"

    params = {}

    if limit:
        query += " LIMIT :limit"
        params["limit"] = limit

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    return [
        {
            "id": r.id,
            "attendee_name": r.attendee_name,
            "question": r.question_text,
            "source": "db"
        }
        for r in rows
    ]
