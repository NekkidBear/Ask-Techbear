# Ask TechBear v3.0 — Public Alpha Readiness Roadmap

**Gymnarctos Studios LLC**
_Drafted: June 2026_
_Branch target: `release/v3.0-public-alpha`_

---

## What v3.0 Means

v3.0 is the minimum public-facing release. It is not the finished product — it is the point at which Ask TechBear is stable, evaluated, and coherent enough to put in front of a real audience without embarrassment.

Per the architecture roadmap:

> _v3.0: portfolio-ready architecture including reviewer ecosystem, educational layer, async production workflow, corpus lifecycle, and benchmark gates._

The public alpha designation means:

- The pipeline produces output Jason is willing to publish under the TechBear brand
- The system has been evaluated by a human, not just by automated judges
- The corpus is clean and the train/test separation has been honored
- The content generation capability has been validated end-to-end at least once
- The live event mode is formalized and has been tested in a real event context

v3.0 is **not** a feature-complete release. It is a trust threshold.

---

## Prerequisites: v2.8 Must Close First

The following v2.8 known gaps must be resolved before v3.0 work begins. These are pipeline correctness issues, not enhancements:

- [ ] Lore bible re-chunking by episode boundary
- [ ] Episode-targeted secondary retrieval
- [ ] Moderation LLM fallback recovery becomes genuine edge case (not primary path)
- [ ] Corpus re-chunking for factual questions (ceiling fan WiFi motivating case)
- [ ] Live mode formalization: HTTP connection pooling, `RunMode` enum, phase-parallel batch execution, unified `AskTB.sh --mode event|batch` startup script
- [ ] Pre-commit hooks updated to enforce v2.8 architectural constraints
- [ ] `feature/v2.8-alpha-prep` branch merged to `v2.0-main`

Do not begin v3.0 sequencing until v2.8 is tagged and merged.

---

## v3.0 Sequencing

The following milestones must be completed **in order**. Each gate is a dependency for the next.

---

### Gate 1: Benchmark Expansion

    _Target: 40–50 test harness questions_

The current test suite (21 questions, 19 passing at v2.7 close) is too small for meaningful human calibration scoring. Human scores should calibrate judges — not drive prompt decisions directly — and that signal requires a larger, more representative question set.

- [ ] Expand test harness to 40–50 questions covering all question types: factual, troubleshooting, advice/column, lore, conversational, edge cases
- [ ] Ensure coverage of all five troubleshooting failure modes (per the Grizz column spec)
- [ ] Ensure lore questions cover all five Pass C lore categories added at v2.7 close (DS9/Quark, HAL 9000, HEX/Discworld, Rocky Horror, Mos Eisley)
- [ ] Confirm all test harness questions are **excluded from ChromaDB** — this exclusion must hold through Gate 3
- [ ] Run full benchmark suite against v2.8 baseline and confirm pass rate before proceeding

**Exit criterion:** 40–50 questions producing reliable output at v2.8 baseline. No human scoring yet.

---

### Gate 2: Human Scoring Pass

    _Single evaluation pass across all test harness questions_

Using the universal human evaluation rubric (`ask_techbear_human_eval_rubric.md`), Jason scores all test harness question outputs. This is a one-time pass, not an ongoing process.

- [ ] Run full benchmark suite and export all outputs
- [ ] Score each question across all six rubric dimensions (Query Understanding, RAG Retrieval, Voice & Character, Technical Accuracy, Structure & Format, Safety & Scope)
- [ ] Complete per-question scoring sheets
- [ ] Complete aggregate analysis section: dimension averages, failure mode frequency, collection-level signal
- [ ] Identify top 3–5 priority actions from aggregate scoring
- [ ] Archive completed rubric as versioned evaluation artifact (`eval_v1_[date].md`)

**Exit criterion:** Aggregate analysis complete. Priority actions identified. Do not apply delta feedback yet — document first, act second.

---

### Gate 3: Delta Feedback Application

    _Apply human scoring signal to pipeline_

- [ ] Apply targeted corpus or prompt scaffolding adjustments based on Gate 2 aggregate analysis
- [ ] Re-run failing questions to verify improvements on specific dimensions
- [ ] Confirm no regression on previously passing questions
- [ ] **Graduate test harness questions into corpus** — ingest into appropriate ChromaDB collection(s) as additional training signal
  - Factual/troubleshooting questions → `techbear_facts`
  - Voice/column/lore questions → `techbear_voice`
  - Lore questions → `techbear_lore`
- [ ] Tag graduated questions with `source: test_harness_v1` for future corpus audit traceability

**Exit criterion:** Delta feedback applied. Test questions in corpus. Pipeline re-validated against updated question set.

---

### Gate 4: Content Generation Validation

    _Full end-to-end article generation test_

This is the final pre-deployment pipeline test. It validates that Ask TechBear can produce long-form column content at publication quality — not just short Q&A responses.

**The test prompt:** Grizz's audio troubleshooting letter (`ask_techbear_column_prompt_spec.md`)

**The gold standard:** `ask_techbear_gold_standard_response.md` (v1.1, includes TechBear's Technology Corollary to Murphy's Law)

- [ ] Run the Grizz letter through the full async pipeline
- [ ] Score output against the column prompt spec pass criteria
- [ ] Compare output against gold standard on all five troubleshooting failure modes
- [ ] Verify Corollary invocation: named in opening, short-form callback in Failure Mode 5, closing callback
- [ ] Verify voice consistency throughout — no mid-article drift
- [ ] Verify technical accuracy on all five failure modes
- [ ] Human editorial pass on output: trim, polish, verify publishability
- [ ] **If passing:** flag for publication as first Ask TechBear column
- [ ] **If failing:** note which criteria failed, identify implicated pipeline phase, adjust and re-test before proceeding

**Post-test corpus action:**

- Ingest the **published, edited version** of the column into `techbear_voice` (not the raw pipeline output, not the gold standard — the human-approved final)
- Ingest gold standard as a separate `lore_tier: reference` document if useful for future calibration
- Do NOT ingest the gold standard before the test runs

**Exit criterion:** Content generation test passes. First column published or publication-ready. Gold standard and published column ingested into corpus.

---

### Gate 5: Live Event Validation

    _Real event, real audience, real questions_

The Queermunity soft launch (Public Alpha 001) is the target event. The system must have run at least one real tabling session before v3.0 is tagged.

- [ ] Pre-event checklist completed (per `ask-techbear-system-design.md` Event Day Checklist)
- [ ] `AskTB.sh --mode event` startup script validated on event hardware
- [ ] Cloudflare tunnel active, QR code generated and tested
- [ ] Blocklist reviewed for event-specific terms
- [ ] Live mode HTTP connection pooling confirmed (no per-call TCP connection overhead)
- [ ] Degraded mode documented and tested (fewer staff than full four-person model)
- [ ] Event runs successfully
- [ ] Post-event: STT transcription of performed responses captured
- [ ] Post-event: human review of live session outputs
- [ ] Post-event: any moderation false positives persisted with halt phase, reason, and ReviewNote for calibration

**Exit criterion:** At least one successful live tabling event completed. Post-event review done.

---

### Gate 6: Corpus Lifecycle Established

    _Ongoing content flywheel confirmed_

v3.0 requires that the corpus has a defined, repeatable intake workflow — not just the initial seed corpus.

- [ ] Jason-voice post repurposing workflow documented and tested at least once (column → corpus entry)
- [ ] Published column(s) ingested via defined workflow, not ad-hoc
- [ ] Corpus audit query confirmed working: can identify source, version, and tier of any chunk
- [ ] Curated intake policy documented: not every published post goes back into corpus automatically; criteria defined
- [ ] WordPress webhook or manual re-ingest workflow for new articles: at least one method working

**Exit criterion:** At least one post-event or post-publication corpus update completed via documented workflow.

---

### Gate 7: v3.0 Tag & Release Notes

    _Formal version close_

- [ ] All six gates above completed
- [ ] `release/v3.0-public-alpha` branch created from `v2.0-main`
- [ ] v3.0 release notes written covering: what's in, what's deferred, known limitations
- [ ] Developer setup doc updated for v3.0 state
- [ ] Pre-commit hooks updated to enforce v3.0 architectural constraints
- [ ] ROADMAP updated: v3.0 closed, v3.1+ known backlog documented

---

## What v3.0 Does NOT Include

These are explicitly deferred to v3.1 and beyond:

- Voice output / TTS (kokoro or coqui earpiece feed)
- Full automated corpus re-ingestion webhook from WordPress
- Analytics dashboard (most common topics, question volume over time)
- Multi-event archive / searchable history
- Philip co-host mode / second operator view
- NekkidBear persona extension
- Fine-tuning / LoRA
- Production publishing workflow (automated, not human-gated)
- IGDA Twin Cities architecture talk (after real event data exists — post Gate 5)
- Minnebar capstone (after IGDA)

---

## Version Milestone Summary

    ```text
    v2.5 = async pipeline exists and runs
    v2.6 = pipeline outputs become persistent evaluation data
    v2.7 = lore retrieval architecture, episode isolation, benchmark expansion
    v2.8 = pipeline correctness: live mode, corpus re-chunking, moderation hardening
    v3.0 = public alpha: evaluated, corpus-complete, live-validated, content-generating
    ```

---

## Named Artifacts Required for v3.0 Close

| Artifact                                          | Status            | Gate                       |
| ------------------------------------------------- | ----------------- | -------------------------- |
| `ask_techbear_human_eval_rubric.md`               | ✅ Drafted        | Gate 2                     |
| `ask_techbear_column_prompt_spec.md`              | ✅ Drafted        | Gate 4                     |
| `ask_techbear_gold_standard_response.md`          | ✅ Drafted (v1.1) | Gate 4                     |
| `techbear_character_corollary_entry.md`           | ✅ Drafted        | Gate 4 (voice corpus)      |
| `character_voice.md` Corollary insertion          | ✅ Merged         | v2.8 character file update |
| Completed human eval rubric (`eval_v1_[date].md`) | ⏳ Gate 2         | Gate 2                     |
| Published first Ask TechBear column               | ⏳ Gate 4         | Gate 4                     |
| v3.0 release notes                                | ⏳ Gate 7         | Gate 7                     |

---

_Gymnarctos Studios LLC — Internal Technical Document_
_Ask TechBear is a Gymnarctos Studios LLC project. TechBear is the alter ego of Jason, CEO/CTO/Chief Everything Bear._
