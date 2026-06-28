# Ask TechBear v2.6 Release Notes

## Overview

v2.6 focused on evaluation, observability, benchmarking, and pipeline diagnostics rather than response-quality improvements.

This release establishes the infrastructure required to systematically measure, review, score, and improve future pipeline behavior.

## Major Features

### Evaluation Infrastructure

- Pipeline run persistence
- Evaluation score storage
- Human review workflow
- Batch review dashboard support
- Benchmark result reporting
- Score delta tracking

### Benchmarking & Diagnostics

- Expanded benchmark orchestration
- Pass-based benchmark execution
- Retrieval-mode visibility
- Pipeline stage tracing
- Improved debugging output

### Lore Pipeline Foundations

- Lore retrieval mode support
- Lore corpus ingestion tooling
- Retrieval mode propagation through pipeline artifacts
- Initial lore benchmark set (`lore_001` and related cases)

### Quality & Developer Experience

- Pre-commit integrity checks
- Flake8 clean
- Pyright cleanup with documented SQLAlchemy suppressions
- Pylint score improved to 9.94/10

## Janeway Investigation Status

### Fixed During v2.6 Closeout

- Moderation correctly classifies lore questions.
- `Have you ever met Captain Janeway?` now routes as:
  - decision = pass
  - retrieval_mode = lore
  - scope = OFF_TOPIC_FUN

### Remaining Issue

The pipeline now reaches:

- moderation
- retrieval
- factual_pass

but halts during:

- fact_critique

Current hypothesis:

- lore questions are being evaluated using factual-world accuracy rules
- canon consistency and factual accuracy are not yet separated

Root-cause analysis deferred to v2.7.

## Known Technical Debt

### SQLAlchemy Typed ORM Migration

Current suppressions exist for classic SQLAlchemy Column typing.

Planned:

- migrate to Mapped[]
- migrate to mapped_column()
- remove temporary Pyright suppressions

### Moderation Duplication Review

Evaluate overlap between:

- backend/services/moderation.py
- backend/services/pipeline/moderation.py

Determine whether shared moderation logic should be extracted while preserving separate runtime adapters.

## Acceptance Summary

- Evaluation infrastructure operational
- Benchmark framework operational
- Lore routing operational
- Integrity checks passing
- Pre-commit hooks passing

v2.6 establishes measurement and diagnostic capabilities required for future quality improvements.
