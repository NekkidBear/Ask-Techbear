# Future Concept: Retrieval Enhancements

## Status

Partially scoped / Future Implementation
Items here are understood but intentionally deferred until the three-collection
RAG architecture (techbear_facts, techbear_voice, techbear_lore) has stabilized
and produced enough human-reviewed benchmark data to validate improvements.

---

## Overview

The current retrieval system uses cosine similarity over ChromaDB embeddings
with per-collection filtering. This works well for the current corpus size and
question set. As the corpus grows and retrieval mode routing matures, several
enhancements will improve answer quality and retrieval precision.

---

## Hybrid Retrieval Scoring

Currently each collection is queried independently and results are returned
as flat lists. A hybrid scoring layer would weight results from multiple
collections before they reach the generation phase.

Example weighting for a hybrid retrieval mode question:

```
final_score = (0.6 × facts_similarity) + (0.4 × lore_similarity)
```

The weights would vary by retrieval mode:

| Mode      | Facts | Lore | Voice |
| --------- | ----- | ---- | ----- |
| factual   | 0.7   | 0.0  | 0.3   |
| lore      | 0.0   | 0.7  | 0.3   |
| hybrid    | 0.4   | 0.4  | 0.2   |
| tall_tale | 0.0   | 0.6  | 0.4   |

Prerequisites: enough benchmark data to empirically validate weight choices.
Premature tuning without validation data is optimization theater.

---

## Metadata Filtering

ChromaDB supports `where` clause filtering on metadata fields. Current usage:

- `is_fiction: False` on facts retrieval

Potential additional filters:

- `series` — retrieve only from a specific content series
  (e.g., only Ask TechBear columns for question-answer style retrieval)
- `date` — prefer newer content for fast-moving topics
  (pairs with the Knowledge Stewardship freshness system)
- `voice_score` — boost minimum threshold for voice collection queries
- `lore_tier` — already implemented for canon vs tall_tale separation
- `content_type` — separate article body from bio callbacks

These filters are low-cost to add but require the metadata to exist in the
collection at ingest time. Most are already tagged during ingest.

---

## Reranking Layer

After initial retrieval, a reranking pass could reorder results by a second
relevance signal before passing chunks to the generation phase.

Candidate approaches:

- **MMR (Maximal Marginal Relevance)** — reduces redundancy across retrieved
  chunks from the same source post, improving coverage across articles
- **Cross-encoder reranking** — a small model scores (query, chunk) pairs
  directly rather than comparing embeddings; more accurate but slower
- **Recency boost** — apply a date-decay multiplier to down-rank older chunks
  when the topic is known to change frequently

Reranking is most valuable when the corpus is large enough that initial
retrieval frequently returns redundant or marginally relevant chunks.
At the current corpus size (~700 chunks per collection) it is premature.

---

## pgvector Backend

ChromaDB is the correct choice for local development and tabling events.
For a future cloud-hosted or multi-user deployment, pgvector (PostgreSQL
vector extension) would consolidate the vector store into the existing
PostgreSQL database, eliminating a separate dependency.

Migration path:

1. Abstract the retriever behind a protocol/interface
   (`FactsRetriever`, `LoreRetriever`, etc.)
2. Implement `ChromaRetriever` as the current concrete implementation
3. Implement `PgvectorRetriever` when deployment requires it
4. Switch via environment variable — no pipeline code changes

The retriever abstraction is the prerequisite. ChromaDB stays until
the abstraction exists.

---

## Token Usage Tracking

Ollama returns token counts in the response payload (`eval_count`,
`prompt_eval_count`). These are not currently captured.

Adding token tracking to each phase's score dict would enable:

- Cost estimation for cloud model deployment
- Per-phase latency and token budget analysis
- Identification of phases that consume disproportionate context

Implementation: one additional field in `_call_ollama()` return value,
propagated to the phase's scores dict entry.

---

## Prerequisites for All Items Above

- Stable three-collection RAG architecture ✓ (v2.6)
- Human-reviewed benchmark dataset (v2.6 in progress)
- Score delta tracking between LLM and human reviewers (v2.6 in progress)
- Empirical baseline to validate improvements against (v3.0 target)

Do not implement retrieval enhancements before the baseline exists.
Optimization without measurement is guesswork.
