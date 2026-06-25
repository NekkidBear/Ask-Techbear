# ARCHIVED: RAG System TODOs (Pre-v2.5)

**Status: Superseded**
This document was the active TODO list during early RAG and benchmarking
development, prior to the v2.5 async pipeline implementation.

Most items listed here have been completed. See the notes below each section.
For genuinely still-open items, see `docs/concepts/retrieval_enhancements.md`.

---

## 🧹 Linting & Code Style

### Import Ordering
**COMPLETED in v2.5.3** — All pipeline and service files have PEP8-compliant
import ordering enforced by `.flake8` and `.pylintrc`.

### Missing Docstrings
**COMPLETED in v2.5.3 / v2.6** — Docstrings added to all pipeline phases,
service modules, and scripts. Alembic migration files use file-level pylint
disable for convention violations inherent to Alembic's boilerplate pattern.

### Final Newline / Formatting
**COMPLETED** — All Python files end with a newline. `.flake8` enforces W292.

---

## ⚠️ DO NOT IGNORE items — current status

- **Retrieval correctness** — COMPLETED. Three-collection architecture
  (techbear_facts, techbear_voice, techbear_lore) with metadata filtering,
  bio section splitting, and retrieval mode routing via moderation classification.

- **Deterministic dataset ordering** — MAINTAINED. Benchmark questions are
  defined as static lists in `tests/test_pipeline.py`.

- **Mode isolation** — COMPLETED and extended. Original raw/prompt_only/rag_facts/
  rag_full modes replaced by factual/lore/hybrid/tall_tale retrieval routing.

- **Consistent return schema** — COMPLETED. All pipeline phases return the
  artifact dict. Schema documented in orchestrator and handoff modules.

- **Import path correctness** — COMPLETED. All imports use
  `backend.services.pipeline.*` and `backend.services.rag.*` paths.

---

## 🔧 Future Improvements — current status

- **Hybrid retrieval scoring** — Still open. Documented in
  `docs/concepts/retrieval_enhancements.md`.

- **Metadata filtering** — Partially complete. `is_fiction` and `lore_tier`
  filters implemented. Additional filters (series, date, voice_score) deferred.

- **Reranking layer** — Still open. Documented in
  `docs/concepts/retrieval_enhancements.md`.

- **JSONL benchmark output** — Deferred. JSON output exists; JSONL is minor.

- **Token usage tracking** — Still open. Documented in
  `docs/concepts/retrieval_enhancements.md`.

- **Experiment metadata tracking** — COMPLETED via pipeline_runs schema.
  model_config and retrieval_config JSONB fields capture run context.

---

## 🚀 Future Architecture — current status

- **Pluggable retriever interface** — Still open. Documented in
  `docs/concepts/retrieval_enhancements.md` as pgvector migration path.

- **LLM-as-judge scoring** — COMPLETED in v2.5.3. Claude API judge implemented.

- **Factuality scoring** — COMPLETED. fact_critique phase with accuracy/safety scores.

- **Voice adherence scoring** — COMPLETED. character_critique phase with
  fidelity, regurgitation, anti_formulaic, and structure_compliance scores.

- **Educational effectiveness scoring** — COMPLETED in v2.5.3 (beyond original scope).
  educational_critique phase added per v4 roadmap architectural insight.

- **API layer integration** — Partially complete. Live mode generate route
  (`POST /questions/{id}/generate`) uses legacy single-pass llm.py.
  Async pipeline integration with the live route is deferred to v2.6.2.

---

*Archived: June 2026. Original author: Jason King-Lowe / Gymnarctos Studios LLC.*
