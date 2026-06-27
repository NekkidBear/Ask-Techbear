"""
services/moderation.py — Content moderation for Ask TechBear
Gymnarctos Studios LLC
"""

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Blocklist


# =============================================================
# Blocklist check — fast, synchronous, fuzzy + prefix matching
# =============================================================

async def check_blocklist(
    text: str,
    db: AsyncSession,
    threshold: int = 85,
) -> tuple[bool, str | None]:
    """
    Checks text against the blocklist table using exact substring,
    prefix, and fuzzy matching. Returns (is_flagged, matched_term).

    threshold: 0-100, higher = stricter exact match required for
    the fuzzy pass. 85 catches near-misses (typos, leetspeak)
    without being overly aggressive on unrelated words.
    """
    result = await db.execute(select(Blocklist))
    blocked_terms = result.scalars().all()

    text_lower = text.lower()
    words = text_lower.split()

    for entry in blocked_terms:
        term_lower = entry.term.lower()

        # Stage 1 — exact substring match, fastest path
        if term_lower in text_lower:
            return True, entry.term

        for word in words:
            # Strip common trailing punctuation so "shit?" or "shit," match
            cleaned_word = word.strip('.,!?;:"\'')

            # Stage 2 — prefix match, catches word-form variants
            # (-ing, -er, -s, -ed) without needing every variant listed.
            # Length guard prevents short terms from over-matching
            # (e.g. a 2-letter blocked term matching half the dictionary).
            if len(term_lower) >= 3 and cleaned_word.startswith(term_lower):
                return True, entry.term

            # Stage 3 — fuzzy match, catches typos/obfuscation
            if fuzz.ratio(cleaned_word, term_lower) >= threshold:
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
        # Clipped/short forms — added after testing showed truncated slurs
        # (e.g. "fag" from "faggot") aren't caught by substring, prefix,
        # or fuzzy matching against the longer term. Fuzzy match scores
        # drop fast as the relative length difference grows, so these
        # need their own explicit entries rather than relying on the
        # existing terms to generalize down.
        ("fag", "profanity"),
        ("nig", "profanity"),
        ("nigga", "profanity"),
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
