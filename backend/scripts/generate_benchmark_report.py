"""
backend/scripts/generate_benchmark_report.py — Benchmark report generator
Ask TechBear — Gymnarctos Studios LLC

Reads pipeline run data and score deltas from PostgreSQL and produces
a human-readable DOCX benchmark report.

The report is generated via a Node.js script (using the docx npm package)
fed structured JSON from this Python script. This follows the project's
established pattern for DOCX generation.

Usage:
    python -m backend.scripts.generate_benchmark_report
    python -m backend.scripts.generate_benchmark_report --version v2.6
    python -m backend.scripts.generate_benchmark_report --output reports/my_report.docx

Output lands in benchmark_results/reports/ by default.

Sections produced:
    1. Executive Summary
    2. Test Configuration
    3. Question Set Overview
    4. Completion Summary
    5. Score Summary (LLM scores + human scores + deltas)
    6. Calibration Analysis (judge bias direction per dimension)
    7. Failure Modes (top flags, high-delta runs)
    8. Moderation Findings
    9. Retrieval Findings
    10. Voice / Character Findings
    11. Editorial Findings
    12. Recommendations
    13. Next Steps
"""

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from sqlalchemy.orm import selectinload

load_dotenv()

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.models_v26 import (  # noqa: E402  # pylint: disable=wrong-import-position
    HumanReview,
    PipelineRun,
)
from backend.services.score_deltas import get_delta_summary  # noqa: E402  # pylint: disable=wrong-import-position
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

OUTPUT_DIR = Path("benchmark_results/reports")
SCRIPT_DIR = Path(__file__).parent


# =============================================================
# Data collection
# =============================================================


async def collect_report_data(
    session: AsyncSession,
    pipeline_version: str | None,
) -> dict:
    """Gather all data needed for the report from the database."""

    # All pipeline runs (optionally filtered by version)
    run_query = (
        select(PipelineRun)
        .options(
            selectinload(PipelineRun.question),
            selectinload(PipelineRun.llm_scores),
            selectinload(PipelineRun.human_review).selectinload(
                HumanReview.notes
            ),
            selectinload(PipelineRun.artifacts),
        )
        .order_by(PipelineRun.completed_at.asc().nulls_last())
    )
    if pipeline_version:
        run_query = run_query.where(
            PipelineRun.pipeline_version == pipeline_version
        )

    result = await session.execute(run_query)
    runs = result.scalars().all()

    # Delta summary
    summary = await get_delta_summary(session, pipeline_version=pipeline_version)

    # Per-run detail rows for the score table
    score_rows = []
    for run in runs:
        review = run.human_review
        llm_by_type: dict[str, dict] = {}
        for score in (run.llm_scores or []):
            if score.score_type not in llm_by_type:
                llm_by_type[score.score_type] = {}
            if score.score_value is not None:
                llm_by_type[score.score_type][score.score_name] = float(
                    score.score_value
                )

        def _llm(phase, name):
            return llm_by_type.get(phase, {}).get(name)  # pylint: disable=cell-var-from-loop

        score_rows.append({
            "run_id": str(run.id)[:8],
            "question": (
                run.question.question_text[:60] + "..."
                if run.question and len(run.question.question_text) > 60
                else (run.question.question_text if run.question else "—")
            ),
            "status": run.status,
            "llm_fact": _llm("fact_critique", "accuracy_score"),
            "llm_character": _llm("character_critique", "character_fidelity_score"),
            "llm_editorial": _llm("editorial_critique", "clarity_score"),
            "human_fact": float(review.fact_score) if review and review.fact_score else None,
            "human_character": (
                float(review.character_score) if review and review.character_score else None
            ),
            "human_editorial": (
                float(review.editorial_score) if review and review.editorial_score else None
            ),
            "edit_effort": review.edit_effort if review else None,
            "publishable": review.publishable if review else None,
            "flags": [n.note_type for n in (review.notes if review else [])] if review else [],
        })

    # Halted runs breakdown
    halted = [r for r in runs if r.status == "halted"]
    halted_detail = []
    for r in halted:
        halted_detail.append({
            "question": (
                r.question.question_text[:80] if r.question else "—"
            ),
            "error": r.error_message or "unknown",
        })

    # Retrieval mode distribution
    retrieval_modes: dict[str, int] = {}
    for r in runs:
        for artifact in (r.artifacts or []):
            if artifact.artifact_type == "moderation_result":
                meta = artifact.artifact_metadata or {}
                mode = meta.get("retrieval_mode", "unknown")
                retrieval_modes[mode] = retrieval_modes.get(mode, 0) + 1
                break

    # Moderation routing breakdown
    moderation_decisions: dict[str, int] = {}
    for r in runs:
        for artifact in (r.artifacts or []):
            if artifact.artifact_type == "moderation_result":
                decision = artifact.content or "unknown"
                moderation_decisions[decision] = (
                    moderation_decisions.get(decision, 0) + 1
                )
                break

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": pipeline_version or "all",
        "total_runs": len(runs),
        "complete_runs": len([r for r in runs if r.status == "complete"]),
        "halted_runs": len(halted),
        "halted_detail": halted_detail,
        "reviewed_runs": len([
            r for r in runs
            if r.human_review and r.human_review.review_status == "complete"
        ]),
        "publishable_runs": len([
            r for r in runs
            if r.human_review and r.human_review.publishable
        ]),
        "score_rows": score_rows,
        "delta_summary": summary,
        "retrieval_modes": retrieval_modes,
        "moderation_decisions": moderation_decisions,
    }


# =============================================================
# Node.js DOCX generation
# =============================================================

DOCX_SCRIPT = """  # noqa: E501
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  HeadingLevel, AlignmentType, BorderStyle, WidthType, ShadingType,
  LevelFormat, PageNumber, PageBreak,
} = require('docx');
const fs = require('fs');

const data = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const outPath = process.argv[3];

// ── Helpers ──────────────────────────────────────────────────

function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    children: [new TextRun({ text, bold: true })],
    spacing: { before: 400, after: 200 },
  });
}

function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    children: [new TextRun({ text, bold: true })],
    spacing: { before: 300, after: 160 },
  });
}

function para(text, opts = {}) {
  return new Paragraph({
    children: [new TextRun({ text, ...opts })],
    spacing: { after: 120 },
  });
}

function bullet(text) {
  return new Paragraph({
    numbering: { reference: 'bullets', level: 0 },
    children: [new TextRun(text)],
    spacing: { after: 80 },
  });
}

function fmt(val, decimals = 1) {
  if (val === null || val === undefined) return '—';
  return Number(val).toFixed(decimals);
}

function pct(val) {
  if (val === null || val === undefined) return '—';
  return (Number(val) * 100).toFixed(1) + '%';
}

function biasColor(dir) {
  if (dir === 'overconfident') return 'C00000';
  if (dir === 'underconfident') return 'ED7D31';
  if (dir === 'calibrated') return '375623';
  return '7F7F7F';
}

const border = { style: BorderStyle.SINGLE, size: 1, color: 'CCCCCC' };
const borders = { top: border, bottom: border, left: border, right: border };
const cellMargins = { top: 80, bottom: 80, left: 120, right: 120 };

function cell(text, opts = {}) {
  const { width = 1300, bold = false, shade = null, color = null } = opts;
  const run = new TextRun({
    text: String(text),
    bold,
    color: color || undefined,
    size: 18,
  });
  const cellOpts = {
    borders,
    width: { size: width, type: WidthType.DXA },
    margins: cellMargins,
    children: [new Paragraph({ children: [run] })],
  };
  if (shade) {
    cellOpts.shading = { fill: shade, type: ShadingType.CLEAR };
  }
  return new TableCell(cellOpts);
}

function headerRow(labels, widths) {
  return new TableRow({
    tableHeader: true,
    children: labels.map((l, i) =>
      cell(l, { width: widths[i], bold: true, shade: 'D5E8F0' })
    ),
  });
}

// ── Score table ───────────────────────────────────────────────

const scoreCols = [3000, 1200, 1200, 1200, 1200, 1200, 1100, 1100];
const scoreTotalW = scoreCols.reduce((a, b) => a + b, 0);

const scoreTableRows = [
  headerRow(
    ['Question', 'LLM Fact', 'LLM Char', 'LLM Ed',
     'Human Fact', 'Human Char', 'Human Ed', 'Effort'],
    scoreCols
  ),
  ...data.score_rows.map(r => new TableRow({
    children: [
      cell(r.question, { width: scoreCols[0] }),
      cell(fmt(r.llm_fact), { width: scoreCols[1] }),
      cell(fmt(r.llm_character), { width: scoreCols[2] }),
      cell(fmt(r.llm_editorial), { width: scoreCols[3] }),
      cell(fmt(r.human_fact), { width: scoreCols[4] }),
      cell(fmt(r.human_character), { width: scoreCols[5] }),
      cell(fmt(r.human_editorial), { width: scoreCols[6] }),
      cell(r.edit_effort !== null && r.edit_effort !== undefined ? r.edit_effort : '—', { width: scoreCols[7] }),
    ]
  }))
];

// ── Delta table ───────────────────────────────────────────────

const dims = data.delta_summary.dimensions || {};
const dimNames = ['fact_score', 'character_score', 'editorial_score',
                  'semantic_score', 'educational_score'];
const dimLabels = {
  fact_score: 'Factual accuracy',
  character_score: 'Character fidelity',
  editorial_score: 'Editorial quality',
  semantic_score: 'Semantic fidelity',
  educational_score: 'Educational value',
};

const deltaCols = [2200, 1400, 1400, 1400, 1400, 1400, 1360];
const deltaRows = [
  headerRow(
    ['Dimension', 'Mean LLM', 'Mean Human', 'Mean Delta',
     'Mean |Δ|', 'Scored N', 'Bias'],
    deltaCols
  ),
  ...dimNames.map(dim => {
    const d = dims[dim] || {};
    const dir = d.bias_direction || 'insufficient_data';
    return new TableRow({
      children: [
        cell(dimLabels[dim] || dim, { width: deltaCols[0] }),
        cell(fmt(d.mean_llm_score), { width: deltaCols[1] }),
        cell(fmt(d.mean_human_score), { width: deltaCols[2] }),
        cell(fmt(d.mean_delta), { width: deltaCols[3],
          color: d.mean_delta > 0.5 ? 'C00000' : d.mean_delta < -0.5 ? 'ED7D31' : '375623' }),
        cell(fmt(d.mean_absolute_delta), { width: deltaCols[4] }),
        cell(d.scored_count || 0, { width: deltaCols[5] }),
        cell(dir, { width: deltaCols[6], color: biasColor(dir) }),
      ]
    });
  })
];

// ── Flag frequency table ──────────────────────────────────────

const flagFreq = data.delta_summary.flag_frequency || {};
const flagEntries = Object.entries(flagFreq).sort((a, b) => b[1] - a[1]);
const flagCols = [4000, 1500, 4000];
const flagRows = [
  headerRow(['Flag type', 'Count', 'Interpretation'], flagCols),
  ...flagEntries.map(([flag, count]) => {
    const interp = {
      missed_claim: 'Factual content omitted from response',
      unsupported_claim: 'Response makes claims not in source',
      wrong_retrieval: 'Wrong RAG collection used for question type',
      moderation_false_positive: 'Valid question incorrectly blocked',
      moderation_false_negative: 'Inappropriate question passed moderation',
      too_formulaic: 'Response feels templated / predictable',
      voice_break: 'Character voice lost during response',
      too_salesy: 'Response pushes services inappropriately',
      lore_recall_failure: 'Canon lore not retrieved or used correctly',
      verbatim_regurgitation: 'Response lifts text directly from corpus',
      excellent_response: 'Positive — strong response, corpus candidate',
      publishable_with_minor_edits: 'Positive — publish after light editing',
    }[flag] || '';
    return new TableRow({
      children: [
        cell(flag.replace(/_/g, ' '), { width: flagCols[0] }),
        cell(count, { width: flagCols[1] }),
        cell(interp, { width: flagCols[2] }),
      ]
    });
  })
];

// ── Retrieval mode table ──────────────────────────────────────

const retModes = data.retrieval_modes || {};
const retCols = [3000, 1500, 5000];
const retRows = [
  headerRow(['Retrieval mode', 'Count', 'Notes'], retCols),
  ...Object.entries(retModes).map(([mode, count]) => {
    const notes = {
      factual: 'Standard IT questions — facts + voice collections',
      lore: 'TechBear canon questions — lore + voice collections',
      hybrid: 'Mixed technical + lore — all three collections',
      tall_tale: 'Origin/legend questions — lore + voice collections',
      unknown: 'Moderation artifact missing retrieval_mode field',
    }[mode] || '';
    return new TableRow({
      children: [
        cell(mode, { width: retCols[0] }),
        cell(count, { width: retCols[1] }),
        cell(notes, { width: retCols[2] }),
      ]
    });
  })
];

// ── Recommendations ───────────────────────────────────────────

const verdict = data.delta_summary.calibration_verdict || '';
const publishRate = data.delta_summary.publishable_rate;
const meanEffort = data.delta_summary.mean_edit_effort;
const modAccuracy = data.delta_summary.moderation_accuracy;

const recommendations = [];

if (verdict.includes('overconfident')) {
  recommendations.push(
    'Fact critique and character critique prompts are scoring too generously. ' +
    'Review the scoring rubric instructions and add examples of what constitutes ' +
    'a 6 vs an 8 to reduce inflation.'
  );
}
if (verdict.includes('underconfident')) {
  recommendations.push(
    'Judges are scoring below human expectation. Review critique prompts for ' +
    'overly harsh penalty language. Ensure canonical Q&A pairs score 10/10 ' +
    'before shipping prompt changes.'
  );
}
if (publishRate !== null && publishRate < 0.5) {
  recommendations.push(
    `Publishable rate is low (${pct(publishRate)}). Review voice pass and ` +
    'character critique prompts. Check whether retrieval is surfacing the right ' +
    'corpus chunks for the question types represented in this batch.'
  );
}
if (meanEffort !== null && meanEffort > 2) {
  recommendations.push(
    `Mean edit effort of ${Number(meanEffort).toFixed(1)} indicates responses ` +
    'require significant human rework. Consider whether the voice pass model ' +
    '(qwen2.5:7b) needs additional corpus examples or prompt refinement.'
  );
}
if (modAccuracy !== null && modAccuracy < 0.9) {
  recommendations.push(
    `Moderation accuracy of ${pct(modAccuracy)} is below target. Review ` +
    'false positive and false negative flags in the batch to identify patterns. ' +
    'Update character_moderation.md scope definitions if needed.'
  );
}
if ((flagFreq.lore_recall_failure || 0) > 0) {
  recommendations.push(
    `${flagFreq.lore_recall_failure} lore recall failure(s) detected. Run ` +
    'Pass C benchmark to confirm whether retrieval is finding canon chunks. ' +
    'If retrieval is working but generation ignores chunks, lore judge Phase A ' +
    'is the next priority (see v2.6 deferred items).'
  );
}
if (recommendations.length === 0) {
  recommendations.push(
    'No systematic issues detected in this batch. Continue accumulating ' +
    'reviewed runs before drawing firm calibration conclusions.'
  );
}

// ── Document assembly ─────────────────────────────────────────

const doc = new Document({
  styles: {
    default: { document: { run: { font: 'Arial', size: 22 } } },
    paragraphStyles: [
      { id: 'Heading1', name: 'Heading 1', basedOn: 'Normal', next: 'Normal',
        quickFormat: true,
        run: { size: 32, bold: true, font: 'Arial', color: '1F3864' },
        paragraph: { spacing: { before: 360, after: 240 }, outlineLevel: 0 } },
      { id: 'Heading2', name: 'Heading 2', basedOn: 'Normal', next: 'Normal',
        quickFormat: true,
        run: { size: 26, bold: true, font: 'Arial', color: '2E75B6' },
        paragraph: { spacing: { before: 280, after: 160 }, outlineLevel: 1 } },
    ]
  },
  numbering: {
    config: [
      { reference: 'bullets',
        levels: [{ level: 0, format: LevelFormat.BULLET, text: '•',
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
    ]
  },
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 },
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
      }
    },
    children: [

      // ── Cover ──
      new Paragraph({
        children: [new TextRun({
          text: 'Ask TechBear — Benchmark Report',
          bold: true, size: 48, font: 'Arial', color: '1F3864',
        })],
        spacing: { before: 720, after: 240 },
      }),
      para(`Pipeline version: ${data.pipeline_version}`),
      para(`Generated: ${new Date(data.generated_at).toLocaleString()}`),
      para('Gymnarctos Studios LLC', { italics: true, color: '7F7F7F' }),
      new Paragraph({ children: [new PageBreak()] }),

      // ── 1. Executive Summary ──
      h1('1. Executive Summary'),
      para(
        `This report covers ${data.total_runs} pipeline run(s) for ` +
        `version ${data.pipeline_version}. ` +
        `${data.complete_runs} completed successfully, ` +
        `${data.halted_runs} halted. ` +
        `${data.reviewed_runs} run(s) have completed human reviews, ` +
        `of which ${data.publishable_runs} are marked publishable.`
      ),
      para(data.delta_summary.calibration_verdict || ''),

      // ── 2. Test Configuration ──
      h1('2. Test Configuration'),
      bullet(`Pipeline version: ${data.pipeline_version}`),
      bullet(`Report generated: ${new Date(data.generated_at).toUTCString()}`),
      bullet(`Total runs included: ${data.total_runs}`),

      // ── 3. Question Set Overview ──
      h1('3. Question Set Overview'),
      para(`${data.total_runs} question(s) were processed in this evaluation batch.`),
      h2('Retrieval Mode Distribution'),
      new Table({
        width: { size: 9500, type: WidthType.DXA },
        columnWidths: retCols,
        rows: retRows,
      }),

      // ── 4. Completion Summary ──
      h1('4. Completion Summary'),
      bullet(`Complete: ${data.complete_runs} / ${data.total_runs}`),
      bullet(`Halted: ${data.halted_runs} / ${data.total_runs}`),
      bullet(`Human reviewed: ${data.reviewed_runs}`),
      bullet(`Publishable: ${data.publishable_runs}`),
      bullet(`Publishable rate: ${pct(data.delta_summary.publishable_rate)}`),
      bullet(`Mean edit effort: ${fmt(data.delta_summary.mean_edit_effort)} / 4`),
      ...(data.halted_runs > 0 ? [
        h2('Halted Run Detail'),
        ...data.halted_detail.flatMap(h => [
          para(`Question: ${h.question}`, { bold: true }),
          para(`Error: ${h.error}`, { color: 'C00000' }),
        ])
      ] : []),

      // ── 5. Score Summary ──
      h1('5. Score Summary'),
      para(
        'LLM scores are automated judge outputs. Human scores are reviewer entries ' +
        'via the batch review dashboard. Columns show per-run values.'
      ),
      new Table({
        width: { size: scoreTotalW, type: WidthType.DXA },
        columnWidths: scoreCols,
        rows: scoreTableRows,
      }),

      // ── 6. Calibration Analysis ──
      h1('6. Calibration Analysis'),
      para(
        'Delta = LLM score − Human score. ' +
        'Positive delta = judge is overconfident (scores higher than human). ' +
        'Negative delta = judge is underconfident. ' +
        'Near-zero = well-calibrated. ' +
        'Threshold for "calibrated" verdict: |mean delta| < 0.5.'
      ),
      new Table({
        width: { size: 9560, type: WidthType.DXA },
        columnWidths: deltaCols,
        rows: deltaRows,
      }),
      para(
        `Overall mean absolute delta: ${fmt(data.delta_summary.overall_mean_absolute_delta)}`
      ),
      para(data.delta_summary.calibration_verdict || '', { bold: true }),

      // ── 7. Failure Modes ──
      h1('7. Failure Modes'),
      para(
        'Flags are set by human reviewers during batch review. ' +
        'Each flag maps to a ReviewNote row in the database.'
      ),
      flagEntries.length > 0
        ? new Table({
            width: { size: 9500, type: WidthType.DXA },
            columnWidths: flagCols,
            rows: flagRows,
          })
        : para('No flags recorded in this batch.', { italics: true }),

      // ── 8. Moderation Findings ──
      h1('8. Moderation Findings'),
      bullet(`Moderation accuracy (human-verified): ${pct(data.delta_summary.moderation_accuracy)}`),
      bullet(`False positives flagged: ${data.delta_summary.flag_frequency.moderation_false_positive || 0}`),
      bullet(`False negatives flagged: ${data.delta_summary.flag_frequency.moderation_false_negative || 0}`),

      // ── 9. Retrieval Findings ──
      h1('9. Retrieval Findings'),
      bullet(`Wrong retrieval flags: ${data.delta_summary.flag_frequency.wrong_retrieval || 0}`),
      bullet(`Lore recall failures: ${data.delta_summary.flag_frequency.lore_recall_failure || 0}`),
      para(
        (data.delta_summary.flag_frequency.lore_recall_failure || 0) > 0
          ? 'Lore recall failures detected. Run Pass C benchmark to diagnose ' +
            'whether the failure is in retrieval (chunks not found) or generation ' +
            '(chunks found but ignored). See v2.6 deferred items for lore judge design.'
          : 'No lore recall failures in this batch.'
      ),

      // ── 10. Voice / Character Findings ──
      h1('10. Voice / Character Findings'),
      bullet(`Voice breaks: ${data.delta_summary.flag_frequency.voice_break || 0}`),
      bullet(`Too formulaic: ${data.delta_summary.flag_frequency.too_formulaic || 0}`),
      bullet(`Verbatim regurgitation: ${data.delta_summary.flag_frequency.verbatim_regurgitation || 0}`),
      bullet(`Too salesy: ${data.delta_summary.flag_frequency.too_salesy || 0}`),
      bullet(`Excellent responses: ${data.delta_summary.flag_frequency.excellent_response || 0}`),
      para(
        `Mean character fidelity (LLM): ` +
        `${fmt(data.delta_summary.dimensions.character_score?.mean_llm_score)} · ` +
        `Mean character fidelity (human): ` +
        `${fmt(data.delta_summary.dimensions.character_score?.mean_human_score)}`
      ),

      // ── 11. Editorial Findings ──
      h1('11. Editorial Findings'),
      bullet(`Missed claims: ${data.delta_summary.flag_frequency.missed_claim || 0}`),
      bullet(`Unsupported claims: ${data.delta_summary.flag_frequency.unsupported_claim || 0}`),
      para(
        `Mean editorial score (LLM): ` +
        `${fmt(data.delta_summary.dimensions.editorial_score?.mean_llm_score)} · ` +
        `Mean editorial score (human): ` +
        `${fmt(data.delta_summary.dimensions.editorial_score?.mean_human_score)}`
      ),

      // ── 12. Recommendations ──
      h1('12. Recommendations'),
      ...recommendations.map(r => bullet(r)),

      // ── 13. Next Steps ──
      h1('13. Next Steps'),
      bullet(
        'Continue accumulating human-reviewed runs. Target 30–50 before ' +
        'major judge recalibration.'
      ),
      bullet(
        'If lore recall failures > 0: run Pass C benchmark (lore_001 Janeway ' +
        'calibration case) to isolate retrieval vs. generation failure.'
      ),
      bullet(
        'Once publishable rate and edit effort are stable across two consecutive ' +
        'batches, consider re-ingesting approved final_answer content into ' +
        'the techbear_voice corpus.'
      ),
      bullet(
        'Tag v2.6 complete when: delta queries operational, report generates ' +
        'cleanly, pre-commit hook passes, existing pipeline still runs.'
      ),

    ]
  }]
});

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync(outPath, buffer);
  console.log('Report written to: ' + outPath);
}).catch(err => {
  console.error('DOCX generation failed:', err);
  process.exit(1);
});
"""


# =============================================================
# Main
# =============================================================


async def main(pipeline_version: str | None, output_path: Path) -> None:
    """Collect report data from the database and generate a DOCX benchmark report."""
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://localhost/ask_techbear")
    engine = create_async_engine(db_url, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    logger.info("Connecting to database...")
    async with async_session() as session:
        logger.info("Collecting report data...")
        data = await collect_report_data(session, pipeline_version)

    await engine.dispose()

    logger.info(
        "Collected: %d runs, %d reviewed, %d publishable",
        data["total_runs"],
        data["reviewed_runs"],
        data["publishable_runs"],
    )

    # Write data to temp JSON, run Node.js generator
    tmp_path = None
    js_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            json.dump(data, tmp, indent=2, default=str)
            tmp_path = tmp.name

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", delete=False
        ) as js_tmp:
            js_tmp.write(DOCX_SCRIPT)
            js_path = js_tmp.name

        output_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Generating DOCX report...")

        result = subprocess.run(
            ["node", js_path, tmp_path, str(output_path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        if result.returncode != 0:
            logger.error("Node.js error:\n%s", result.stderr)
            sys.exit(1)

        logger.info("✅ Report generated: %s", output_path)

        json_out = output_path.with_suffix(".json")
        json_out.write_text(json.dumps(data, indent=2, default=str))
        logger.info("   JSON data: %s", json_out)

    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
        if js_path:
            Path(js_path).unlink(missing_ok=True)


def cli() -> None:
    """Parse CLI arguments and invoke the report generator."""
    parser = argparse.ArgumentParser(
        description="Generate Ask TechBear benchmark report (DOCX)"
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Pipeline version to filter on (e.g. v2.6). Default: all versions.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Output path for the .docx file. "
            "Default: benchmark_results/reports/report_<timestamp>.docx"
        ),
    )
    args = parser.parse_args()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    version_slug = args.version.replace(".", "_") if args.version else "all"
    default_name = f"benchmark_report_{version_slug}_{timestamp}.docx"
    output_path = Path(args.output) if args.output else OUTPUT_DIR / default_name

    asyncio.run(main(args.version, output_path))


if __name__ == "__main__":
    cli()
