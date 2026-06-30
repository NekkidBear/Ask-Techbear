<!-- markdownlint-disable MD024 MD025 -->
# Ask TechBear v2.9 — Code Quality and Pre-Public Hardening

_Gymnarctos Studios LLC_
_Branch: `feature/v2.9-hardening`_
_Base tag: `v2.8-stabilization` (pending close)_

---

## Context

v2.8 closes with a working public alpha (Queermunity soft launch). v2.9 is the
hardening pass before the codebase becomes publicly visible — IGDA Twin Cities
architecture talk, Minnebar, and any open-source or portfolio exposure.

v2.9 does not add features. It makes the existing codebase correct, clean, and
defensible to a technical audience.

The guiding principle: if someone in the IGDA audience asks to see the code,
it should reflect the architecture decisions described in the talk — not an
earlier draft of them.

---

## What v2.8 Will Have Shipped

For reference when reading v2.9 scope:

- Debug logging cleanup (print → logger.debug)
- Multi-question test execution
- Editorial readiness scoring
- Live/batch mode profiles
- Lore bible re-chunked by episode boundary
- Episode-targeted secondary retrieval
- Shared JSON recovery utility (json_utils.py)
- Regression pack --suite flag
- Queermunity event completed

Known items carried forward from v2.8 with intentional suppression:

- `score_deltas.py:234` pyright `arg-type` error — suppressed pending
  SQLAlchemy `Mapped[]` migration (this file)
- Several pylint complexity warnings (too-many-locals, too-many-branches,
  too-many-statements) — suppressed at function level in pipeline phases
  where complexity is structural, not accidental

---

## Priority 1 — SQLAlchemy 2.0 Mapped[] Migration

### Background

The codebase currently uses SQLAlchemy 1.x-style column declarations:

```python
# Current style (pre-2.0)
class PipelineRun(Base):
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    status = Column(String, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
```

SQLAlchemy 2.0 introduces `Mapped[]` type annotations with `mapped_column()`:

```python
# Target style (2.0)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, DateTime
from uuid import UUID, uuid4

class PipelineRun(Base):
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    status: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

### Why This Matters

- Pyright understands `Mapped[UUID]` — accessing `row.id` yields `UUID`,
  not `Column[UUID]`. The type error in `score_deltas.py:234` disappears
  without a cast.
- Relationships, nullable fields, and optional columns become explicit in
  the type signature rather than implicit in the Column() call.
- Required for SQLAlchemy 2.0 native async support (asyncpg path).
- Makes the schema self-documenting — a reader can understand the model
  from the type annotations alone.

### Scope

All models in `backend/models/` and any inline model definitions:

- `pipeline_runs`
- `pipeline_artifacts`
- `llm_scores`
- `human_reviews`
- `review_notes`
- `questions`
- `sessions`
- `session_context`
- `blocklist`

### Migration Risk

Medium. Mechanical changes, but touches load-bearing schema code.

Mitigations:

- Do not change column names, types, or nullability during migration —
  pure style change only
- Run full Alembic migration check before and after: `alembic check`
- Run full benchmark suite after migration to confirm no persistence
  regressions
- Keep v2.8 tag as rollback point

### Interim Suppression (v2.8)

Until migration is complete, suppress the known pyright error at the call site
with a self-documenting comment:

```python
get_run_delta(run_id=some_row.run_id)  # type: ignore[arg-type]  # pending Mapped[] migration (v2.9)
```

This comment is grep-able at migration time: `grep -r "pending Mapped\[\]"`.

### Acceptance Criteria

- All model columns declared as `Mapped[T] = mapped_column(...)`
- All relationships declared as `Mapped[List[T]]` or `Mapped[Optional[T]]`
- `score_deltas.py:234` pyright error resolved without cast
- Zero new pyright errors introduced
- Full benchmark suite passes after migration
- All `# type: ignore[arg-type]  # pending Mapped[] migration` suppressions removed

---

## Priority 2 — Pylint Complexity Suppressions Audit

### Background

Several pylint complexity warnings are currently suppressed across the pipeline.
v2.9 audits each suppression and either:

1. Confirms it is structural (pipeline phases are legitimately complex) and
   documents why at the suppression site
2. Refactors the function to genuinely reduce complexity where the warning
   is identifying real technical debt

### Known Suppressions to Audit

| File                           | Warning                                                 | Line     | Assessment                        |
| ------------------------------ | ------------------------------------------------------- | -------- | --------------------------------- |
| `lint_check.py`                | too-many-locals, too-many-branches, too-many-statements | 104      | Audit — may be refactorable       |
| `lint_check.py`                | subprocess-run-check                                    | 118      | Fix: add `check=False` explicitly |
| `lint_check.py`                | missing-function-docstring                              | 276      | Fix: add one-liner docstring      |
| `check_thresholds.py`          | unnecessary-comprehension                               | 238      | Fix: `set(avg_scores)`            |
| `generate_benchmark_report.py` | too-many-locals                                         | 73       | Audit                             |
| `ingest_corpus.py`             | too-many-locals                                         | 516      | Likely structural — document      |
| `ingest_lore_supplementary.py` | too-many-locals, too-many-statements                    | 281, 350 | Audit                             |
| `persistence.py`               | too-many-locals                                         | 192      | Likely structural — document      |
| `score_deltas.py`              | too-many-locals                                         | 92, 241  | Audit — 30 locals at 241 is high  |
| `score_deltas.py`              | too-many-branches                                       | 241      | Audit                             |
| `orchestrator.py`              | too-many-arguments                                      | 122      | Consider config dataclass         |
| `fact_critique.py`             | too-many-branches, too-many-statements                  | 273      | Likely structural — document      |

### Easy Fixes (Do First)

These are not complexity debates — they have clear correct resolutions:

- `lint_check.py:118` — `subprocess.run(..., check=False)` explicit
- `lint_check.py:276` — add docstring
- `check_thresholds.py:238` — `set(avg_scores)`

### Orchestrator Config Dataclass (Consider)

`orchestrator.py:122` has too-many-arguments (10/5). A config dataclass
groups related parameters without changing behavior:

```python
@dataclass
class OrchestratorConfig:
    generation_mode: str
    run_label: Optional[str]
    verbose: bool
    retry_limit: int
    # ... etc
```

This is a meaningful improvement if the orchestrator argument list has grown
organically — worth evaluating during the audit.

### score_deltas.py:241 (30 Locals)

30 local variables in one function is a signal worth investigating. After the
`Mapped[]` migration resolves the type error, audit this function for genuine
refactor opportunities before suppressing.

---

## Priority 3 — Pre-Commit Hook Update

### Goal

v2.9 pre-commit hook enforces the architectural constraints added in v2.8
and v2.9:

- `Mapped[]` style enforced in new model additions (post-migration)
- `json_utils.py` used for JSON recovery (no new phase-local parse variants)
- No `# type: ignore` without accompanying comment explaining suppression
- `subprocess.run` calls must have explicit `check=` parameter

### Implementation

Extend `.githooks/pre-commit-v2.5-pipeline` or create
`.githooks/pre-commit-v2.9-quality` depending on hook dispatcher structure
at v2.8 close.

---

## Priority 4 — Documentation Pass

### Goal

Before the codebase is shown publicly, developer-facing documentation should
match the actual architecture.

### Items

- `DEVELOPER_SETUP.md` — verify all setup steps reflect v2.8/v2.9 state;
  update any references to old module paths or removed flags
- `README.md` — confirm architecture overview matches current pipeline
- Character file headers — confirm each file's preamble accurately describes
  its role and which pipeline phases consume it
- Inline docstrings — audit pipeline phase files for missing or stale
  docstrings on public functions
- `json_utils.py` — new in v2.8; ensure fully docstringed on arrival in v2.9

---

## Priority 5 — Lint Check Script Hardening

### Background

The lint check script itself has pylint warnings (too-many-locals,
too-many-branches, too-many-statements at line 104). Since this script
is the quality gate for the rest of the codebase, it should model the
standards it enforces.

### Goal

Refactor `lint_check.py` to:

- Extract report-generation logic into a separate helper function
- Reduce branch count in the main runner function
- Add missing docstring at line 276
- Add `check=False` at line 118

### Acceptance Criteria

- `lint_check.py` passes its own lint check without suppressions

---

## v2.9 Acceptance Criteria

v2.9 is complete when:

- All SQLAlchemy models use `Mapped[T] = mapped_column(...)` style
- Zero pyright errors
- All `# type: ignore[arg-type]  # pending Mapped[] migration` suppressions removed
- Easy pylint fixes applied (subprocess-run-check, unnecessary-comprehension,
  missing-function-docstring)
- All remaining pylint suppressions documented with rationale at suppression site
- Pre-commit hook updated to enforce v2.9 constraints
- `DEVELOPER_SETUP.md` and `README.md` accurate to v2.9 state
- `lint_check.py` passes its own lint check without suppressions
- Full benchmark suite passes at v2.9 close (no regressions from refactoring)
- Pylint score >= 9.94/10 (maintain or improve from v2.8 baseline)

---

## What v2.9 Does NOT Include

Deferred to v3.0 and beyond:

- New pipeline features
- New model integrations
- Corpus expansion
- Frontend changes
- LoRA / fine-tuning
- pgvector migration
- Dynamic freshness search

v2.9 is a code quality release. If it adds behavior, something has gone wrong.

---

## Version Milestone Summary

```text
v2.5 = async pipeline exists and runs
v2.6 = pipeline outputs become persistent evaluation data
v2.7 = lore retrieval architecture, episode isolation, benchmark expansion
v2.8 = pipeline correctness: live mode, corpus re-chunking, moderation hardening
v2.9 = code quality: Mapped[] migration, complexity audit, pre-public hardening
v3.0 = public alpha: evaluated, corpus-complete, live-validated, content-generating
```

---

_Last updated: 2026-06-30_
_Gymnarctos Studios LLC — Internal reference only_
