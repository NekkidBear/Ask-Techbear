# Ask TechBear v2.7 Roadmap — Lore Retrieval Diagnosis & Judge Pipeline

## Purpose

v2.7 uses the infrastructure built in v2.6 to diagnose and resolve the Janeway problem and introduce independent judge-based scoring.

## Current Status

### Confirmed Working

- Moderation routing
- Lore classification
- Retrieval mode propagation
- Benchmark infrastructure

### Current Failure Point

Pipeline reaches:

- moderation
- retrieval
- factual_pass

Pipeline halts at:

- fact_critique

### Updated Hypotheses

#### A. Retrieval Failure

Lore chunks are not being retrieved correctly.

#### B. Generation Failure

Lore chunks are retrieved but ignored.

#### C. Critique Failure

Lore answers are being judged against factual-world standards instead of canon consistency.

## Diagnostic Plan

### Milestone 1 — Janeway Root Cause Determined

Run Pass C benchmark and capture:

- retrieval_mode
- retrieved chunks
- chunk metadata
- factual draft
- critique output
- critique scores

Determine whether failure is retrieval, generation, or critique.

## Judge Pipeline

### Primary Judge

- gemma2:9b

### Fallback Judge

- Claude Sonnet

### Calibration Requirements

Canonical answers must consistently receive top-tier scores before judge results are used for pipeline decisions.

## Lore-Aware Critique Path

If retrieval_mode == lore:

Evaluate:

- canon consistency
- retrieved-context support
- contradiction detection

Do not reject solely because content is fictional.

Possible implementation:

```python
if retrieval_mode == "lore":
    run_lore_critique()
else:
    run_fact_critique()
```

## Lore Prompt Investigation

If retrieval succeeds but generation fails:

Implement lore-aware factual pass prompts using retrieval_mode.

## Technical Debt

### SQLAlchemy Typed ORM Migration

- Mapped[] migration
- mapped_column()
- remove Pyright suppressions

### Moderation Consolidation Review

Evaluate:

- services/moderation.py
- services/pipeline/moderation.py

Extract shared logic if appropriate.

## Build Order

1. Tag v2.6
2. Create feature/v2.7-lore-judge
3. Run Pass C benchmark
4. Determine root cause
5. Fix retrieval, generation, or critique path
6. Calibrate judge
7. Integrate judge scoring
8. Re-run benchmarks
9. Analyze deltas
10. Implement lore-aware critique path

## Acceptance Criteria

- Janeway root cause identified
- Lore benchmark improves from baseline
- Judge calibrated
- Judge scores persisted
- Lore-aware critique implemented if required
- Benchmark pipeline remains stable
- Pre-commit checks pass
