<!-- markdownlint-disable MD024 MD025 -->
# Ask TechBear v2.8 — Stabilization, Corpus, and Editorial Quality

_Gymnarctos Studios LLC_
_Branch: `feature/v2.8-stabilization`_
_Base tag: `v2.7-lore-evaluation` (pending merge)_

---

## Context

v2.7 introduced lore routing, episode isolation, and the three-collection RAG
architecture. By end of v2.7, the pipeline runs end-to-end on all question
types with human-in-the-loop editorial review.

v2.8 focuses on four things:

- Stabilizing the developer experience (logging, test tooling, fault tolerance)
- Improving editorial workflow support (readiness scoring, mode profiles)
- Fixing corpus and retrieval quality (lore_bible re-chunking, episode-targeted retrieval)
- Strengthening moderation and judge robustness

The public alpha launch (Queermunity event, July/August) is the v2.8 milestone.

---

## What v2.7 Shipped

For reference when reading v2.8 scope:

- Three-collection RAG architecture (techbear_facts, techbear_voice, techbear_lore)
- Lore/tall_tale/factual routing via moderation
- Episode isolation post-processor (episode_isolation.py)
- Fact_critique lore mode with episode relevance check
- Moderation fallback retrieval_mode recovery
- Satirical submission intent class (corpus Pass B false positive fix)
- Helpdesk water moderation false positive fix
- NoneType safety_score None guard in failure_reason
- generation_mode field stub for v2.8 batch mode
- test_pipeline.py: --verbose, --summary, --phase flags; retrieval diagnostics;
  moderation raw response capture; lore scoring threshold fix

Known gaps shipped with human-review coverage:

- Lore recall averaging 1.0–1.8/5 (retrieval thin on episode-specific facts)
- lore_bible chunks span episode boundaries causing cross-episode contamination
  (partially mitigated by episode isolation; root fix is re-chunking)
- db_002 character_critique loop cap — stable repro, needs investigation
- SQLAlchemy engine chatter in --summary mode (not suppressed without --verbose)

---

## Priority 1 — Stability and Developer Experience

### 1. Logging Cleanup

#### Problem

DEBUG print() statements remain in moderation.py and orchestrator.py from
development. These interleave with stage output in all modes including --summary.

#### Requirements

Standard mode: stage progress only, minimal output.
Summary mode: one-line result per question, aggregate statistics.
Verbose mode: SQLAlchemy logs, retrieval diagnostics, retry activity,
critique flags, routing decisions, raw moderation output.
Debug mode (optional): full prompts, raw model responses, stack traces.

#### Implementation

- Replace print() diagnostics in moderation.py and orchestrator.py with logger.debug()
- Centralized logger configuration
- Verbosity-controlled output
- SQLAlchemy engine logging suppressed in non-verbose modes (partially done
  in test_pipeline.py; needs to cover the moderation/orchestrator debug prints)

---

### 2. Multi-Question Test Execution

#### Current State

--question flag accepts only a single ID.

#### Goal

```text
python -m tests.test_pipeline --question lore_002 lore_006 lore_004
```

#### Implementation

- argparse nargs='+' on --question argument
- questions_to_run construction handles list
- Aggregate stats still computed across the subset

---

### 3. Improved Retry Visibility

#### Goal

Expose retry behavior clearly in verbose output.

#### Example Output

```text
factual_pass retry 1/2
  accuracy: 0 → 9
  trigger: wrong_episode flag from fact_critique
```

#### Captured Fields

- Retry count
- Score before and after retry
- Retry trigger reason (which flag, which critique)

---

### 4. Fault-Tolerant Reporting

#### Problem

Formatting bugs can make successful runs appear failed.
NoneType.**format** in \_summary_line() — partially fixed in v2.7 for
routing_flag; full audit needed for all formatted fields.

corpus_002 safety_score=None propagating into halt reason string —
fixed in fact_critique.py v2.7; confirm no other None-propagation paths.

#### Requirements

- Defensive formatting on all score fields in \_summary_line() and write_summary()
- Safe summary generation — reporting failure must not abort the run
- Preserve JSON artifacts on reporting failures
- Mark technical_error in result rather than crashing

---

## Priority 2 — Editorial Workflow Support

### 5. Editorial Readiness Scoring

#### Problem

Pass/fail does not reflect the actual publication workflow.
The pipeline is permanently human-in-the-loop. "Editor effort" is
more useful than autonomous pass/fail.

#### Current Workflow

Generate → Human Review → Edit if Needed → Publish

#### Goal

Separate pipeline success from publication readiness.

#### Proposed Output Fields

```json
{
  "pipeline_result": "complete",
  "generation_mode": "live",
  "readiness": "minor_edit",
  "readiness_score": 82,
  "human_review_required": true
}
```

#### Readiness Categories

- publish_ready — publish after normal audit
- minor_edit — small cleanup needed
- major_edit — correct foundation but significant editing needed
- reject — wrong answer / major factual issue
- technical_error — pipeline or reporting failure

#### Scoring Inputs

- fact_critique accuracy and safety scores
- character_critique fidelity and anti_formulaic scores
- editorial_critique clarity and FK scores
- routing mismatches
- critical flags
- retry counts
- episode_context.episode_isolated (lore mode)
- lore recall score when available

#### Benefits

- Tracks editor effort over time
- Supports human-in-the-loop publishing workflow
- More meaningful KPI than pass/fail
- Enables editorial effort analytics in v2.9+

---

### 6. Live Mode vs Batch Mode Profiles

#### Problem

Word-count failures often reflect mode mismatch rather than quality failures.
The pipeline currently generates live-mode length (150–250 words) for all
questions. Batch/publication questions need longer, richer responses.

#### Live Mode

Purpose: convention booth, live events, presentation support
Targets: 150–250 words, fast generation, low latency, read-aloud friendly

#### Batch Mode

Purpose: blog generation, newsletter drafts, editorial workflow
Targets: 250–800+ words, richer explanations, higher token budget, SEO-friendly

#### Future Extension

Article Mode: 600–1500+ words

#### Implementation

- generation_mode field already stubbed in v2.7 artifact output
- character_voice.md word count targets parameterized by mode
- test harness --mode flag to select profile per run
- Readiness scoring (item 5) mode-aware word count evaluation

#### Benefits

- Removes false word-count failures from benchmark results
- Better aligns scoring with actual use case
- Enables future automated batch publication workflow

---

## Priority 3 — Lore Corpus and Retrieval Quality

### 7. Lore Bible Re-Chunking by Episode Boundary

#### Problem

The lore_bible.md is currently ingested as a single document chunked by token
count. Episode summaries span chunk boundaries, so a single retrieved chunk
can contain content from two adjacent episodes. This causes cross-episode
contamination even after episode isolation runs.

Episode 2 (Jurassic Park) key facts — Nedry, security arrays, coffee maker
outlet — are in the lore_bible Episode 2 summary section, but that section
may share a chunk with Episode 1 or Episode 3 content depending on token
boundaries.

#### Root Cause

Token-count chunking does not respect the `### Episode N` section headers
that cleanly separate episodes in the source document.

#### Fix

Re-ingest the lore_bible split at `### Episode N —` headers rather than
token count. Each episode section becomes one document in the lore collection,
tagged with:

- source_type: lore_bible_episode
- episode_number: N
- post_id: matching WordPress episode post ID
- lore_tier: canon_reference
- retrieval_tags: from the existing tags in each episode section

#### Expected Result

- lore_bible chunks are episode-scoped — no cross-episode contamination possible
- Episode 2 summary chunk contains exactly: Nedry, security arrays, coffee
  maker outlet, torrential rain, GPS coordinates, dinosaurs
- Episode isolation still runs as a safety net but has nothing to filter

#### Prerequisites

- Identify post_id mapping for each episode (already in Techbear_lore_bios.md)
- Update ingest script to split on episode headers before chunking
- Re-ingest lore_bible after split
- Validate with lore_004 (Jurassic Park) and lore_001 (Janeway) recall scores

---

### 8. Episode-Targeted Secondary Retrieval

#### Problem

Even with correct episode routing, semantic similarity retrieval returns only
the top-k chunks by cosine similarity. The key episode facts may rank below
atmospheric or narrative chunks, leaving the factual pass content-starved.

lore_004 (Jurassic Park) retrieved only 2 episode-specific chunks after
isolation removed the lore_bible contamination. The Nedry/security arrays
facts were not in those 2 chunks.

#### Fix

Two-stage retrieval for lore mode when a dominant episode is identified:

Stage 1: broad semantic search across lore collection (current behavior)
→ identifies dominant post_id via episode isolation

Stage 2: targeted query filtered by where={"post_id": dominant_post_id}
with k=10 or higher, pulling all chunks from that episode post
→ supplements Stage 1 results with full episode coverage

Deduplication: merge Stage 1 and Stage 2 results, deduplicate by chunk ID,
pass combined set to factual pass.

#### Prerequisites

- Episode isolation already identifies dominant_post_id (v2.7)
- Requires post_id metadata on WordPress episode chunks (already present)
- Lore bible re-chunking (item 7) ensures lore_bible chunks also have post_id
- ChromaDB where clause filtering (already supported)

#### Expected Result

- All episode narrative chunks available to the factual pass regardless of
  semantic similarity ranking
- Lore recall scores expected to improve significantly for episode questions

---

### 9. Episode Isolation Validation in Test Harness

#### Problem

episode_context is stored in the artifact but not yet surfaced in test
harness summary output or benchmark reporting.

#### Goal

- Add episode_context to retrieval_diagnostics in test harness output
- Report in verbose summary: episode_isolated, dominant post_id, chunks_removed
- Add episode_match metric to Pass C benchmark: did the pipeline answer
  the correct episode vs. a different one?
- Add episode_id field to test_questions.json for Pass C questions so
  the harness can validate dominant_post_id against expected episode

#### Benefits

- Episode contamination becomes a directly measurable metric
- Catches wrong-episode answers that pass fact_critique (lore relevance check
  catches most, but surface proxy may miss subtle cases)

---

### 10. Lore Relevance Judge (Phase A)

#### Prerequisite

Pass C benchmark data confirming retrieval works reliably after items 7 and 8.

#### Problem

Current judges evaluate "Is this valid lore?" but not "Is this the lore the
user asked about?" The episode relevance check in fact_critique (v2.7) catches
wrong-episode answers during generation. A dedicated lore judge evaluates the
final voice draft with full episode context.

#### Goal

Phase A: pipeline critique phase between character_critique and editorial_pass
for lore-routed responses (mistral:latest).

Evaluates:

- Episode match: does the voice draft address the correct episode?
- Canon recall: does the draft include episode-specific details?
- Canon contradiction: does the draft contradict established lore?

Scoring: extends lore_recall 0–5 rubric with judge-confirmed claims
rather than surface proxy.

#### Phase B (Deferred)

Editorial tool for new Multiverse episode drafts.
Claude API for source IP plausibility checking.
Prerequisite: Phase A data confirming retrieval works reliably.

---

## Priority 4 — Moderation and Judge Robustness

### 11. Shared JSON Recovery Utility

#### Problem

JSON recovery logic is currently duplicated across moderation.py
(\_parse_llm_json, \_extract_first_json_object) and fact_critique.py
(\_parse_response). Each phase has its own variant with slightly
different error handling.

#### Goal

Extract shared utility to backend/services/pipeline/json_utils.py:

```python
def parse_llm_json(raw: str) -> dict:
    """Parse LLM JSON, repairing common trailing-text failures."""

def extract_first_json_object(raw: str) -> str:
    """Extract first complete JSON object, tolerating trailing commentary."""
```

Import in all phases. Consistent behavior across moderation, fact_critique,
character_critique, editorial phases.

#### Status

Prototype identified during v2.7 debugging. Consolidation deferred to v2.8.

---

### 12. Structured Parse Telemetry

#### Goal

Track per phase:

```json
{
  "parse_success": true,
  "parse_repaired": false,
  "parse_failed": false,
  "repair_method": null
}
```

#### Benefits

- Identify which models are worst JSON producers
- Measure prompt compliance over time
- Correlate parse failures with routing errors

---

### 13. Moderation Scope Calibration

#### Problem

All lore/tall_tale questions route OFF_TOPIC_FUN scope rather than IN_SCOPE.
Functionally correct but test fixture expectations all say IN_SCOPE, creating
spurious scope mismatch noise in every test run.

#### Fix

Two-part:

1. Update test_questions.json expected_scope for lore and tall_tale categories
   from IN_SCOPE to OFF_TOPIC_FUN
2. Consider whether IN_SCOPE is the right scope for lore questions in
   character_moderation.md — TechBear's Multiverse episodes are arguably
   in-scope for a TechBear Q&A event

#### Note

Routing (lore/factual/tall_tale) is correct. This is a scope label
calibration issue only.

---

## Priority 5 — Benchmarking and Evaluation

### 14. Regression Packs

#### Goal

Named test suites runnable as a unit:

```text
python -m tests.test_pipeline --suite lore_core
python -m tests.test_pipeline --suite moderation_edge_cases
python -m tests.test_pipeline --suite corpus_rag
```

#### Proposed Suites

- lore_core: lore_001–lore_009
- moderation_edge_cases: satirical submissions, helpdesk water, jailbreak attempts
- routing: questions designed to stress retrieval_mode classification
- corpus_rag: Pass B corpus questions
- safety: questions near content boundaries

---

### 15. Failure Artifact Preservation

#### Goal

Automatically preserve debugging artifacts for failed or human-review runs.

#### Saved Fields

- Retrieval context (chunks, episode_context)
- All drafts (factual, educational, voice)
- All critique outputs
- Routing decisions and moderation classification
- Retry history and loop counts

#### Storage Path

```text
tests/test_output/failures/{timestamp}_{question_id}/
```

#### Benefits

- Easier debugging of intermittent failures
- Historical comparison across pipeline versions
- Input data for lore judge calibration

---

### 16. Benchmark Expansion Before Judge Calibration

#### Principle

Run the v2.7 benchmark first, then expand the question set to 40–50 questions
before human-scoring, so human scores calibrate judges rather than directly
drive prompt decisions.

#### Target Question Counts

- Pass A: expand from 5 to 10–15 (more observation/event categories)
- Pass B: expand from 7 to 15–20 (more corpus questions, more satirical setups)
- Pass C: expand from 9 to 15–20 (cover all published episodes, add multi-episode
  questions, add recurring element questions like Kevin archetype and Dax chronology)

#### Gate

Do not recalibrate judges based on small-n results. Expand question set first.

---

## v2.8 Acceptance Criteria

v2.8 is complete when:

- Debug print() statements replaced with logger.debug() in all pipeline phases
- Multi-question --question flag operational
- Editorial readiness scoring field present in all pipeline artifacts
- Live/batch mode profiles configurable and reflected in word count targets
- lore_bible re-chunked by episode boundary and re-ingested
- Episode-targeted secondary retrieval implemented and validated
- Pass C avg lore recall >= 2.5/5 on the core 9 questions
- lore_004 (Jurassic Park) lore recall >= 3/5 with Nedry/security arrays in draft
- Shared JSON recovery utility extracted to json_utils.py
- test_questions.json scope expectations corrected for lore/tall_tale
- Regression pack --suite flag operational for at least lore_core
- All 21 current questions complete without reporting crashes
- Queermunity event soft launch readiness confirmed

---

## Deferred to v3.x

### Dynamic Freshness Search

Trigger web search when corpus confidence is low, data is stale, or topic
falls outside retrieval coverage.

Components: search integration, citation validation, freshness scoring,
optional human review gate.

### Full Lore Judge (Phase B)

Editorial tool for new Multiverse episode drafts.
Claude API for source IP plausibility.
Prerequisite: Phase A (item 10) data first.

### Corpus Repurposing Workflow

Running Jason-voice posts (IDs 725, 1071, 1255, 1758 as candidates) through
educational and voice passes only. Deferred until voice engine is stable and
batch mode profiles are confirmed.

### pgvector Backend

Consolidate ChromaDB into PostgreSQL via pgvector for cloud deployment.
Prerequisite: retriever abstraction layer first.

### LoRA / Fine-tuning

Not until sufficient human-reviewed benchmark data exists to validate.

---

_Last updated: 2026-06-29_
_Gymnarctos Studios LLC — Internal reference only_
