#!/usr/bin/env python3
"""
Ask TechBear v2.8 — Pipeline Test Harness
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
    python -m tests.test_pipeline [--pass a|b|c|all] [--dry-run]
                                  [--question ID [ID ...]]
                                  [--verbose] [--summary] [--phase PHASE]

Verbosity:
    default   — phase scores, draft excerpts (300 chars), similarity scores, errors
    --summary — one line per question: ID | status | routing | scores | error
    --verbose — everything: retrieval chunk metadata, raw LLM responses,
                moderation parse diagnostics; useful for GIGO diagnosis

Output:
    tests/test_output/pipeline_test_results_{timestamp}.json
    tests/test_output/pipeline_test_summary_{timestamp}.txt
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import requests
from dotenv import load_dotenv
from rapidfuzz import fuzz
from requests.exceptions import RequestException

load_dotenv()

# ── Logging ────────────────────────────────────────────────────────────────
# Configured after argparse so --verbose/--summary can set the correct level.
# configure_logging() replaces the former logging.basicConfig() call and
# ensures SQLAlchemy suppression and structured output format are consistent
# with the rest of the pipeline.
logger = logging.getLogger(__name__)

# ── Pipeline import ────────────────────────────────────────────────────────
try:
    from backend.services.pipeline.orchestrator import run_pipeline as _run_pipeline
    from backend.services.pipeline.logging_config import configure_logging
    _PIPELINE_AVAILABLE = True
except ImportError:
    _run_pipeline = None  # type: ignore[assignment]
    configure_logging = None  # type: ignore[assignment]
    _PIPELINE_AVAILABLE = False

# ── DB import — optional, graceful fallback ────────────────────────────────
# TYPE_CHECKING guard gives Pylance correct types without affecting runtime.
# The _DB_AVAILABLE flag gates all usage so the except path is never reached
# when these names are actually called.
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
OLLAMA_URL = os.getenv(
    "OLLAMA_BASE_URL", "http://localhost:11434") + "/api/chat"
SIMILARITY_MODEL = os.getenv("SIMILARITY_MODEL", "mistral:latest")


# =============================================================================
# QUESTION FILE LOADER
# =============================================================================

QUESTIONS_FILE = ROOT / "tests" / "test_questions.json"


def _load_question_file() -> dict[str, list[dict]]:
    """
    Load question sets from tests/test_questions.json.
    Returns a dict keyed by pass label: {"A": [...], "B": [...], "C": [...]}.
    Raises FileNotFoundError with a clear message if the file is missing.
    """
    if not QUESTIONS_FILE.exists():
        raise FileNotFoundError(
            f"Test question file not found: {QUESTIONS_FILE}\n"
            "Re-generate it or restore it from version control."
        )
    with QUESTIONS_FILE.open(encoding="utf-8") as fh:
        return json.load(fh)


# =============================================================================
# HARDCODED EMERGENCY FALLBACK
# Minimal stubs used only if test_questions.json is missing AND the DB is down.
# Not the source of truth — add/edit questions in tests/test_questions.json.
# =============================================================================

_EMERGENCY_FALLBACK: dict[str, list[dict]] = {
    "A": [{"id": "db_001", "question": "What was the most chaotic system you ever had to untangle?", "category": "tall_tale", "expected_scope": "IN_SCOPE", "expected_retrieval_mode": "tall_tale", "key_claims": [], "notes": "Emergency fallback — restore test_questions.json"}],
    "B": [{"id": "corpus_001", "question": "My tech guy keeps going on about the importance of regular backups. Am I a genius for emailing files to myself?", "category": "corpus", "expected_scope": "IN_SCOPE", "expected_retrieval_mode": "factual", "source_post": "", "source_url": "", "key_claims": ["email is not a backup solution"], "notes": "Emergency fallback — restore test_questions.json"}],
    "C": [{"id": "lore_001", "question": "Have you ever met Captain Janeway?", "category": "lore", "expected_scope": "IN_SCOPE", "expected_retrieval_mode": "lore", "source_post": "", "source_url": "", "key_claims": ["Coffee Crisis in the Delta Quadrant", "Tom Paris was responsible"], "notes": "Emergency fallback — restore test_questions.json"}],
}


# =============================================================================
# DB LOADER
# =============================================================================

async def _load_from_db(pass_label: str) -> list[dict] | None:
    """Load test questions from the database for the given pass label."""
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


def load_questions(pass_label: str) -> list[dict]:
    """
    Load questions for a pass label using priority order:

    1. DB (test_questions table) — live source when schema is migrated
    2. tests/test_questions.json — file-based source of truth (version controlled)
    3. _EMERGENCY_FALLBACK — minimal stubs when both above are unavailable
    """
    # 1. Try DB
    db_questions = asyncio.run(_load_from_db(pass_label))
    if db_questions:
        print(
            f"  [Pass {pass_label}] {len(db_questions)} question(s) from database.")
        return db_questions

    # 2. Try JSON file
    try:
        all_questions = _load_question_file()
        file_questions = all_questions.get(pass_label, [])
        if file_questions:
            print(
                f"  [Pass {pass_label}] {len(file_questions)} question(s) "
                f"from {QUESTIONS_FILE.name}."
            )
            return file_questions
    except FileNotFoundError as exc:
        print(f"  [Pass {pass_label}] WARNING: {exc}")

    # 3. Emergency fallback
    fallback = _EMERGENCY_FALLBACK.get(pass_label, [])
    print(
        f"  [Pass {pass_label}] WARNING: Using emergency fallback "
        f"({len(fallback)} stub question(s)). Restore test_questions.json."
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
""",
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
    2 = TechBear canon referenced (correct episode area, < 40% claims hit)
    3 = Correct story identified (>= 40% claims hit)
    4 = Key lore details retrieved (>= 60% claims hit)
    5 = Rich canon synthesis (>= 80% claims hit)
    """
    surface = score_surface_similarity(pipeline_output, key_claims)
    claims_hit = surface["claims_above_60"]
    total_claims = len(key_claims)

    if total_claims == 0 or claims_hit == 0:
        lore_score = 0
    elif claims_hit / total_claims >= 0.8:
        lore_score = 5
    elif claims_hit / total_claims >= 0.6:
        lore_score = 4
    elif claims_hit / total_claims >= 0.4:
        lore_score = 3
    elif claims_hit / total_claims >= 0.2:
        lore_score = 2
    else:
        lore_score = 1  # at least 1 hit but below 20% threshold

    return {
        "method": "lore_recall_surface_proxy",
        "lore_score": lore_score,
        "max_score": 5,
        "surface_avg": surface["average"],
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

_PHASE_STOP_SENTINEL = "__phase_stop__"


class _PhaseStopSignal(Exception):
    """Raised by the stage callback when the target phase has completed."""


def _make_stage_printer(phase_stop: str | None = None) -> Callable[[str], None]:
    """
    Returns an on_stage callback. If phase_stop is set, raises _PhaseStopSignal
    after the named phase fires, halting the pipeline cleanly at that point.
    """
    completed: list[str] = []

    def _printer(stage: str) -> None:
        print(f"    → {stage}", flush=True)
        if phase_stop and completed and completed[-1] == phase_stop:
            raise _PhaseStopSignal(f"Stopped after phase: {phase_stop}")
        completed.append(stage)

    return _printer


def run_question(
    q: dict,
    pass_label: str,
    dry_run: bool = False,
    verbose: bool = False,
    phase_stop: str | None = None,
) -> dict:
    """Run one question through the pipeline and collect all results."""
    submission = {
        "id": q["id"],
        "attendee_name": "TestHarness",
        "question": q["question"],
        "source": f"test_{pass_label}",
        "expected_scope": q.get("expected_scope", "IN_SCOPE"),
        "expected_retrieval_mode": q.get("expected_retrieval_mode", "factual"),
        "conversation_depth": 0,
        "rolling_context": "",
        "batch_context": [],
        "diagnostic_mode": True,
    }

    result: dict = {
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
        "retrieval_diagnostics": {},
        "scores": {},
        "flags": {},
        "loop_counts": {},
        "similarity": {},
        "diagnostics": {},
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
        try:
            artifact = _run_pipeline(
                submission, on_stage=_make_stage_printer(phase_stop))
        except _PhaseStopSignal:
            # Pipeline halted cleanly at requested phase — treat as partial complete
            result["pipeline_result"] = f"stopped_after_{phase_stop}"
            return result

        result["pipeline_result"] = "complete" if artifact.get(
            "passed") else "halted"
        result["pipeline_error"] = artifact.get("failure_reason")
        result["voice_draft"] = artifact.get("drafts", {}).get("voice", "")
        result["factual_draft"] = artifact.get("drafts", {}).get("factual", "")
        result["scores"] = artifact.get("scores", {})
        result["flags"] = artifact.get("flags", {})
        result["loop_counts"] = artifact.get("loop_counts", {})
        result["diagnostics"] = artifact.get("diagnostics", {})

        # Retrieval diagnostics — always captured, full chunks only in verbose JSON
        retrieval = artifact.get("retrieval", {})
        facts_chunks = retrieval.get("facts", [])
        voice_chunks = retrieval.get("voice", [])
        lore_chunks = retrieval.get("lore", [])
        episode_context = retrieval.get("episode_context", {})
        result["retrieval_diagnostics"] = {
            "retrieval_mode": retrieval.get("retrieval_mode"),
            "facts_count": len(facts_chunks),
            "voice_count": len(voice_chunks),
            "lore_count": len(lore_chunks),
            "retrieval_error": artifact.get("flags", {}).get("retrieval_error"),
            "episode_context": episode_context,
            "facts_chunks": facts_chunks if verbose else [],
            "lore_chunks": lore_chunks if verbose else [],
            "voice_chunks": voice_chunks if verbose else [],
        }

        # Routing validation
        result["actual_retrieval_mode"] = (
            retrieval.get("retrieval_mode")
            or artifact.get("scores", {}).get("moderation", {}).get("retrieval_mode")
        )
        if (
            result["expected_retrieval_mode"]
            and result["actual_retrieval_mode"]
            and result["expected_retrieval_mode"] != result["actual_retrieval_mode"]
        ):
            result["routing_mismatch"] = (
                f"expected={result['expected_retrieval_mode']} "
                f"actual={result['actual_retrieval_mode']}"
            )

        # Episode match validation — Pass C only.
        # Checks whether the dominant post_id identified by episode isolation
        # matches the expected episode_id in the test question.
        # None episode_id = cross-episode or tall-tale question, skip check.
        expected_episode_id = q.get("episode_id")
        actual_post_id = episode_context.get("post_id")
        if (
            pass_label == "C"
            and expected_episode_id is not None
            and episode_context.get("episode_isolated")
        ):
            if actual_post_id != expected_episode_id:
                result["episode_mismatch"] = (
                    f"expected_post_id={expected_episode_id} "
                    f"actual_post_id={actual_post_id}"
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
# SUMMARY WRITERS
# =============================================================================

def _summary_line(r: dict) -> str:
    """One-line summary for --summary mode. Defensive against None scores."""
    status = r.get("pipeline_result") or "?"
    routing = r.get("actual_retrieval_mode") or "?"
    expected = r.get("expected_retrieval_mode") or ""
    routing_flag = f"⚠{expected}→{routing}" if r.get(
        "routing_mismatch") else routing

    scores = r.get("scores") or {}
    fc = scores.get("fact_critique") or {}
    cc = scores.get("character_critique") or {}

    score_parts = []
    acc = fc.get("accuracy_score")
    if acc is not None:
        score_parts.append(f"acc={acc}")
    fid = cc.get("character_fidelity_score")
    if fid is not None:
        score_parts.append(f"fid={fid}")
    lr = (r.get("similarity") or {}).get("lore_recall")
    if lr:
        score_parts.append(f"lore={lr.get('lore_score', '?')}/5")

    score_str = " ".join(score_parts) if score_parts else "—"

    error_str = ""
    err = str(r.get("pipeline_error") or "")
    if err:
        error_str = f" | {err[:60]}" if len(err) > 60 else f" | {err}"

    return (
        f"{r.get('id', '?'):<12} {status:<10} {routing_flag:<14} {score_str}{error_str}"
    )


def write_summary(results: list[dict], output_path: Path, verbose: bool = False) -> None:
    """Write human-readable summary. Defensive against None values throughout."""
    lines = [
        "Ask TechBear v2.8 — Pipeline Test Summary",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "=" * 60,
        "",
    ]

    try:
        for r in results:
            _append_result_block(lines, r, verbose)

        # Aggregate
        lines.append("=" * 60)
        lines.append("AGGREGATE")
        _append_aggregate(lines, results)

    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Reporting failure must not abort the run or lose the JSON artifact.
        # Mark the summary as incomplete rather than crashing.
        logger.error(
            "write_summary | reporting failure — summary may be incomplete | error=%r",
            str(exc),
        )
        lines.append("")
        lines.append(f"⚠ SUMMARY INCOMPLETE — reporting error: {exc}")
        lines.append("Full results preserved in JSON artifact.")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def _append_result_block(lines: list[str], r: dict, verbose: bool) -> None:
    """Append one question result block to the summary lines list."""
    pipeline_result = r.get("pipeline_result") or "?"
    lines.append(
        f"[{r.get('pass', '?')}] {r.get('id', '?')} — {pipeline_result}")

    if r.get("category"):
        lines.append(f"  Category: {r['category']}")
    if r.get("source_post"):
        lines.append(f"  Source: {str(r['source_post'])[:65]}")

    question = str(r.get("question") or "")
    lines.append(f"  Q: {question[:100]}...")

    if r.get("routing_mismatch"):
        lines.append(f"  ⚠ ROUTING MISMATCH: {r['routing_mismatch']}")

    if r.get("episode_mismatch"):
        lines.append(f"  ⚠ EPISODE MISMATCH: {r['episode_mismatch']}")

    # Retrieval diagnostics — always show counts and episode context
    rd = r.get("retrieval_diagnostics") or {}
    if rd:
        mode = rd.get("retrieval_mode") or "?"
        counts = (
            f"facts={rd.get('facts_count', 0)} "
            f"lore={rd.get('lore_count', 0)} "
            f"voice={rd.get('voice_count', 0)}"
        )
        lines.append(f"  Retrieval: mode={mode} | {counts}")
        if rd.get("retrieval_error"):
            lines.append(f"  ⚠ Retrieval error: {rd['retrieval_error']}")

        # Episode isolation context — show for all lore questions
        ec = rd.get("episode_context") or {}
        if ec.get("episode_isolated"):
            lines.append(
                f"  Episode isolation: post_id={ec.get('post_id')} "
                f"title={str(ec.get('title', '?'))[:40]} | "
                f"chunks_removed={ec.get('chunks_removed', 0)} "
                f"dominant={ec.get('dominant_chunk_count', 0)}"
            )
        elif ec and rd.get("retrieval_mode") in ("lore", "tall_tale"):
            lines.append(
                f"  Episode isolation: no dominant episode identified "
                f"(all_post_ids={ec.get('all_post_ids', [])})"
            )

        if verbose and rd.get("lore_chunks"):
            lines.append("  Lore chunks retrieved:")
            for i, chunk in enumerate(rd["lore_chunks"], 1):
                meta = chunk.get("meta") or {}
                stage = meta.get("retrieval_stage", "broad")
                lines.append(
                    f"    [{i}] tier={meta.get('lore_tier', '?')} "
                    f"stage={stage} "
                    f"post_id={meta.get('post_id', '?')} "
                    f"src={str(meta.get('source_type', '?'))[:30]}"
                )
                lines.append(f"        {str(chunk.get('text', ''))[:120]}...")

    if r.get("pipeline_error"):
        lines.append(f"  ERROR: {r.get('pipeline_error')}")

    # Moderation diagnostics in verbose mode
    if verbose:
        diag = r.get("diagnostics") or {}
        if diag.get("moderation_raw_response"):
            parse_ok = diag.get("moderation_parse_succeeded", "?")
            lines.append(f"  Moderation parse succeeded: {parse_ok}")
            lines.append(
                f"  Moderation raw response: {str(diag['moderation_raw_response'])[:300]}"
            )
        if diag.get("fact_critique_raw_response"):
            fc_parse = diag.get("fact_critique_parse_succeeded", "?")
            lines.append(f"  Fact critique parse succeeded: {fc_parse}")
            if not fc_parse:
                lines.append(
                    f"  Fact critique raw: {str(diag['fact_critique_raw_response'])[:300]}"
                )

    if r.get("voice_draft"):
        lines.append(
            f"  Voice draft ({len(str(r['voice_draft']).split())} words):")
        excerpt_len = 600 if verbose else 300
        lines.append(f"    {str(r['voice_draft'])[:excerpt_len]}...")

    lc = r.get("loop_counts") or {}
    if lc:
        lines.append(
            f"  Loop counts: factual={lc.get('factual', 0)} voice={lc.get('voice', 0)}"
        )

    sim = r.get("similarity") or {}
    if sim.get("surface"):
        s = sim["surface"]
        per_claim = s.get("per_claim") or []
        lines.append(
            f"  Surface similarity: avg={s.get('average', '?')} | "
            f"claims≥60: {s.get('claims_above_60', 0)}/{len(per_claim)} | "
            f"claims≥80: {s.get('claims_above_80', 0)}/{len(per_claim)}"
        )
        if verbose:
            for cp in per_claim:
                lines.append(
                    f"    {cp.get('score', '?'):>3}  {cp.get('claim', '')}")

    sem = sim.get("semantic") or {}
    if sem.get("overall_score") is not None:
        lines.append(
            f"  Semantic similarity: {sem['overall_score']}/10 — "
            f"{sem.get('summary', '')}"
        )

    lr = sim.get("lore_recall")
    if lr:
        lines.append(
            f"  Lore recall: {lr.get('lore_score', '?')}/5 "
            f"(claims hit: {lr.get('claims_hit', 0)}/{lr.get('total_claims', 0)})"
        )

    scores = r.get("scores") or {}
    fc = scores.get("fact_critique") or {}
    if fc:
        lines.append(
            f"  Fact critique: accuracy={fc.get('accuracy_score', '?')} "
            f"safety={fc.get('safety_score', '?')} "
            f"rec={fc.get('pass_recommendation', '?')} "
            f"[{fc.get('critique_mode', 'factual')} mode]"
        )
    cc = scores.get("character_critique") or {}
    if cc:
        lines.append(
            f"  Character critique: fidelity={cc.get('character_fidelity_score', '?')} "
            f"anti_formulaic={cc.get('anti_formulaic_score', '?')} "
            f"words={cc.get('word_count', '?')}"
        )
    ec = scores.get("editorial_critique") or {}
    if ec:
        fk = ec.get("flesch_kincaid") or {}
        lines.append(
            f"  Editorial critique: clarity={ec.get('clarity_score', '?')} "
            f"FK={fk.get('flesch_kincaid_score', '?')} "
            f"(in range: {fk.get('in_range', '?')})"
        )

    lines.append("")


def _append_aggregate(lines: list[str], results: list[dict]) -> None:
    """Append aggregate statistics block to the summary lines list."""
    total = len(results)
    complete = sum(1 for r in results if r.get(
        "pipeline_result") == "complete")
    halted = sum(1 for r in results if r.get("pipeline_result") == "halted")
    errors = sum(1 for r in results if r.get("pipeline_result") == "exception")
    routing_mismatches = sum(1 for r in results if r.get("routing_mismatch"))
    episode_mismatches = sum(1 for r in results if r.get("episode_mismatch"))

    lines.append(
        f"Total: {total} | Complete: {complete} | Halted: {halted} | Errors: {errors}"
    )
    if routing_mismatches:
        lines.append(f"Routing mismatches: {routing_mismatches}")
    if episode_mismatches:
        ids = ", ".join(
            str(r.get("id", "?"))
            for r in results if r.get("episode_mismatch")
        )
        lines.append(f"Episode mismatches: {episode_mismatches} ({ids})")

    # Retrieval summary
    lore_zero = [
        r for r in results
        if (r.get("retrieval_diagnostics") or {}).get("lore_count", 0) == 0
        and r.get("category") in ("lore", "tall_tale")
    ]
    if lore_zero:
        ids = ", ".join(str(r.get("id", "?")) for r in lore_zero)
        lines.append(f"Lore questions with zero lore chunks: {ids}")

    # Pass B similarity
    b_results = [
        r for r in results
        if r.get("pass") == "B" and r.get("similarity")
    ]
    if b_results:
        surface_scores = [
            r["similarity"]["surface"]["average"]
            for r in b_results
            if (r.get("similarity") or {}).get("surface")
        ]
        sem_scores = [
            r["similarity"]["semantic"]["overall_score"]
            for r in b_results
            if (r.get("similarity") or {}).get("semantic", {}).get("overall_score") is not None
        ]
        if surface_scores:
            lines.append(
                f"Pass B avg surface similarity: "
                f"{round(sum(surface_scores)/len(surface_scores), 1)}"
            )
        if sem_scores:
            lines.append(
                f"Pass B avg semantic similarity: "
                f"{round(sum(sem_scores)/len(sem_scores), 1)}/10"
            )

    # Pass C lore recall
    c_results = [
        r for r in results
        if r.get("pass") == "C" and (r.get("similarity") or {}).get("lore_recall")
    ]
    if c_results:
        avg_lore = sum(
            r["similarity"]["lore_recall"].get("lore_score", 0) for r in c_results
        ) / len(c_results)
        lines.append(f"Pass C avg lore recall: {round(avg_lore, 1)}/5")


def write_summary_oneline(results: list[dict], output_path: Path) -> None:
    """--summary mode: one line per question to stdout + summary file."""
    header = f"{'ID':<12} {'STATUS':<10} {'ROUTING':<14} SCORES / ERROR"
    lines = [
        "Ask TechBear v2.8 — Pipeline Test Summary (one-line)",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "=" * 72,
        header,
        "-" * 72,
    ]
    for r in results:
        lines.append(_summary_line(r))
    lines.append("=" * 72)

    total = len(results)
    complete = sum(1 for r in results if r.get(
        "pipeline_result") == "complete")
    errors = total - complete
    lines.append(
        f"Total: {total}  Complete: {complete}  Errors/Halted: {errors}")

    text = "\n".join(lines)
    print(text)
    output_path.write_text(text, encoding="utf-8")


# =============================================================================
# ENTRY POINT
# =============================================================================

def main() -> None:  # pylint: disable=missing-function-docstring
    parser = argparse.ArgumentParser(
        description="Ask TechBear v2.8 pipeline test harness"
    )
    parser.add_argument(
        "--pass",
        dest="run_pass",
        choices=["a", "b", "c", "all"],
        default="all",
        help="Which pass to run: a (DB/event), b (corpus), c (lore recall), all",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate setup and question sets without hitting Ollama",
    )
    parser.add_argument(
        "--question",
        dest="question_ids",
        nargs="+",
        default=None,
        metavar="ID",
        help=(
            "Run one or more questions by ID "
            "(e.g. --question lore_002 lore_006 lore_004)"
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "Full output: retrieval chunk metadata, raw LLM responses, "
            "moderation parse diagnostics, per-claim similarity scores"
        ),
    )
    parser.add_argument(
        "--phase",
        dest="phase_stop",
        default=None,
        choices=[
            "moderation", "factual_pass", "fact_critique",
            "educational_pass", "voice_pass", "semantic_check",
            "character_critique", "editorial_pass", "editorial_critique",
            "educational_critique", "handoff",
        ],
        help="Stop pipeline after this phase (e.g. --phase moderation)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="One line per question: ID | status | routing | scores | error",
    )
    args = parser.parse_args()

    # Configure logging via logging_config — replaces logging.basicConfig().
    # summary mode uses standard (INFO) so phase chatter doesn't interleave
    # with the one-line output. verbose uses verbose (DEBUG).
    verbosity = "verbose" if args.verbose else "standard"
    if configure_logging is not None:
        configure_logging(verbosity=verbosity, file_logging=True)
    else:
        # Pipeline unavailable — minimal fallback so error messages still surface
        logging.basicConfig(
            level=logging.DEBUG if args.verbose else logging.WARNING,
            format="%(name)s %(levelname)s %(message)s",
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp: str = datetime.now().strftime("%Y%m%d_%H%M%S")

    questions_to_run: list[tuple[dict, str]] = []

    if args.question_ids:
        # Build lookup from all available questions across all passes
        _all_loaded = {
            "A": load_questions("A"),
            "B": load_questions("B"),
            "C": load_questions("C"),
        }
        all_q: dict[str, tuple[dict, str]] = {}
        for _pass_label, _qs in _all_loaded.items():
            for _q in _qs:
                all_q[_q["id"]] = (_q, _pass_label)

        unknown = [qid for qid in args.question_ids if qid not in all_q]
        if unknown:
            print(f"Unknown question ID(s): {', '.join(unknown)}")
            print(f"Available: {list(all_q.keys())}")
            sys.exit(1)

        questions_to_run = [all_q[qid] for qid in args.question_ids]
    else:
        if args.run_pass in ("a", "all"):
            questions_to_run += [(q, "A") for q in load_questions("A")]
        if args.run_pass in ("b", "all"):
            questions_to_run += [(q, "B") for q in load_questions("B")]
        if args.run_pass in ("c", "all"):
            questions_to_run += [(q, "C") for q in load_questions("C")]

    if not args.summary:
        print(
            f"Running {len(questions_to_run)} question(s) "
            f"{'[DRY RUN] ' if args.dry_run else ''}"
            f"{'[VERBOSE] ' if args.verbose else ''}"
            f"..."
        )
        print()

    results = []
    for i, (q, p) in enumerate(questions_to_run, 1):
        if not args.summary:
            print(
                f"[{i}/{len(questions_to_run)}] {q['id']} ({p})  "
                f"{q['question'][:60]}..."
            )
        r = run_question(
            q, p,
            dry_run=args.dry_run,
            verbose=args.verbose,
            phase_stop=args.phase_stop,
        )
        results.append(r)

        if not args.summary:
            status = r.get("pipeline_result") or "?"
            routing = (
                f" ⚠ routing={r['routing_mismatch']}"
                if r.get("routing_mismatch") else ""
            )
            rd = r.get("retrieval_diagnostics") or {}
            chunk_info = (
                f" [facts={rd.get('facts_count', 0)} "
                f"lore={rd.get('lore_count', 0)} "
                f"voice={rd.get('voice_count', 0)}]"
            ) if rd else ""
            print(f"  → {status}{routing}{chunk_info}")
            if r.get("pipeline_error"):
                print(f"  ⚠ {str(r.get('pipeline_error') or '')[:100]}")
            print()

    json_path = OUTPUT_DIR / f"pipeline_test_results_{timestamp}.json"
    summary_path = OUTPUT_DIR / f"pipeline_test_summary_{timestamp}.txt"

    # Always write full JSON (chunk data included if verbose)
    json_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    if args.summary:
        write_summary_oneline(results, summary_path)
    else:
        write_summary(results, summary_path, verbose=args.verbose)
        print(f"Results: {json_path}")
        print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
