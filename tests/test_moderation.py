#!/usr/bin/env python3
"""
Ask TechBear v2.7 — Moderation Routing Test Harness
Gymnarctos Studios LLC

Fast routing-only evaluation: runs only the moderation phase against
all questions, reports routing decisions without running any generation.
No Ollama generation calls — only the LLM moderation classifier.

Use this after any change to character_moderation.md to verify routing
before waiting for a full pipeline run.

Run from repo root:
    python -m tests.test_moderation [--pass a|b|c|all] [--question ID]
    python -m tests.test_moderation --summary

Output:
    tests/test_output/moderation_test_results_{timestamp}.json
    tests/test_output/moderation_test_summary_{timestamp}.txt
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Pipeline moderation import ─────────────────────────────────────────────
try:
    from backend.services.pipeline.moderation import run as _run_moderation
    _MODERATION_AVAILABLE = True
except ImportError:
    _run_moderation = None  # type: ignore[assignment]
    _MODERATION_AVAILABLE = False

# ── DB import — optional ───────────────────────────────────────────────────
_DB_AVAILABLE = False  # pylint: disable=invalid-name
if TYPE_CHECKING:
    from sqlalchemy import select
    from backend.database import get_db_context
    from backend.models_v26 import TestQuestion

try:
    from sqlalchemy import select  # pylint: disable=ungrouped-imports  # noqa: F811
    from backend.database import get_db_context  # noqa: F811  # pylint: disable=ungrouped-imports
    from backend.models_v26 import TestQuestion  # noqa: F811  # pylint: disable=ungrouped-imports
    _DB_AVAILABLE = True  # pylint: disable=invalid-name
except ImportError:
    pass

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "tests" / "test_output"
QUESTIONS_FILE = ROOT / "tests" / "test_questions.json"


# =============================================================================
# QUESTION LOADING
# =============================================================================

def _load_question_file() -> dict[str, list[dict]]:
    if not QUESTIONS_FILE.exists():
        raise FileNotFoundError(
            f"Test question file not found: {QUESTIONS_FILE}")
    with QUESTIONS_FILE.open(encoding="utf-8") as fh:
        return json.load(fh)


async def _load_from_db(pass_label: str) -> list[dict] | None:
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
                    "notes": r.notes or "",
                }
                for r in rows
            ]
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def load_questions(pass_label: str) -> list[dict]:
    """Load questions for a pass label: DB → JSON file → empty list."""
    db_questions = asyncio.run(_load_from_db(pass_label))
    if db_questions:
        return db_questions
    try:
        return _load_question_file().get(pass_label, [])
    except FileNotFoundError:
        return []


# =============================================================================
# MODERATION RUNNER
# =============================================================================

def run_moderation_check(q: dict, pass_label: str) -> dict:
    """Run only the moderation phase against one question."""
    submission = {
        "id": q["id"],
        "attendee_name": "ModerationTestHarness",
        "question": q["question"],
        "source": f"moderation_test_{pass_label}",
        "expected_scope": q.get("expected_scope", "IN_SCOPE"),
        "conversation_depth": 0,
        "rolling_context": "",
        "batch_context": [],
        "diagnostic_mode": True,
    }

    result = {
        "id": q["id"],
        "pass": pass_label,
        "question": q["question"],
        "category": q.get("category", ""),
        "expected_scope": q.get("expected_scope", ""),
        "expected_retrieval_mode": q.get("expected_retrieval_mode", ""),
        "notes": q.get("notes", ""),
        "moderation_result": None,
        "moderation_error": None,
        "actual_decision": None,
        "actual_scope": None,
        "actual_intent": None,
        "actual_retrieval_mode": None,
        "routing_mismatch": None,
        "scope_mismatch": None,
        "raw_response": None,
        "parse_succeeded": None,
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }

    if not _MODERATION_AVAILABLE or _run_moderation is None:
        result["moderation_result"] = "error"
        result["moderation_error"] = "Moderation phase not available — check installation"
        return result

    try:
        artifact = {
            "submission": submission,
            "scores": {},
            "flags": {},
            "drafts": {},
            "retrieval": {},
            "passed": True,
            "failure_reason": None,
            "loop_counts": {},
            "diagnostics": {},
        }
        artifact = _run_moderation(artifact)

        mod_scores = artifact.get("scores", {}).get("moderation", {})
        diagnostics = artifact.get("diagnostics", {})

        result["actual_decision"] = mod_scores.get("decision")
        result["actual_scope"] = mod_scores.get("scope")
        result["actual_intent"] = mod_scores.get("intent")
        result["actual_retrieval_mode"] = mod_scores.get("retrieval_mode")
        result["raw_response"] = diagnostics.get("moderation_raw_response")
        result["parse_succeeded"] = diagnostics.get(
            "moderation_parse_succeeded")
        result["moderation_result"] = (
            "pass" if artifact.get("passed", True) else "halt"
        )
        result["moderation_error"] = artifact.get("failure_reason")

        # Routing mismatch check
        if (
            result["expected_retrieval_mode"]
            and result["actual_retrieval_mode"]
            and result["expected_retrieval_mode"] != result["actual_retrieval_mode"]
        ):
            result["routing_mismatch"] = (
                f"expected={result['expected_retrieval_mode']} "
                f"actual={result['actual_retrieval_mode']}"
            )

        # Scope mismatch check
        if (
            result["expected_scope"]
            and result["actual_scope"]
            and result["expected_scope"] != result["actual_scope"]
        ):
            result["scope_mismatch"] = (
                f"expected={result['expected_scope']} "
                f"actual={result['actual_scope']}"
            )

    except (RuntimeError, ValueError, OSError) as exc:
        result["moderation_result"] = "error"
        result["moderation_error"] = str(exc)

    return result


# =============================================================================
# SUMMARY WRITER
# =============================================================================

def write_summary(results: list[dict], output_path: Path) -> None:
    """Write moderation routing summary table to file."""
    lines = [
        "Ask TechBear v2.7 — Moderation Routing Test Summary",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "=" * 60,
        "",
        f"{'ID':<12} {'RESULT':<8} {'DECISION':<8} {'ROUTING':<12} {'INTENT':<22} MISMATCHES",
        "-" * 80,
    ]

    routing_mismatches = 0
    scope_mismatches = 0
    halts = 0
    errors = 0

    for r in results:
        status = r.get("moderation_result", "?")
        decision = r.get("actual_decision", "?")
        routing = r.get("actual_retrieval_mode", "?")
        intent = r.get("actual_intent", "?")

        mismatches = []
        if r.get("routing_mismatch"):
            mismatches.append(f"ROUTE:{r['routing_mismatch']}")
            routing_mismatches += 1
        if r.get("scope_mismatch"):
            mismatches.append(f"SCOPE:{r['scope_mismatch']}")
            scope_mismatches += 1
        if status == "halt":
            halts += 1
        if status == "error":
            errors += 1

        mismatch_str = " | ".join(mismatches) if mismatches else "—"
        lines.append(
            f"{r['id']:<12} {status:<8} {decision:<8} {routing:<12} {intent:<22} {mismatch_str}"
        )

    lines.append("=" * 80)
    lines.append(
        f"Total: {len(results)} | "
        f"Routing mismatches: {routing_mismatches} | "
        f"Scope mismatches: {scope_mismatches} | "
        f"Halts: {halts} | Errors: {errors}"
    )
    lines.append("")

    # Detail section for mismatches and halts
    problem_results = [
        r for r in results
        if r.get("routing_mismatch") or r.get("scope_mismatch")
        or r.get("moderation_result") in ("halt", "error")
    ]
    if problem_results:
        lines.append("DETAIL — Mismatches and Halts:")
        lines.append("-" * 60)
        for r in problem_results:
            lines.append(f"\n{r['id']}: {r['question'][:80]}")
            if r.get("routing_mismatch"):
                lines.append(f"  ⚠ ROUTING: {r['routing_mismatch']}")
            if r.get("scope_mismatch"):
                lines.append(f"  ⚠ SCOPE:   {r['scope_mismatch']}")
            if r.get("moderation_error"):
                lines.append(f"  ❌ ERROR:   {r['moderation_error']}")
            if r.get("raw_response"):
                lines.append(f"  Raw: {r['raw_response'][:200]}")

    output_path.write_text("\n".join(lines), encoding="utf-8")


# =============================================================================
# ENTRY POINT
# =============================================================================

def main() -> None:  # pylint: disable=missing-function-docstring
    parser = argparse.ArgumentParser(
        description="Ask TechBear v2.7 moderation routing test harness"
    )
    parser.add_argument(
        "--pass",
        dest="run_pass",
        choices=["a", "b", "c", "all"],
        default="all",
        help="Which pass to run: a (DB/event), b (corpus), c (lore recall), all",
    )
    parser.add_argument(
        "--question",
        dest="question_id",
        default=None,
        help="Run a single question by ID (e.g. lore_001, corpus_002)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print summary table to stdout after run",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(name)s %(levelname)s %(message)s",
    )
    if not args.verbose if hasattr(args, 'verbose') else True:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp: str = datetime.now().strftime("%Y%m%d_%H%M%S")
    questions_to_run: list[tuple[dict, str]] = []

    if args.question_id:
        _all_loaded = {"A": load_questions(
            "A"), "B": load_questions("B"), "C": load_questions("C")}
        all_q: dict[str, tuple[dict, str]] = {}
        for _pl, _qs in _all_loaded.items():
            for _q in _qs:
                all_q[_q["id"]] = (_q, _pl)
        if args.question_id not in all_q:
            print(f"Unknown question ID: {args.question_id}")
            print(f"Available: {list(all_q.keys())}")
            sys.exit(1)
        q, p = all_q[args.question_id]
        questions_to_run = [(q, p)]
    else:
        if args.run_pass in ("a", "all"):
            questions_to_run += [(q, "A") for q in load_questions("A")]
        if args.run_pass in ("b", "all"):
            questions_to_run += [(q, "B") for q in load_questions("B")]
        if args.run_pass in ("c", "all"):
            questions_to_run += [(q, "C") for q in load_questions("C")]

    print(
        f"Running moderation check on {len(questions_to_run)} question(s)...")
    print()

    results = []
    for i, (q, p) in enumerate(questions_to_run, 1):
        print(f"[{i}/{len(questions_to_run)}] {q['id']} ({p})  {q['question'][:60]}...",
              end=" ", flush=True)
        r = run_moderation_check(q, p)
        results.append(r)

        status = r["moderation_result"]
        routing = r.get("actual_retrieval_mode", "?")
        intent = r.get("actual_intent", "?")
        mismatch = " ⚠ ROUTE MISMATCH" if r.get("routing_mismatch") else ""
        halt = " ❌ HALTED" if status == "halt" else ""
        print(f"→ {status} | {routing} | {intent}{mismatch}{halt}")

    json_path = OUTPUT_DIR / f"moderation_test_results_{timestamp}.json"
    summary_path = OUTPUT_DIR / f"moderation_test_summary_{timestamp}.txt"

    json_path.write_text(json.dumps(
        results, indent=2, ensure_ascii=False), encoding="utf-8")
    write_summary(results, summary_path)

    if args.summary:
        print()
        print(summary_path.read_text())
    else:
        print(f"\nResults: {json_path}")
        print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
