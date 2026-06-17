"""
services/moderation.py — Content moderation for Ask TechBear
Gymnarctos Studios LLC
"""

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Blocklist


# =============================================================
# Blocklist check — fast, synchronous, fuzzy matching
# =============================================================

async def check_blocklist(
    text: str,
    db: AsyncSession,
    threshold: int = 85,
) -> tuple[bool, str | None]:
    """
    Checks text against the blocklist table using fuzzy matching.
    Returns (is_flagged, matched_term).

    threshold: 0-100, higher = stricter exact match required.
    85 catches near-misses (typos, leetspeak) without being
    overly aggressive on unrelated words.
    """
    result = await db.execute(select(Blocklist))
    blocked_terms = result.scalars().all()

    text_lower = text.lower()

    for entry in blocked_terms:
        term_lower = entry.term.lower()

        # Exact substring match — fastest path, catches most cases
        if term_lower in text_lower:
            return True, entry.term

        # Fuzzy match — catches typos/variations on individual words
        words = text_lower.split()
        for word in words:
            if fuzz.ratio(word, term_lower) >= threshold:
                return True, entry.term

    return False, None


async def seed_default_blocklist(db: AsyncSession):
    """
    Seeds the blocklist with a starter set of terms.
    Run once via script — safe to re-run, skips duplicates.
    """
    default_terms = [
        # Profanity — starter set, expand as needed
        ("fuck", "profanity"),
        ("shit", "profanity"),
        ("bitch", "profanity"),
        ("cunt", "profanity"),
        ("nigger", "profanity"),
        ("faggot", "profanity"),
        # Add more as needed — this is intentionally minimal,
        # the LLM topic filter catches more nuanced cases
    ]

    for term, category in default_terms:
        existing = await db.execute(
            select(Blocklist).where(Blocklist.term == term)
        )
        if existing.scalar_one_or_none() is None:
            db.add(Blocklist(term=term, category=category))

    await db.flush()