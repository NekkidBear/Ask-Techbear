"""
backend/scripts/seed_test_questions.py — Test Question Seeder
Ask TechBear — Gymnarctos Studios LLC

Migrates hardcoded Pass A and Pass B questions from tests/test_pipeline.py
into the test_questions database table, and adds Pass C lore recall questions.

Safe to re-run — uses INSERT ... ON CONFLICT DO NOTHING so existing rows
are not overwritten.

Usage (from repo root):
    python -m backend.scripts.seed_test_questions
    python -m backend.scripts.seed_test_questions --dry-run
"""

import argparse
import asyncio
import logging
import sys

from sqlalchemy import text

from backend.database import get_db_context
from backend.models_v26 import TestQuestion

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================
# PASS A — DB / lore / observation questions
# (migrated from tests/test_pipeline.py DB_QUESTIONS)
# =============================================================

PASS_A = [
    {
        "id": "db_001",
        "pass_label": "A",
        "category": "tall_tale",
        "question": (
            "What was the most chaotic system you ever had to untangle "
            "without completely breaking it?"
        ),
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "tall_tale",
        "notes": (
            "Should trigger TechBear mythology/lore. Expect vivid storytelling, "
            "dust bunnies, legendary service calls. NOT about Jason's personal ADHD workflow."
        ),
    },
    {
        "id": "db_002",
        "pass_label": "A",
        "category": "observation",
        "question": "What kind of tech problems make you pause before touching anything?",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
        "notes": (
            "Should produce cautious, experienced IT perspective with TechBear voice. "
            "Expect references to unknown USB drives, suspicious hardware, social engineering."
        ),
    },
    {
        "id": "db_003",
        "pass_label": "A",
        "category": "observation",
        "question": "What are the earliest signs that a system is becoming unstable?",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
        "notes": (
            "Factual + voice. Should mention heat, slowdowns, errors, fan noise. "
            "Check fact accuracy against corpus."
        ),
    },
    {
        "id": "db_004",
        "pass_label": "A",
        "category": "observation",
        "question": "What does a system under stress look like from your perspective?",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
        "notes": "Observational — should blend metaphor with real diagnostics.",
    },
    {
        "id": "db_005",
        "pass_label": "A",
        "category": "event",
        "question": "How do you stay calm when everything feels like it's on fire?",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
        "notes": "TechBear personality question. Expect warm, performative, practical.",
    },
]


# =============================================================
# PASS B — corpus / RAG questions
# (migrated from tests/test_pipeline.py CORPUS_QUESTIONS)
# =============================================================

PASS_B = [
    {
        "id": "corpus_001",
        "pass_label": "B",
        "category": "corpus",
        "question": (
            "My tech guy keeps going on about the importance of regular backups. "
            "To save money, I've implemented an innovative backup solution for our small business. "
            "Every Friday, I have each employee email themselves important files with the subject "
            "line 'MAYBE IMPORTANT?' Then once a month, I ask everyone to forward those emails to me, "
            "which I save in a special folder called 'Backups I think.' Our efficiency is through the "
            "roof since we only back up on Fridays! Our cloud storage bill is $0! Am I a genius?"
        ),
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
        "source_post": "Ask The Tech Bear: The Truth About Backups (And 5 Ways to Avoid Catastrophe)",
        "source_url": "https://gymnarctosstudiosllc.com/2025/03/tech-bear-importance-of-regular-backups/",
        "key_claims": [
            "email is not a backup solution",
            "3-2-1 backup rule",
            "human error in manual backup processes",
            "automated backups are more reliable",
            "cloud storage backup costs are worth it",
        ],
        "notes": "High RAG retrieval expectation — backup article is in corpus.",
    },
    {
        "id": "corpus_002",
        "pass_label": "B",
        "category": "corpus",
        "question": (
            "I think I may have cracked the code on modern home wifi networking solutions. "
            "I attached my router to the ceiling fan so it spins around, covering all parts of "
            "the house as it rotates. My internet keeps cutting out every few seconds, but I "
            "think that's probably my ISP's fault."
        ),
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
        "source_post": "Ask TechBear: 4 Recommendations for Home WiFi Network Solutions That Won't Leave You Spinning",
        "source_url": "https://gymnarctosstudiosllc.com/2025/04/ask-techbear-home-wifi-network-solutions/",
        "key_claims": [
            "rotating router disrupts signal",
            "router placement matters — central, elevated, stationary",
            "ISP is not at fault here",
            "wifi signal needs stable positioning",
        ],
        "notes": "Should produce a firm but warm correction with router placement advice.",
    },
    {
        "id": "corpus_003",
        "pass_label": "B",
        "category": "corpus",
        "question": (
            "I've streamlined our security for maximum efficiency! Our password is 'password' "
            "plus the month (e.g., password4), and they're all on sticky notes under the keyboards. "
            "Nobody has time for complicated passwords! We also use the free antivirus that came "
            "with the computers — it's free, so it's gotta be good, right? And updates? "
            "Ain't nobody got time for that! We're too small to be hacked anyway."
        ),
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
        "source_post": "Ask The TechBear: 2 Small Business Cybersecurity Dilemmas, 1 Simple Solution",
        "source_url": "https://gymnarctosstudiosllc.com/2025/04/tb-small-business-cybersecurity-dilemmas/",
        "key_claims": [
            "predictable passwords are easily cracked",
            "sticky notes are a physical security risk",
            "free bundled antivirus is insufficient",
            "updates patch known vulnerabilities",
            "small businesses are common targets",
            "password manager recommendation",
        ],
        "notes": "Classic TechBear territory — should produce high character fidelity.",
    },
    {
        "id": "corpus_004",
        "pass_label": "B",
        "category": "corpus",
        "question": (
            "I've created the perfect password system, but my former IT guy said these practices "
            "are a password security failure. All of our company passwords are 'password' followed "
            "by the current month number. We change them monthly for security! Plus, I've created "
            "a shared spreadsheet on my company's network drive so everyone can see it. It was a "
            "hassle having to remember a password to open it, so I just left it in plain text. "
            "My IT support guy quit last week for unrelated reasons. Before the spreadsheet, "
            "everyone just wrote their passwords on sticky notes. I think the spreadsheet is a "
            "security improvement. How impressed should I be with myself?"
        ),
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
        "source_post": "Ask Tech Bear: Password Security Fails — A Cybersecurity Comedy of Errors",
        "source_url": "https://gymnarctosstudiosllc.com/2025/04/ask-tb-password-security-fails/",
        "key_claims": [
            "predictable password rotation does not improve security",
            "plain text password storage is dangerous",
            "shared credentials spreadsheet is a single point of failure",
            "the IT guy's departure may not be unrelated",
            "password manager as the correct solution",
            "multi-factor authentication",
        ],
        "notes": "IT guy departure line should be acknowledged with flair.",
    },
    {
        "id": "corpus_005",
        "pass_label": "B",
        "category": "corpus",
        "question": (
            "Great news! I've saved a fortune on antivirus software by not using any! "
            "Not only that, but when I got a ransomware message saying my files were encrypted "
            "and demanding $500 to get them back, I negotiated them down to $200 and paid. "
            "Smart business move, right? I think this is a good system."
        ),
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
        "source_post": "Ask TechBear: The Essential Scoop on Viruses and Ransomware Protection",
        "source_url": "https://gymnarctosstudiosllc.com/2025/04/ask-techbear-ransomware-protection/",
        "key_claims": [
            "paying ransomware is never recommended",
            "paying marks you as a repeat target",
            "no guarantee files are restored after payment",
            "antivirus/endpoint protection is essential",
            "backups are the correct ransomware defense",
            "report ransomware to authorities",
        ],
        "notes": "Safety-critical. Fact critique should score this carefully.",
    },
    {
        "id": "corpus_006",
        "pass_label": "B",
        "category": "corpus",
        "question": (
            "I've solved the battery life problem forever! I keep my phone plugged in 24/7 "
            "so it's always at 100%. My phone is only 8 months old and the battery is already "
            "terrible. This must be a defective product."
        ),
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
        "source_post": "Ask TechBear: Battery Life Bootcamp - The Ultimate Guide to Managing Power",
        "source_url": "https://gymnarctosstudiosllc.com/2025/07/mon-tb-battery-life-management/",
        "key_claims": [
            "keeping battery at 100% degrades lithium-ion cells",
            "optimal charge range is 20-80%",
            "heat from constant charging accelerates degradation",
            "this is not a defective product",
            "battery calibration and management tips",
        ],
        "notes": "Should debunk the myth while validating their frustration.",
    },
    {
        "id": "corpus_007",
        "pass_label": "B",
        "category": "corpus",
        "question": (
            "I'm lost in all the apps/services. What are the names and how do I sign up "
            "for them. Do I need to sign an NDA? or make a deal with the devil?"
        ),
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
        "key_claims": [
            "password manager recommendation",
            "two-factor authentication",
            "no NDA required",
            "start with one tool at a time",
            "home router default password",
        ],
        "notes": (
            "Mixed-intent: genuine IN_SCOPE question with embedded OFF_TOPIC_FUN framing. "
            "Moderation should pass. Voice pass should acknowledge NDA/devil joke once as "
            "a callback, not let it hijack the answer. Known failure: previous v1 draft "
            "focused on NDAs and devil deals instead of answering the question."
        ),
    },
]


# =============================================================
# PASS C — lore recall questions
# Tests techbear_lore collection retrieval
# Scoring: 0-5 scale (see lore_bible.md Pass C rubric)
# =============================================================

PASS_C = [
    {
        "id": "lore_001",
        "pass_label": "C",
        "category": "lore",
        "question": "Have you ever met Captain Janeway?",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "lore",
        "key_claims": [
            "Coffee Crisis in the Delta Quadrant",
            "replicator malfunction",
            "Tom Paris was responsible",
            "Talaxian tomato plant matrices",
            "70,000 light years from Earth",
        ],
        "notes": (
            "Primary lore calibration case. Score 0 = generic Trek answer. "
            "Score 5 = Tom Paris, Talaxian tomatoes, replicator config restore. "
            "Failure here after lore ingestion indicates generation failure, not retrieval."
        ),
    },
    {
        "id": "lore_002",
        "pass_label": "C",
        "category": "lore",
        "question": "What happened in the Delta Quadrant?",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "lore",
        "key_claims": [
            "coffee replicator",
            "Tom Paris",
            "Talaxian tomatoes",
            "Voyager",
            "interdimensional tech trade",
        ],
        "notes": "Follow-up framing of lore_001. Should retrieve same episode.",
    },
    {
        "id": "lore_003",
        "pass_label": "C",
        "category": "lore",
        "question": "Did you ever visit Discworld?",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "lore",
        "key_claims": [
            "Unseen University",
            "HEX",
            "Archchancellor Ridcully",
            "existential computing",
            "thaumic interference",
            "Magic/More Magic switch",
        ],
        "notes": "Episode 8. Should retrieve Discworld/HEX episode specifically.",
    },
    {
        "id": "lore_004",
        "pass_label": "C",
        "category": "lore",
        "question": "What was the Jurassic Park incident?",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "lore",
        "key_claims": [
            "Dennis Nedry",
            "security system",
            "coffee maker outlet",
            "biotech startup",
            "torrential rain",
        ],
        "notes": "Episode 2. Kevin archetype — Nedry disabled security for a coffee maker outlet.",
    },
    {
        "id": "lore_005",
        "pass_label": "C",
        "category": "lore",
        "question": "Is it true you once debugged NASA?",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "tall_tale",
        "key_claims": [
            "tall tale",
            "legendary",
            "origin story",
            "mythology",
        ],
        "notes": (
            "Tall tale question — should trigger lore/mythology response, not factual. "
            "Episode 5 (HAL/NASA) is the closest canon but this is asking about the legend, "
            "not the specific episode. Expect vivid invented autobiography."
        ),
    },
    {
        "id": "lore_006",
        "pass_label": "C",
        "category": "lore",
        "question": "How did you fix the Millennium Falcon's hyperdrive?",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "lore",
        "key_claims": [
            "Mos Eisley",
            "Imperial encryption",
            "Docking Bay 94",
            "Chewbacca",
            "disco era",
        ],
        "notes": "Episode 4. Star Wars. Should retrieve specific episode details.",
    },
    {
        "id": "lore_007",
        "pass_label": "C",
        "category": "lore",
        "question": "What was your most frustrating client ever?",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "tall_tale",
        "key_claims": [
            "Frank N. Furter",
            "Rocky Horror",
            "deeply regrets",
            "lingerie",
            "fish tank",
            "Eddie",
        ],
        "notes": (
            "Should trigger Rocky Horror episode (Ep. 1) or tall tale mythology. "
            "Frank N. Furter is established canon as a former client TechBear deeply regrets."
        ),
    },
    {
        "id": "lore_008",
        "pass_label": "C",
        "category": "lore",
        "question": "What's helpdesk water?",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "lore",
        "key_claims": [
            "water",
            "messing with the audience",
            "sober",
            "character flavor",
        ],
        "notes": (
            "Helpdesk water canon check. Correct answer: it's water, TechBear is sober "
            "and playing the ambiguity for laughs. Failure mode: model confirms it is an "
            "adult beverage, breaking the bit."
        ),
    },
]


# =============================================================
# Seeder
# =============================================================

ALL_QUESTIONS = PASS_A + PASS_B + PASS_C


async def seed(dry_run: bool = False) -> None:
    """Insert all questions using ON CONFLICT DO NOTHING."""

    if dry_run:
        print(f"DRY RUN — would insert {len(ALL_QUESTIONS)} questions:")
        for q in ALL_QUESTIONS:
            print(f"  [{q['pass_label']}] {q['id']} — {q['question'][:60]}...")
        return

    async with get_db_context() as db:
        # Ensure table exists (create_all is idempotent)
        # In production use Alembic migrations instead
        inserted = 0
        skipped = 0

        for q in ALL_QUESTIONS:
            existing = await db.get(TestQuestion, q["id"])
            if existing:
                skipped += 1
                continue

            row = TestQuestion(
                id=q["id"],
                pass_label=q["pass_label"],
                category=q.get("category"),
                question=q["question"],
                expected_scope=q.get("expected_scope"),
                expected_retrieval_mode=q.get("expected_retrieval_mode"),
                key_claims=q.get("key_claims"),
                source_post=q.get("source_post"),
                source_url=q.get("source_url"),
                notes=q.get("notes"),
                active=True,
            )
            db.add(row)
            inserted += 1

        await db.commit()
        logger.info(
            "Seeded %d question(s), skipped %d existing.", inserted, skipped
        )

    print()
    print(f"Test question seed complete.")
    print(f"  Inserted: {inserted}")
    print(f"  Skipped (already present): {skipped}")
    print(f"  Total in table: {len(ALL_QUESTIONS)}")
    print()
    print("Pass breakdown:")
    for label in ("A", "B", "C"):
        count = sum(1 for q in ALL_QUESTIONS if q["pass_label"] == label)
        print(f"  Pass {label}: {count} question(s)")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed test_questions table from hardcoded question sets"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print questions that would be inserted without touching the DB",
    )
    args = parser.parse_args()

    asyncio.run(seed(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
