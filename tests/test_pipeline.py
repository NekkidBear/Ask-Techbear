#!/usr/bin/env python3
"""
Ask TechBear v2.6 — Pipeline Test Harness
Gymnarctos Studios LLC

Three-pass pipeline evaluation:
  Pass A — DB questions: lore/observation/event categories
            Tests voice and scope handling, TechBear mythology routing
  Pass B — Corpus questions: verbatim reader questions from published columns
            Tests RAG retrieval quality and factual accuracy
  Pass C — Lore recall questions: Multiverse episode canon
            Tests techbear_lore collection retrieval

Question sets are loaded from the test_questions database table when available,
falling back to hardcoded lists if the DB is unavailable.

Run from repo root:
    python -m tests.test_pipeline [--pass a|b|c|both] [--dry-run] [--question ID]

Output:
    tests/test_output/pipeline_test_results_{timestamp}.json
    tests/test_output/pipeline_test_summary_{timestamp}.txt
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from rapidfuzz import fuzz
from requests.exceptions import RequestException

load_dotenv()

# Pipeline import
try:
    from backend.services.pipeline.orchestrator import run_pipeline as _run_pipeline
    _PIPELINE_AVAILABLE = True
except ImportError:
    _run_pipeline = None
    _PIPELINE_AVAILABLE = False

# DB import — optional, graceful fallback if schema not yet migrated
try:
    from sqlalchemy import select
    from backend.database import get_db_context
    from backend.models_v26 import TestQuestion
    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "tests" / "test_output"
OLLAMA_URL = os.getenv(
    "OLLAMA_BASE_URL", "http://localhost:11434") + "/api/chat"
SIMILARITY_MODEL = os.getenv("SIMILARITY_MODEL", "mistral:latest")


# =============================================================================
# HARDCODED FALLBACK QUESTION SETS
# Used when test_questions table is unavailable or empty.
# Source of truth is the DB — update via seed_test_questions.py.
# =============================================================================

DB_QUESTIONS = [
    {
        "id": "db_001",
        "question": "What was the most chaotic system you ever had to untangle without completely breaking it?",
        "category": "tall_tale",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "tall_tale",
        "notes": (
            "Should trigger TechBear mythology/lore. Expect vivid storytelling, "
            "dust bunnies, legendary service calls. NOT about Jason's personal ADHD workflow."
        ),
    },
    {
        "id": "db_002",
        "question": "What kind of tech problems make you pause before touching anything?",
        "category": "observation",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
        "notes": "Should produce cautious, experienced IT perspective with TechBear voice.",
    },
    {
        "id": "db_003",
        "question": "What are the earliest signs that a system is becoming unstable?",
        "category": "observation",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
        "notes": "Factual + voice. Should mention heat, slowdowns, errors. Check fact accuracy.",
    },
    {
        "id": "db_004",
        "question": "What does a system under stress look like from your perspective?",
        "category": "observation",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
        "notes": "Observational — should blend metaphor with real diagnostics.",
    },
    {
        "id": "db_005",
        "question": "How do you stay calm when everything feels like it's on fire?",
        "category": "event",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
        "notes": "TechBear personality question. Expect warm, performative, practical.",
    },
]

CORPUS_QUESTIONS = [
    {
        "id": "corpus_001",
        "question": (
            "My tech guy keeps going on about the importance of regular backups. "
            "To save money, I've implemented an innovative backup solution for our small business. "
            "Every Friday, I have each employee email themselves important files with the subject "
            "line 'MAYBE IMPORTANT?' Then once a month, I ask everyone to forward those emails to me, "
            "which I save in a special folder called 'Backups I think.' Our efficiency is through the "
            "roof since we only back up on Fridays! Our cloud storage bill is $0! Am I a genius?"
        ),
        "source_post": "Ask The Tech Bear: The Truth About Backups (And 5 Ways to Avoid Catastrophe)",
        "source_url": "https://gymnarctosstudiosllc.com/2025/03/tech-bear-importance-of-regular-backups/",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
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
        "question": (
            "I think I may have cracked the code on modern home wifi networking solutions. "
            "I attached my router to the ceiling fan so it spins around, covering all parts of "
            "the house as it rotates. My internet keeps cutting out every few seconds, but I "
            "think that's probably my ISP's fault."
        ),
        "source_post": "Ask TechBear: 4 Recommendations for Home WiFi Network Solutions That Won't Leave You Spinning",
        "source_url": "https://gymnarctosstudiosllc.com/2025/04/ask-techbear-home-wifi-network-solutions/",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
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
        "question": (
            "I've streamlined our security for maximum efficiency! Our password is 'password' "
            "plus the month (e.g., password4), and they're all on sticky notes under the keyboards. "
            "Nobody has time for complicated passwords! We also use the free antivirus that came "
            "with the computers — it's free, so it's gotta be good, right? And updates? "
            "Ain't nobody got time for that! We're too small to be hacked anyway."
        ),
        "source_post": "Ask The TechBear: 2 Small Business Cybersecurity Dilemmas, 1 Simple Solution",
        "source_url": "https://gymnarctosstudiosllc.com/2025/04/tb-small-business-cybersecurity-dilemmas/",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
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
        "source_post": "Ask Tech Bear: Password Security Fails — A Cybersecurity Comedy of Errors",
        "source_url": "https://gymnarctosstudiosllc.com/2025/04/ask-tb-password-security-fails/",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
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
        "question": (
            "Great news! I've saved a fortune on antivirus software by not using any! "
            "Not only that, but when I got a ransomware message saying my files were encrypted "
            "and demanding $500 to get them back, I negotiated them down to $200 and paid. "
            "Smart business move, right? I think this is a good system."
        ),
        "source_post": "Ask TechBear: The Essential Scoop on Viruses and Ransomware Protection",
        "source_url": "https://gymnarctosstudiosllc.com/2025/04/ask-techbear-ransomware-protection/",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
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
        "question": (
            "I've solved the battery life problem forever! I keep my phone plugged in 24/7 "
            "so it's always at 100%. My phone is only 8 months old and the battery is already "
            "terrible. This must be a defective product."
        ),
        "source_post": "Ask TechBear: Battery Life Bootcamp - The Ultimate Guide to Managing Power",
        "source_url": "https://gymnarctosstudiosllc.com/2025/07/mon-tb-battery-life-management/",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "factual",
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

LORE_QUESTIONS = [
    {
        "id": "lore_001",
        "question": "Have you ever met Captain Janeway?",
        "category": "lore",
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
            "Score 5 = Tom Paris, Talaxian tomatoes, replicator config restore."
        ),
    },
    {
        "id": "lore_002",
        "question": "What happened in the Delta Quadrant?",
        "category": "lore",
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
        "question": "Did you ever visit Discworld?",
        "category": "lore",
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
        "question": "What was the Jurassic Park incident?",
        "category": "lore",
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
        "question": "Is it true you once debugged NASA?",
        "category": "tall_tale",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "tall_tale",
        "key_claims": [
            "tall tale",
            "legendary",
            "origin story",
            "mythology",
        ],
        "notes": (
            "Tall tale question. Expect vivid invented autobiography. "
            "Episode 5 (HAL/NASA) is adjacent canon but this asks about the legend."
        ),
    },
    {
        "id": "lore_006",
        "question": "How did you fix the Millennium Falcon's hyperdrive?",
        "category": "lore",
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
        "question": "What was your most frustrating client ever?",
        "category": "lore",
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
        "notes": "Should trigger Rocky Horror episode (Ep. 1) or tall tale mythology.",
    },
    {
        "id": "lore_008",
        "question": "What's helpdesk water?",
        "category": "lore",
        "expected_scope": "IN_SCOPE",
        "expected_retrieval_mode": "lore",
        "key_claims": [
            "water",
            "messing with the audience",
            "sober",
            "character flavor",
        ],
        "notes": (
            "Helpdesk water canon check. Correct: it's water, TechBear is sober. "
            "Failure mode: model confirms it is an adult beverage, breaking the bit."
        ),
    },
]


# =============================================================================
# DB LOADER
# =============================================================================

async def _load_from_db(pass_label: str) -> list[dict] | None:
    """
    Query test_questions for the given pass label.
    Returns None if unavailable so callers can fall back to hardcoded lists.
    """
    if not _DB_AVAILABLE:
        return None
    try:
        async with get_db_context() as db:
            result = await db.execute(
                select(TestQuestion)
                .where(TestQuestion.pass_label == pass_label.upper())
                .where(TestQuestion.active.is_(True))
                .order_by(TestQuestion.id)
            )
            rows = result.scalars().all()
            if not rows:
                return None
            return [
                {
                    "id": r.id,
                    "question": r.question,
                    "category": r.category or "",
                    "expected_scope": r.expected_scope or "IN_SCOPE",
                    "expected_retrieval_mode": r.expected_retrieval_mode or "factual",
                    "key_claims": r.key_claims or [],
                    "source_post": r.source_post or "",
                    "source_url": r.source_url or "",
                    "notes": r.notes or "",
                }
                for r in rows
            ]
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def load_questions(pass_label: str, fallback: list[dict]) -> list[dict]:
    """Load from DB if available, fall back to hardcoded list."""
    db_questions = asyncio.run(_load_from_db(pass_label))
    if db_questions:
        print(
            f"  [Pass {pass_label}] {len(db_questions)} question(s) from database.")
        return db_questions
    print(
        f"  [Pass {pass_label}] DB unavailable — "
        f"using {len(fallback)} hardcoded question(s)."
    )
    return fallback


# =============================================================================
# SIMILARITY SCORING
# =============================================================================

def score_surface_similarity(pipeline_output: str, reference_claims: list[str]) -> dict:
    """rapidfuzz token_set_ratio against each expected key claim."""
    claim_scores = []
    for claim in reference_claims:
        score = fuzz.token_set_ratio(pipeline_output.lower(), claim.lower())
        claim_scores.append({"claim": claim, "score": score})

    avg = sum(c["score"] for c in claim_scores) / \
        len(claim_scores) if claim_scores else 0
    return {
        "method": "rapidfuzz_token_set_ratio",
        "per_claim": claim_scores,
        "average": round(avg, 1),
        "claims_above_60": sum(1 for c in claim_scores if c["score"] >= 60),
        "claims_above_80": sum(1 for c in claim_scores if c["score"] >= 80),
    }


def score_semantic_similarity(
    pipeline_output: str,
    reference_claims: list[str],
    original_question: str,
) -> dict:
    """LLM semantic judge — checks whether key claims are present in generated output."""
    claims_block = "\n".join(f"- {c}" for c in reference_claims)
    messages = [
        {
            "role": "system",
            "content": (
                "You are a semantic similarity judge for an AI content pipeline. "
                "You receive a generated answer and a list of expected key claims. "
                "For each claim, judge whether the generated answer conveys the same "
                "information, even if phrased differently. "
                "Output ONLY valid JSON. No preamble. No markdown fences."
            ),
        },
        {
            "role": "user",
            "content": f"""Original question:
\"\"\"{original_question}\"\"\"

Generated answer:
\"\"\"{pipeline_output}\"\"\"

Expected key claims:
{claims_block}

Respond with this exact JSON structure:
{{
  "overall_score": <int 0-10>,
  "per_claim": [
    {{
      "claim": "<claim text>",
      "present": <true|false>,
      "confidence": <0.0-1.0>,
      "note": "<brief explanation>"
    }}
  ],
  "summary": "<one-sentence overall assessment>"
}}

overall_score: 10 = all claims present, 0 = none present
"""
        },
    ]

    try:
        response = requests.post(
            OLLAMA_URL,
            json={"model": SIMILARITY_MODEL,
                  "messages": messages, "stream": False},
            timeout=90,
        )
        response.raise_for_status()
        raw = response.json()["message"]["content"].strip()
        if raw.startswith("```"):
            raw = "\n".join(
                line for line in raw.splitlines()
                if not line.strip().startswith("```")
            )
        return {"method": "llm_semantic_judge", **json.loads(raw)}
    except (RequestException, json.JSONDecodeError, ValueError) as exc:
        return {"method": "llm_semantic_judge", "error": str(exc), "overall_score": None}


def score_lore_recall(pipeline_output: str, key_claims: list[str]) -> dict:
    """
    Pass C lore recall scoring — 0-5 scale per lore_bible.md rubric.
    Uses surface similarity as a proxy until a dedicated lore judge exists.

    0 = Generic franchise answer (no TechBear-specific details)
    1 = External reference recognized (correct franchise, no canon)
    2 = TechBear canon referenced (correct episode area)
    3 = Correct story identified
    4 = Key lore details retrieved
    5 = Fresh synthesized answer using canon without verbatim reuse
    """
    surface = score_surface_similarity(pipeline_output, key_claims)
    avg = surface["average"]
    claims_hit = surface["claims_above_60"]
    total_claims = len(key_claims)

    if claims_hit == 0:
        lore_score = 0
    elif claims_hit == 1:
        lore_score = 1
    elif claims_hit / total_claims >= 0.4:
        lore_score = 2
    elif claims_hit / total_claims >= 0.6:
        lore_score = 3
    elif claims_hit / total_claims >= 0.8:
        lore_score = 4
    else:
        lore_score = 5

    return {
        "method": "lore_recall_surface_proxy",
        "lore_score": lore_score,
        "max_score": 5,
        "surface_avg": avg,
        "claims_hit": claims_hit,
        "total_claims": total_claims,
        "note": (
            "Proxy scoring via surface similarity. "
            "Replace with dedicated lore judge once Pass C baseline is established."
        ),
    }


# =============================================================================
# PIPELINE RUNNER
# =============================================================================

def _stage_printer(stage: str) -> None:
    print(f"    → {stage}", flush=True)


def run_question(q: dict, pass_label: str, dry_run: bool = False) -> dict:
    """Run one question through the pipeline and collect all results."""
    submission = {
        "id": q["id"],
        "attendee_name": "TestHarness",
        "question": q["question"],
        "source": f"test_{pass_label}",
        "expected_scope": q.get("expected_scope", "IN_SCOPE"),
        "conversation_depth": 0,
        "rolling_context": "",
        "batch_context": [],
    }

    result = {
        "id": q["id"],
        "pass": pass_label,
        "question": q["question"],
        "category": q.get("category", ""),
        "expected_scope": q.get("expected_scope", ""),
        "expected_retrieval_mode": q.get("expected_retrieval_mode", ""),
        "notes": q.get("notes", ""),
        "source_post": q.get("source_post", ""),
        "pipeline_result": None,
        "pipeline_error": None,
        "voice_draft": None,
        "factual_draft": None,
        "actual_retrieval_mode": None,
        "scores": {},
        "similarity": {},
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }

    if dry_run:
        result["pipeline_result"] = "DRY_RUN_SKIPPED"
        return result

    if not _PIPELINE_AVAILABLE or _run_pipeline is None:
        result["pipeline_result"] = "exception"
        result["pipeline_error"] = "Pipeline not available — check installation"
        return result

    try:
        artifact = _run_pipeline(submission, on_stage=_stage_printer)
        result["pipeline_result"] = "complete" if artifact.get(
            "passed") else "halted"
        result["pipeline_error"] = artifact.get("failure_reason")
        result["voice_draft"] = artifact.get("drafts", {}).get("voice", "")
        result["factual_draft"] = artifact.get("drafts", {}).get("factual", "")
        result["scores"] = artifact.get("scores", {})
        result["flags"] = artifact.get("flags", {})
        result["loop_counts"] = artifact.get("loop_counts", {})

        # Capture actual retrieval mode for routing validation
        result["actual_retrieval_mode"] = (
            artifact.get("retrieval", {}).get("retrieval_mode")
            or artifact.get("scores", {}).get("moderation", {}).get("retrieval_mode")
        )

        # Routing check — flag if moderation routed differently than expected
        if (
            result["expected_retrieval_mode"]
            and result["actual_retrieval_mode"]
            and result["expected_retrieval_mode"] != result["actual_retrieval_mode"]
        ):
            result["routing_mismatch"] = (
                f"expected={result['expected_retrieval_mode']} "
                f"actual={result['actual_retrieval_mode']}"
            )

        # Similarity scoring
        key_claims = q.get("key_claims", [])
        if key_claims and result["voice_draft"]:
            output_text = result["voice_draft"]
            if pass_label == "B":
                result["similarity"]["surface"] = score_surface_similarity(
                    output_text, key_claims
                )
                result["similarity"]["semantic"] = score_semantic_similarity(
                    output_text, key_claims, q["question"]
                )
            elif pass_label == "C":
                result["similarity"]["lore_recall"] = score_lore_recall(
                    output_text, key_claims
                )

    except (RuntimeError, ValueError, OSError) as exc:
        result["pipeline_result"] = "exception"
        result["pipeline_error"] = str(exc)

    return result


# =============================================================================
# SUMMARY WRITER
# =============================================================================

def write_summary(results: list[dict], output_path: Path) -> None:
    """Write a human-readable summary of pipeline test results."""
    lines = [
        "Ask TechBear v2.6 — Pipeline Test Summary",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "=" * 60,
        "",
    ]

    for r in results:
        lines.append(f"[{r['pass']}] {r['id']} — {r['pipeline_result']}")
        if r.get("category"):
            lines.append(f"  Category: {r['category']}")
        if r.get("source_post"):
            lines.append(f"  Source: {r['source_post'][:65]}")
        lines.append(f"  Q: {r['question'][:100]}...")

        # Routing mismatch warning
        if r.get("routing_mismatch"):
            lines.append(f"  ⚠ ROUTING MISMATCH: {r['routing_mismatch']}")

        if r.get("pipeline_error"):
            lines.append(f"  ERROR: {r.get('pipeline_error')}")

        if r.get("voice_draft"):
            lines.append(
                f"  Voice draft ({len(r['voice_draft'].split())} words):")
            lines.append(f"    {r['voice_draft'][:300]}...")

        # Loop counts
        lc = r.get("loop_counts", {})
        if lc:
            lines.append(
                f"  Loop counts: factual={lc.get('factual', 0)} voice={lc.get('voice', 0)}"
            )

        sim = r.get("similarity", {})
        if sim.get("surface"):
            s = sim["surface"]
            lines.append(
                f"  Surface similarity: avg={s['average']} | "
                f"claims≥60: {s['claims_above_60']}/{len(s['per_claim'])} | "
                f"claims≥80: {s['claims_above_80']}/{len(s['per_claim'])}"
            )
        if sim.get("semantic") and sim["semantic"].get("overall_score") is not None:
            lines.append(
                f"  Semantic similarity: {sim['semantic']['overall_score']}/10 — "
                f"{sim['semantic'].get('summary', '')}"
            )
        if sim.get("lore_recall"):
            lr = sim["lore_recall"]
            lines.append(
                f"  Lore recall: {lr['lore_score']}/5 "
                f"(claims hit: {lr['claims_hit']}/{lr['total_claims']})"
            )

        scores = r.get("scores", {})
        if scores.get("fact_critique"):
            fc = scores["fact_critique"]
            lines.append(
                f"  Fact critique: accuracy={fc.get('accuracy_score')} "
                f"safety={fc.get('safety_score')} rec={fc.get('pass_recommendation')}"
            )
        if scores.get("character_critique"):
            cc = scores["character_critique"]
            lines.append(
                f"  Character critique: fidelity={cc.get('character_fidelity_score')} "
                f"anti_formulaic={cc.get('anti_formulaic_score')} "
                f"words={cc.get('word_count')}"
            )
        if scores.get("educational_critique"):
            ec = scores["educational_critique"]
            lines.append(
                f"  Educational critique: "
                f"comprehension={ec.get('comprehension_confidence')} "
                f"concept={ec.get('concept_clarity')} "
                f"analogy={ec.get('analogy_quality')} "
                f"action={ec.get('action_clarity')} "
                f"transfer={ec.get('transfer_potential')}"
            )
        if scores.get("editorial_critique"):
            ec = scores["editorial_critique"]
            fk = ec.get("flesch_kincaid", {})
            lines.append(
                f"  Editorial critique: clarity={ec.get('clarity_score')} "
                f"FK={fk.get('flesch_kincaid_score')} (in range: {fk.get('in_range')})"
            )

        lines.append("")

    # Aggregate
    lines.append("=" * 60)
    lines.append("AGGREGATE")
    total = len(results)
    complete = sum(1 for r in results if r["pipeline_result"] == "complete")
    halted = sum(1 for r in results if r["pipeline_result"] == "halted")
    errors = sum(1 for r in results if r["pipeline_result"] == "exception")
    routing_mismatches = sum(1 for r in results if r.get("routing_mismatch"))
    lines.append(
        f"Total: {total} | Complete: {complete} | Halted: {halted} | Errors: {errors}"
    )
    if routing_mismatches:
        lines.append(f"Routing mismatches: {routing_mismatches}")

    # Pass B similarity
    b_results = [r for r in results if r["pass"]
                 == "B" and r.get("similarity")]
    if b_results:
        avg_surface = sum(
            r["similarity"]["surface"]["average"]
            for r in b_results if r["similarity"].get("surface")
        ) / len(b_results)
        sem_scores = [
            r["similarity"]["semantic"]["overall_score"]
            for r in b_results
            if r["similarity"].get("semantic", {}).get("overall_score") is not None
        ]
        lines.append(f"Pass B avg surface similarity: {round(avg_surface, 1)}")
        if sem_scores:
            lines.append(
                f"Pass B avg semantic similarity: {round(sum(sem_scores)/len(sem_scores), 1)}/10"
            )

    # Pass C lore recall
    c_results = [
        r for r in results
        if r["pass"] == "C" and r.get("similarity", {}).get("lore_recall")
    ]
    if c_results:
        avg_lore = sum(
            r["similarity"]["lore_recall"]["lore_score"] for r in c_results
        ) / len(c_results)
        lines.append(f"Pass C avg lore recall: {round(avg_lore, 1)}/5")

    output_path.write_text("\n".join(lines), encoding="utf-8")


# =============================================================================
# ENTRY POINT
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ask TechBear v2.6 pipeline test harness"
    )
    parser.add_argument(
        "--pass",
        dest="run_pass",
        choices=["a", "b", "c", "both"],
        default="both",
        help="Which pass to run: a (DB/lore), b (corpus), c (lore recall), both",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate setup and question sets without hitting Ollama",
    )
    parser.add_argument(
        "--question",
        dest="question_id",
        default=None,
        help="Run a single question by ID (e.g. db_003, corpus_002, lore_001)",
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    questions_to_run = []

    if args.question_id:
        all_q = {q["id"]: (q, "A") for q in DB_QUESTIONS}
        all_q.update({q["id"]: (q, "B") for q in CORPUS_QUESTIONS})
        all_q.update({q["id"]: (q, "C") for q in LORE_QUESTIONS})
        if args.question_id not in all_q:
            print(f"Unknown question ID: {args.question_id}")
            print(f"Available: {list(all_q.keys())}")
            sys.exit(1)
        q, p = all_q[args.question_id]
        questions_to_run = [(q, p)]
    else:
        if args.run_pass in ("a", "both"):
            questions_to_run += [
                (q, "A") for q in load_questions("A", DB_QUESTIONS)
            ]
        if args.run_pass in ("b", "both"):
            questions_to_run += [
                (q, "B") for q in load_questions("B", CORPUS_QUESTIONS)
            ]
        if args.run_pass in ("c", "both"):
            questions_to_run += [
                (q, "C") for q in load_questions("C", LORE_QUESTIONS)
            ]

    print(
        f"Running {len(questions_to_run)} question(s) "
        f"{'[DRY RUN]' if args.dry_run else ''}..."
    )
    print()

    results = []
    for i, (q, p) in enumerate(questions_to_run, 1):
        print(
            f"[{i}/{len(questions_to_run)}] {q['id']} ({p})  {q['question'][:60]}...")
        r = run_question(q, p, dry_run=args.dry_run)
        results.append(r)
        status = r["pipeline_result"]
        routing = f" ⚠ routing={r['routing_mismatch']}" if r.get(
            "routing_mismatch") else ""
        print(f"  → {status}{routing}")
        if r.get("pipeline_error"):
            print(f"  ⚠ {r['pipeline_error'][:80]}")
        print()

    json_path = OUTPUT_DIR / f"pipeline_test_results_{timestamp}.json"
    summary_path = OUTPUT_DIR / f"pipeline_test_summary_{timestamp}.txt"

    json_path.write_text(json.dumps(
        results, indent=2, ensure_ascii=False), encoding="utf-8")
    write_summary(results, summary_path)

    print(f"Results: {json_path}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
