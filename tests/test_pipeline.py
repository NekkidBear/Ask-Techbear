#!/usr/bin/env python3
"""
Ask TechBear v2.5 — Pipeline Test Harness
Gymnarctos Studios LLC

Two-pass pipeline evaluation:
  Pass A — DB questions: the safe seed questions already in the database
            (lore/observation/event categories; tests voice and scope handling)
  Pass B — Corpus questions: verbatim reader questions from published Ask TechBear
            columns (tests RAG retrieval quality and factual accuracy)

For Pass B, the expected answer exists in the corpus.
We don't want exact matching — we want to judge:
  - Whether the same key claims appear
  - Whether the voice is consistent
  - Whether TechBear's actual published answer is recognizable as the same advice

Similarity is scored two ways:
  1. rapidfuzz token_set_ratio — surface-level claim overlap
  2. A brief LLM similarity judge call (via Ollama) — semantic equivalence

Run from repo root:
    python -m backend.scripts.test_pipeline [--pass a|b|both] [--dry-run]

Output:
    test_output/pipeline_test_results_{timestamp}.json
    test_output/pipeline_test_summary_{timestamp}.txt  (human-readable)
"""

import argparse
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

# Pipeline import — try at module level to satisfy C0415 (import-outside-toplevel)
# Falls back gracefully on --dry-run or if pipeline not yet installed
try:
    from backend.services.pipeline.orchestrator import run_pipeline as _run_pipeline
    _PIPELINE_AVAILABLE = True
except ImportError:
    _run_pipeline = None  # type: ignore[assignment]
    _PIPELINE_AVAILABLE = False

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = ROOT / "test_output"
OLLAMA_URL = os.getenv(
    "OLLAMA_BASE_URL", "http://localhost:11434") + "/api/chat"
SIMILARITY_MODEL = os.getenv("SIMILARITY_MODEL", "mistral:latest")


# =============================================================================
# QUESTION SETS
# =============================================================================

# Pass A — questions already seeded in the DB (from seed_safe_questions.py)
# These are lore/observation/event questions: no single correct answer,
# but they should elicit in-character TechBear responses.
DB_QUESTIONS = [
    {
        "id": "db_001",
        "question": "What was the most chaotic system you ever had to untangle without completely breaking it?",
        "category": "lore",
        "expected_scope": "IN_SCOPE",
        "notes": "Should trigger TechBear mythology/lore. Expect vivid storytelling, no factual claims to check.",
    },
    {
        "id": "db_002",
        "question": "What kind of tech problems make you pause before touching anything?",
        "category": "observation",
        "expected_scope": "IN_SCOPE",
        "notes": "Should produce cautious, experienced IT perspective with TechBear voice.",
    },
    {
        "id": "db_003",
        "question": "What are the earliest signs that a system is becoming unstable?",
        "category": "observation",
        "expected_scope": "IN_SCOPE",
        "notes": "Factual + voice. Should mention heat, slowdowns, errors. Check fact accuracy.",
    },
    {
        "id": "db_004",
        "question": "What does a system under stress look like from your perspective?",
        "category": "observation",
        "expected_scope": "IN_SCOPE",
        "notes": "Observational — should blend metaphor with real diagnostics.",
    },
    {
        "id": "db_005",
        "question": "How do you stay calm when everything feels like it's on fire?",
        "category": "event",
        "expected_scope": "IN_SCOPE",
        "notes": "TechBear personality question. Expect warm, performative, practical.",
    },
]

# Pass B — verbatim reader questions from published Ask TechBear columns.
# reference_answer is extracted/summarized from the published response.
# We compare the pipeline output against this to judge retrieval quality.
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
        "key_claims": [
            "predictable password rotation does not improve security",
            "plain text password storage is dangerous",
            "shared credentials spreadsheet is a single point of failure",
            "the IT guy's departure may not be unrelated",
            "password manager as the correct solution",
            "multi-factor authentication",
        ],
        "notes": "Rich TechBear scenario. IT guy departure line should be acknowledged with flair.",
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
        "key_claims": [
            "paying ransomware is never recommended",
            "paying marks you as a repeat target",
            "no guarantee files are restored after payment",
            "antivirus/endpoint protection is essential",
            "backups are the correct ransomware defense",
            "report ransomware to authorities",
        ],
        "notes": "Safety-critical advice. Fact critique should score this carefully.",
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
        "key_claims": [
            "keeping battery at 100% degrades lithium-ion cells",
            "optimal charge range is 20-80%",
            "heat from constant charging accelerates degradation",
            "this is not a defective product",
            "battery calibration and management tips",
        ],
        "notes": "Should debunk the myth while validating their frustration.",
    },
]


# =============================================================================
# SIMILARITY SCORING
# =============================================================================

def score_surface_similarity(pipeline_output: str, reference_claims: list[str]) -> dict:
    """
    rapidfuzz token_set_ratio against each expected key claim.
    Returns per-claim scores and an aggregate.
    """
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
    """
    Ask the similarity model whether the key claims are semantically present.
    Returns structured JSON with per-claim verdicts and an overall score.
    """
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
            raw = "\n".join(line for line in raw.splitlines()
                            if not line.strip().startswith("```"))
        return {"method": "llm_semantic_judge", **json.loads(raw)}
    except (RequestException, json.JSONDecodeError, ValueError) as exc:
        return {
            "method": "llm_semantic_judge",
            "error": str(exc),
            "overall_score": None,
        }


# =============================================================================
# PIPELINE RUNNER
# =============================================================================

def _stage_printer(stage: str) -> None:
    """Print pipeline stage progress during test runs."""
    print(f"    → {stage}", flush=True)


def run_question(q: dict, pass_label: str, dry_run: bool = False) -> dict:
    """
    Run one question through the pipeline and collect all results.
    """
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
        "notes": q.get("notes", ""),
        "source_post": q.get("source_post", ""),
        "pipeline_result": None,
        "pipeline_error": None,
        "voice_draft": None,
        "factual_draft": None,
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

        # Similarity scoring only for corpus questions with key claims
        if pass_label == "B" and "key_claims" in q and result["voice_draft"]:
            output_text = result["voice_draft"]
            result["similarity"]["surface"] = score_surface_similarity(
                output_text, q["key_claims"]
            )
            result["similarity"]["semantic"] = score_semantic_similarity(
                output_text, q["key_claims"], q["question"]
            )

    except (RuntimeError, ValueError, OSError) as exc:  # pipeline halts raise RuntimeError
        result["pipeline_result"] = "exception"
        result["pipeline_error"] = str(exc)

    return result


# =============================================================================
# SUMMARY WRITER
# =============================================================================

def write_summary(results: list[dict], output_path: Path) -> None:
    """Write a human-readable summary of pipeline test results to disk."""
    lines = [
        "Ask TechBear v2.5 — Pipeline Test Summary",
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
        if r.get("pipeline_error"):
            lines.append(f"  ERROR: {r.get('pipeline_error')}")
        if r.get("voice_draft"):
            lines.append(
                f"  Voice draft ({len(r['voice_draft'].split())} words):")
            lines.append(f"    {r['voice_draft'][:300]}...")

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

        # Key automated scores
        scores = r.get("scores", {})
        if scores.get("fact_critique"):
            fc = scores["fact_critique"]
            lines.append(
                f"  Fact critique: accuracy={fc.get('accuracy_score')} "
                f"safety={fc.get('safety_score')} "
                f"rec={fc.get('pass_recommendation')}"
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
                f"FK={fk.get('flesch_kincaid_score')} "
                f"(in range: {fk.get('in_range')})"
            )

        lines.append("")

    # Aggregate summary
    lines.append("=" * 60)
    lines.append("AGGREGATE")
    total = len(results)
    complete = sum(1 for r in results if r["pipeline_result"] == "complete")
    halted = sum(1 for r in results if r["pipeline_result"] == "halted")
    errors = sum(1 for r in results if r["pipeline_result"] == "exception")
    lines.append(
        f"Total: {total} | Complete: {complete} | Halted: {halted} | Errors: {errors}")

    corpus_results = [r for r in results if r["pass"]
                      == "B" and r.get("similarity")]
    if corpus_results:
        avg_surface = sum(
            r["similarity"]["surface"]["average"]
            for r in corpus_results
            if r["similarity"].get("surface")
        ) / len(corpus_results)
        sem_scores = [
            r["similarity"]["semantic"]["overall_score"]
            for r in corpus_results
            if r["similarity"].get("semantic", {}).get("overall_score") is not None
        ]
        avg_semantic = sum(sem_scores) / \
            len(sem_scores) if sem_scores else None
        lines.append(f"Pass B avg surface similarity: {round(avg_surface, 1)}")
        if avg_semantic is not None:
            lines.append(
                f"Pass B avg semantic similarity: {round(avg_semantic, 1)}/10")

    output_path.write_text("\n".join(lines), encoding="utf-8")


# =============================================================================
# ENTRY POINT
# =============================================================================

def main() -> None:
    """Parse arguments and run the pipeline test harness."""
    parser = argparse.ArgumentParser(
        description="Ask TechBear v2.5 pipeline test harness"
    )
    parser.add_argument(
        "--pass",
        dest="run_pass",
        choices=["a", "b", "both"],
        default="both",
        help="Which pass to run: a (DB questions), b (corpus questions), both",
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
        help="Run a single question by ID (e.g. db_003 or corpus_002)",
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    questions_to_run = []

    if args.question_id:
        all_q = {q["id"]: (q, "A") for q in DB_QUESTIONS}
        all_q.update({q["id"]: (q, "B") for q in CORPUS_QUESTIONS})
        if args.question_id not in all_q:
            print(f"Unknown question ID: {args.question_id}")
            print(f"Available: {list(all_q.keys())}")
            sys.exit(1)
        q, p = all_q[args.question_id]
        questions_to_run = [(q, p)]
    else:
        if args.run_pass in ("a", "both"):
            questions_to_run += [(q, "A") for q in DB_QUESTIONS]
        if args.run_pass in ("b", "both"):
            questions_to_run += [(q, "B") for q in CORPUS_QUESTIONS]

    print(
        f"Running {len(questions_to_run)} question(s) {'[DRY RUN]' if args.dry_run else ''}...")
    print()

    results = []
    for i, (q, p) in enumerate(questions_to_run, 1):
        print(
            f"[{i}/{len(questions_to_run)}] {q['id']} ({p})  {q['question'][:60]}...")
        r = run_question(q, p, dry_run=args.dry_run)
        results.append(r)
        status = r["pipeline_result"]
        print(f"  → {status}")
        if r.get("pipeline_error"):
            print(f"  ⚠ {r['pipeline_error'][:80]}")
        print()

    # Write outputs
    json_path = OUTPUT_DIR / f"pipeline_test_results_{timestamp}.json"
    summary_path = OUTPUT_DIR / f"pipeline_test_summary_{timestamp}.txt"

    json_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    write_summary(results, summary_path)

    print(f"Results: {json_path}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
