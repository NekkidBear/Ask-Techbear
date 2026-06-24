# Ask TechBear — V2 Addendum: Model + Pipeline Design Session

_Gymnarctos Studios LLC | Companion to ROADMAP.md and ask-techbear-system-design.md_
_Captures architecture decisions from the post-6/20-event design conversation, before any v2 code is written_

---

## How this relates to the existing docs

This addendum **extends** the "Topic duplication detection" and "Adaptive moderation" sections of `ROADMAP.md` largely as written. It **confirms** the existing "Two-stage generation pipeline with dual RAG" section's core structure — sequential generation, not merged — after weighing the alternative in this session:

- **Original design (`ROADMAP.md`), now confirmed:** two _sequential_ generation calls — stage 1 produces a plain, no-character fact answer grounded in the knowledge index; stage 2 takes that plain answer and rewrites it in voice using a second retrieval pass against the personality/blog corpus.
- **Alternative considered and rejected:** parallel retrieval into a single merged generation call. Rejected because when a single-call output goes wrong, there's no way to tell whether it was a fact-checking failure or a voice failure without re-running the prompt and guessing. The sequential version isolates that by construction — stage 1's output is checkable as plain text _before_ any personality is layered on, so a bad result is attributable to a specific stage rather than an undifferentiated "something's off."

**Live mode stays single-call.** The latency budget for live performance doesn't allow two sequential inference calls. Two-stage sequential generation is async-only. See the live pipeline section below.

**What this system actually is (from external architecture review):** not a chatbot. A multi-stage content compiler with human validation gates and dual execution modes. Each stage transforms a constrained artifact — structured state, not free-form text — and critique models disagree structurally rather than confirming stylistically.

---

## Model selection

**Hardware ceiling:** MacBook Pro M2, 16GB unified memory, 650GB free storage. Storage is a non-issue; RAM is the binding constraint. Given this Mac runs inference _and_ the full dev/prod stack simultaneously (Postgres, FastAPI, React, Cloudflare tunnel), the practical ceiling is the 7–8B model class (Q4_K_M quantization, ~4–6GB), not the theoretical 13–14B ceiling quoted for dedicated AI boxes.

**Validation step (do this first, before any RAG work):** A/B Qwen (2.5 or 3, 7–8B) against the current `llama3.2` on the existing v1.0 single-prompt system, no RAG involved yet. This isolates how much of the "takes 2–3 tries" problem is raw model capability vs. missing grounding before adding engineering surface that would confound the comparison. v1.0's current retry rate is a clean baseline specifically because nothing else has changed yet.

**Working assumption pending that test:** Qwen wins and becomes the primary generation model.

**Three-critique async pipeline introduces Mistral as a third model candidate.** With generation (Qwen), fact/safety critique (small fast model), voice critique (potential Mistral slot), and aggregate scoring (Qwen under a different prompt), the async pipeline runs at most three distinct models per batch. Each loads once per stage-batch, not once per question, so this is tractable on 16GB. Mistral's stylistic judgment makes it a reasonable candidate for the voice critic role specifically — worth evaluating after the core pipeline is built, not before.

**Model role mapping (async):**

| Role                             | Candidate                      |
| -------------------------------- | ------------------------------ |
| Blocklist                        | rapidfuzz (no model)           |
| Input moderation gate            | llama3.2:1b                    |
| Factual generation (stage 1)     | Qwen 7–8B                      |
| Fact/safety/moderation critic    | llama3.2:1b or 3B              |
| Voice rewrite (stage 2)          | Qwen 7–8B                      |
| Voice critic                     | Mistral 7–8B (TBD)             |
| Aggregate scorer                 | Qwen 7–8B (different prompt)   |
| Clustering judgment              | Qwen 7–8B (different prompt)   |
| Consultation redirect generation | Qwen 7–8B (constrained prompt) |
| STT transcription                | MLX Whisper (not Ollama)       |

---

## Two front ends, one brain

Both pipelines share the same corpus, the same two indices, and the same `character_file.md`. They differ in latency budget and output stakes.

### Live performance pipeline — three-person staffing model

```pseudocode
blocklist (automatic, instant)
        ↓
Reviewer — triages questions, groups related ones, enforces conversation depth limit
        ↓
Moderator — approves, triggers single-call dual-RAG generation, approves draft
        ↓
Onstage reader (mic'd) — presents the approved question aloud, announces attendee name
        ↓
Jason — performs as TechBear, teleprompter-style off the approved draft, light wording adjustments
```

**No separate LLM moderation pass live.** The system design doc's "Stage 2 — Topic filter" is structurally redundant with a dedicated Reviewer present — a person is better suited to edge-case judgment than `llama3.2:1b` and the latency budget doesn't allow it anyway.

**The Reviewer's grouping role is the human version of the async clustering pass.** Same job, done with full audience context instead of chunk-overlap math. Consistent with the existing roadmap's instinct that operator judgment wins over auto-merging for live sessions.

**Performance mode raises the bar on "approved."** Teleprompter-style with light improv means the draft needs to be performance-ready cold. `character_file.md`'s structure — Reaction Beat, the Read, the Gospel, Warm Close, 150–250 word constraint — is the literal checklist the Moderator checks against before approving. There's no editorial pass live; the Moderator is that check.

**Staffing realism / degraded mode.** The full four-person model is the target for larger events. Worth deciding explicitly what the degraded mode looks like when fewer people are available — Reviewer+Moderator collapsed into one role, onstage reader dropped and Jason reads directly off the dashboard, etc. The June 20th event ran as a two-person operation; the design should not assume four is always available without a documented fallback.

**Future: lightweight live-assist tooling (not yet building).** Low-latency non-generative assistive tools for Reviewer and Moderator — fast retrieval/similarity lookups, not LLM calls — to handle mechanical heavy lifting so the live team can focus attention on edge cases. Reuses the same embedding indices and chunk-overlap math the async pipeline already builds. Sequenced after core pipelines are validated.

---

### Async "inbox" / column pipeline — full pass

Batched by _stage_ across the week's queue, not by question. Each model loads once per stage-batch rather than once per question — this is what makes multi-pass tractable on 16GB.

```pseudocode
input moderation gate
        ↓
retrieval (knowledge + personality indices, both)
        ↓
clustering pass
        ↓
stage 1: factual generation
        ↓
fact/safety/moderation critic
        ↓
stage 2: voice rewrite
        ↓
voice critic
        ↓
aggregate scorer → loop or escalate
        ↓
human approval gate
        ↓
publish + conditional corpus re-ingestion
```

#### 1. Input moderation gate

Per-question, small fast model. Must run _before_ clustering — clustering before moderation risks a borderline question riding through on a clean cluster's coattails, or a whole cluster being rejected because one member trips the blocklist.

Conversation depth is also enforced here: if `conversation_depth >= 2` for this attendee token, route to **consultation redirect generation** rather than queuing for the full pipeline. This check happens at submission in live mode (immediate response, no generation) and at the moderation gate in async (lightweight constrained generation of the redirect, no full pipeline).

#### 2. Retrieval

Dual-RAG against both indices for every question that survives moderation.

- **Knowledge index:** standard semantic-similarity embedding (`nomic-embed-text`). Retrieves factual content — articles, guides, procedures.
- **Personality index:** voice-matching is a style problem, not a meaning-similarity problem. Evaluate whether full-corpus semantic embedding outperforms a curated exemplar set for this specific job before committing to full retrieval. The `character_file.md` static exemplars already do a version of this; dynamic retrieval should demonstrably improve on that before adding the overhead.

**Session-level deduplication for personality retrieval:** track which personality chunks have already been retrieved during this session/batch and de-prioritize repeats. Without this, a small corpus surfaces the same strongest-matching exemplars repeatedly, and the same metaphor or verbal tic could appear in multiple answers published the same week — working against the exact "less canned" goal RAG was supposed to solve. Generalizes the once-per-five-messages aphorism limit already in `character_file.md` to the retrieval layer.

**Curated corpus intake:** not everything that gets published goes back into the corpus automatically. See corpus feedback loop section below.

#### 3. Clustering pass

Two tiers:

- **Pre-filter (no LLM call):** compute overlap on each question's _retrieved chunk set_ — not raw question text, which is a false signal (surface vocabulary overlap doesn't distinguish same-answer-different-phrasing from different-answers-shared-frame). Narrows the full inbox to candidate groups.
- **Judgment (LLM call):** for each candidate group, structured output — treatment type, member questions, one-line rationale. This is reasoning the overlap math can't do.

**Two treatment types:**

- _Merge-with-branches:_ same question asked different ways (e.g. wifi connection across three OSes). One answer with per-OS branches; generation must be explicitly instructed to address every member question, not just the first-phrased one.
- _Themed-bundle:_ different questions sharing an anchor (e.g. guest network setup + neighbor wifi theft + parental controls — all route through router security settings but need distinct procedures). Multi-part column piece under one connecting frame.

**Follow-up questions are exempt from clustering** — `conversation_depth > 0` is an automatic standalone signal. Clustering a follow-up with a thematically similar question from a different attendee breaks the conversation thread.

**Editorial checks the rationale, not just the draft.** The clustering pass proposes with reasoning attached; human approval gate confirms the frame actually holds together as a piece before treating it as final.

#### 4. Stage 1 — Factual generation

Plain-language, no-character answer grounded in retrieved knowledge chunks. No sass, no metaphor, no structure — just "is this correct." Optimized purely for accuracy. This is the inspectable checkpoint that makes the sequential two-stage design worth its inference cost: a hallucinated fact is far easier to catch in plain prose than buried inside a TechBear bit.

#### 5. Fact/safety/moderation critic

Sits between stage 1 and stage 2. Checks the factual draft _before_ personality is applied:

- Factual consistency against retrieved knowledge chunks
- Hallucination or unsupported claims
- Missing critical information
- Anything that slipped past input moderation (intent-level issues the blocklist couldn't catch)
- Output: issues list, confidence score, pass/fail

If fail: loop back to stage 1 (up to iteration cap), then escalate to human review if still failing. A fact problem is easier to attribute and fix before voice is layered on top.

#### 6. Stage 2 — Voice rewrite

Takes the critic-approved factual draft and rewrites in TechBear's voice using retrieved personality chunks as live few-shot examples. Hard constraint: **cannot introduce new facts.** The voice pass transforms existing claims, it does not add or modify them. This constraint must be explicit in the stage 2 prompt and is one of the things the voice critic checks for specifically.

Cluster-aware output shape: branched single answer for merge-with-branches, multi-part column piece for themed-bundle, standard Q&A for standalone.

#### 7. Voice critic

Sits after stage 2. Checks:

- Character consistency against `character_file.md`
- No new facts introduced during rewrite
- Anti-regurgitation: contiguous-run check (~8–10 words) against retrieved personality chunks using `rapidfuzz partial_ratio` — same library, third job. Flags verbatim lifted sentences regardless of what percentage of total response they represent. Does _not_ penalize recurring TechBear verbal tics or signature phrases — those are voice consistency, not regurgitation.
- Cross-batch consistency: checks draft against other approved drafts in this batch for repeated jokes, metaphors, or structural beats. A model can produce output that's original relative to the corpus but repetitive relative to other answers published the same week.
- Rhythm and performability for content intended for live reading (column pieces that will eventually be performed, not just published).

#### 8. Aggregate scorer

Synthesizes both critique reports into a single structured output: overall confidence score, unresolved issues, go/no-go recommendation. Not generating new content — reasoning about two structured inputs. Uses Qwen under a critic-style prompt rather than a generation prompt; same model, different job.

If confidence below threshold: re-run the relevant stage (up to iteration cap, as external architecture doc specifies). If still below threshold after cap: escalate to human review rather than auto-publish or auto-reject. The cap is important — without it, a persistently low-confidence item loops indefinitely.

The aggregate scorer's confidence score is also the primary signal the orchestration layer uses to decide whether an item goes straight to the human approval gate or needs a loop first.

#### 9. Human approval gate

Jason reviews the final draft, both critique reports, and the aggregate score. Can approve, edit, or reject. Rejection with a note feeds back as a signal — see corpus feedback loop below. This is the highest-leverage human decision point in the async pipeline, not just final editing: the chunk validation / clustering rationale review that happens earlier is the second highest.

#### 10. Publish + conditional corpus re-ingestion

See corpus feedback loop section.

---

## Consultation redirect

Triggered when `conversation_depth >= 2` for an attendee token. Applies in both live and async modes.

**What it is:** a short (1–2 sentence) character-consistent redirect that acknowledges the specific question topic warmly and pivots to the consultation scheduling link. Not a full pipeline call — a lightweight constrained single-call generation with the submitted question as the only context input beyond the system prompt.

**Critical prompt constraint:** the redirect **cannot start answering the question, even partially.** A model given "this is complex, redirect warmly" will often try to be helpful by summarizing what it would have said first, which gives away the answer for free and defeats the funnel. This must be an explicit negative constraint in the prompt, not just assumed.

**Character model:** the existing refusal pattern in `character_file.md` is structurally identical — acknowledge, redirect, stay in voice, close warmly. The consultation redirect is a more specific instance of the same pattern:

> "Honey, this one's got more layers than my Grandma Bruin's baklava — this is exactly the kind of thing we should sit down and dig into properly. Let's get you on Jason's calendar, sugar. [scheduling link]"

**Live mode:** redirect check happens at submission before any generation is triggered, so no pipeline cost.

**Async mode:** redirect check happens at the moderation gate. The lightweight constrained generation still runs (to produce the warm, topic-aware redirect rather than a canned message), but skips the full pipeline entirely.

---

## Conversation threading

Connects three features from `ROADMAP.md` into one coherent conversation arc rather than three standalone features:

1. Realtime response loop (every submitter sees their answer regardless of highlight status)
2. Chat-like system, 2 interaction cycles
3. Lead funnel (third interaction triggers consultation routing)

**The arc:** question → answer delivered to that attendee → optional follow-up → answer delivered → third question triggers warm consultation redirect rather than generation.

**Anonymous session token is the linchpin.** Without it, the system can't recognize a second question came from the same person as the first, can't deliver responses back to specific attendees, and can't enforce conversation depth. Already flagged in `ROADMAP.md` as the prerequisite for the realtime response loop — now load-bearing for three features rather than one, which moves it up the priority stack. Token issued at first submission, stored in browser localStorage on the submission form, keyed to session ID.

**Two context types, currently conflated in `ROLLING_CONTEXT`:**

- _Session-level rolling context:_ last N answered Q&As across the whole session — TechBear's consistency within a show, already partially implemented.
- _Per-thread conversation context:_ this specific attendee's prior question(s) and answer(s) — needed for follow-up generation to make sense. A follow-up question is often incomprehensible without the prior exchange. Generation for `conversation_depth > 0` must receive the full thread as context, not just the new question.

These two context types serve different purposes and should be kept distinct in the prompt assembly, even if they ultimately both get injected into the same generation call.

**Schema additions (additive, no breaking changes):**

```sql
-- attendee_sessions: anonymous token issued at first submission
CREATE TABLE attendee_sessions (
    token           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES sessions(id),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- questions table additions:
ALTER TABLE questions ADD COLUMN conversation_id    UUID REFERENCES attendee_sessions(token);
ALTER TABLE questions ADD COLUMN conversation_depth INTEGER DEFAULT 0;
ALTER TABLE questions ADD COLUMN parent_question_id INTEGER REFERENCES questions(id);
```

`conversation_depth` is 0 for first question, 1 for follow-up, redirect triggered at 2. `parent_question_id` is a nullable FK back to the question being followed up on — used to assemble the per-thread context window for generation.

**Follow-up questions are exempt from clustering** (see clustering pass above). `conversation_depth > 0` is an automatic standalone signal. Clustering a follow-up with a thematically similar question from a different attendee breaks the thread.

**Clustering and the realtime response loop interact for bundled questions.** If several standalone questions are bundled into a themed-column piece, each submitter should receive the full column piece rather than only "their" part extracted back out — the column is the answer, and partial extraction would misrepresent it. This is a design decision worth confirming before building the delivery layer.

---

## Corpus feedback loop

The corpus doesn't just grow from original blog posts — it grows from real performed content over time, but **only approved content gets re-ingested.** The critique models and human approval gate together act as the quality filter that prevents a bad draft from becoming a voice exemplar for future answers. This is the mechanism that makes the system self-improving rather than self-reinforcing.

**Intake curation:** not everything published goes into the corpus automatically. Criteria for re-ingestion eligibility:

- Passed all three critique layers without exceeding the iteration cap (low-confidence items that required multiple loops are excluded even if eventually approved)
- Received human approval without significant edits (heavily edited approvals are borderline — they're correct, but they're not representative of clean TechBear output)
- For performed content specifically: the `session_context.response_text` (as-performed version, captured via STT pipeline — see below) is the version ingested, not the draft. The corpus reflects what was actually said, not what was written and then deviated from in performance.

**Rejection feedback loop:** a rejected draft with a note from Jason is a signal about what "bad TechBear" looks like, which is as useful for calibrating the critique models as approvals are. Periodic review of rejection notes can inform `character_file.md` updates and critique prompt refinement — same adaptive loop as the existing blocklist override log in `ROADMAP.md`, applied to corpus quality rather than moderation tuning.

**The critique model's long-term role:** the fact/safety critic and voice critic aren't just quality gates for individual drafts — they're the gatekeepers that determine what the system learns from. A hallucinated fact that slips past both critics and gets approved gets re-ingested as if it were correct, and future retrieval will surface it as a reference. The critics' accuracy directly determines corpus integrity over time, not just output quality per-answer.

---

## Post-performance STT pipeline

**Recording setup:** lavalier mic on Jason _and_ on the onstage reader. Both are mic'd, resolving the open question of who captures the attendee name announcement — either person can say it and the recording captures it cleanly.

**Transcription:** MLX Whisper (`whisper-large-v3-turbo`), Apple Silicon optimized, runs as a post-event batch job. Not live, so it sits entirely outside the latency constraints that apply to everything else in this doc. Install via pip, CLI and Python API both available, drops into the existing `backend/scripts/` pattern.

**Name-anchored matching:** the Moderator or onstage reader announces the attendee's name immediately before each question ("Clueless in Cincinnati asks..."). This turns STT matching from a content-inference problem into an anchor-matching problem — much more reliable.

Mechanism: `rapidfuzz` fuzzy-matches the closed list of that session's `attendee_name` values against the transcript. Sorting matches by transcript position segments the recording into per-question stretches. No vector database or embeddings involved — name-anchored string matching is a stronger and cheaper signal here than semantic similarity.

Residual unmatched cases (mangled name, two attendees sharing a first name, unanounced question): either a quick Qwen judgment pass with the leftover questions and unmatched transcript stretch, or direct manual review. Small enough at realistic session volumes to not be a burden.

Output writes to `session_context.response_text` — the field already documented for exactly this purpose, no schema change needed.

**Handle-style names are a meaningfully better matching anchor** than plain first names: lower collision risk within a session, more distinctive for both Whisper to transcribe accurately and rapidfuzz to match confidently. The seed data already uses this convention ("Clueless in Cincinnati," "Tab_Collector," "Glitch_Witch") — worth maintaining as a soft convention for the submission form UX.

**Training habit:** "say the name before the question" needs to be a literal checklist item on the dashboard or event-day checklist, not just institutional knowledge. Whoever is new to the Moderator or reader role on a given event day should see this as an explicit reminder.

---

## Anti-regurgitation

Not an aggregate similarity percentage — these are noisy in both directions. Generic technical vocabulary ("head into your router's admin panel") will trip a percentage threshold even though it's just shared domain language, while a single verbatim 10-word lifted sentence can sit well under 10% of a full response and slide through undetected.

**Contiguous-run check:** flag any unbroken ~8–10 word sequence matching a retrieved chunk verbatim, regardless of total response fraction. `rapidfuzz partial_ratio / token_set_ratio` against each retrieved chunk — same library, third job alongside moderation blocklist and STT name-matching.

**Two kinds of reuse, only one is a problem:**

- Recurring TechBear verbal tics, signature phrases, endearments from the personality index → voice consistency, working as intended, do not flag.
- Full clauses or sentences lifted wholesale, structure intact, from either index → flag.

**Cross-batch check (voice critic's job):** also diff the draft against other approved drafts in this batch. A model can produce output original relative to the corpus but repetitive relative to other answers published the same week.

---

## Known costs / open risks

- **Latency (live):** worth timing dual-RAG single-call end-to-end on the actual hardware once wired in. Not assumed fast enough.
- **Failure surface:** each pipeline stage is an independent failure point. The dashboard / review surface needs to show intermediate stage output (stage 1 factual draft, each critique report) alongside the final draft so a bad result is attributable to a specific stage rather than undifferentiated.
- **Corpus dependency:** no dual-RAG work functions until the blog corpus is chunked and embedded. Blocks build steps 2–3. Real setup work, not a prompt change.
- **Clustering thresholds unvalidated:** the wifi/wireless-pitfalls examples reason about mechanism correctly but actual overlap thresholds need tuning against real inbox data, not assumed in advance.
- **Staffing realism:** full four-person live model requires people to be available. Degraded mode (fewer staff) needs an explicit documented fallback rather than discovering it live.
- **Corpus quality compounding:** the critique models' accuracy determines long-term corpus integrity. A hallucinated fact that slips through and gets re-ingested becomes a retrieval reference for future answers. Early critique prompt calibration is worth treating as a first-class task, not a tuning afterthought.
- **Consultation redirect prompt guard:** the redirect generation prompt must explicitly forbid partial answers. A model tends toward helpfulness and will start answering before redirecting unless this is a hard negative constraint.
- **Recording consent:** attendees at community events (Queermunity especially) should be informed that Q&A sessions are recorded for transcription. A sign at the table or brief announcement before the session starts is appropriate — not just a technical detail.
- **Cloudflare tunnel single point of failure:** if the tunnel drops mid-event, public submission goes down entirely. Worth knowing and accepting rather than discovering live.
- **Solo-operator maintenance load:** five-to-six async passes plus three critique models is meaningful prompt-tuning surface for one person. The build order below exists to avoid paying this cost before confirming each stage earns its place.
- **Frontend scope:** three-person staffing model needs Reviewer/Moderator split into distinct views plus an onstage-reader teleprompter display. Additive to the planned WebSocket push, not a rebuild, but real work.

---

## Build order

Each step answers whether the next one is actually needed before committing to it:

1. **A/B Qwen vs. Llama 3.3** on v1.0, no RAG. Isolates model capability from grounding.
2. **Anonymous session token** — prerequisite for realtime response loop, conversation threading, and consultation funnel. Build before touching any of those three features.
3. **Knowledge index**, wire into existing single-call generation.
4. **Personality index**, implement two-stage sequential generation (stage 1 factual → stage 2 voice rewrite).
5. **Checkpoint:** does dual-RAG two-stage reduce the retry rate? If yes, proceed. If no, revisit prompt structure or model choice before adding more pipeline.
6. **Fact/safety critic** between stage 1 and stage 2.
7. **Voice critic** after stage 2, including anti-regurgitation check.
8. **Aggregate scorer** synthesizing both critics.
9. **Corpus re-ingestion pipeline** with intake curation criteria.
10. **Realtime response delivery** to attendee tokens (FastAPI SSE or WebSocket).
11. **Conversation threading** (per-thread context window, follow-up routing, depth tracking).
12. **Consultation redirect** generation (lightweight, constrained prompt).
13. **Clustering pass** (pre-filter + judgment call), once real inbox data exists to tune thresholds against.
14. **STT post-performance pipeline** (MLX Whisper + rapidfuzz name-matching + `session_context.response_text` update).
15. **Orchestration state machine** across all stages, once each individual stage is validated.
16. **Live-assist tooling** (non-generative, retrieval-only assistive signals for Reviewer/Moderator).

---

_This document captures design decisions and reasoning. Implementation details (exact prompt text, quantization flags, chunk sizes, overlap thresholds) are deferred to the relevant build step rather than specified here, since many depend on empirical tuning against real data rather than design-time assumptions._

---

## Benchmark findings — v2.2 real-RAG run (2026-06-23)

_First benchmark run with live ChromaDB retrieval. 5 models × 4 modes × 5 questions = 100 responses. Supersedes the "working assumption" model assignments above with empirical data._

### Revised model role mapping

| Role                               | Candidate                        | Basis                                                                     |
| ---------------------------------- | -------------------------------- | ------------------------------------------------------------------------- |
| Live TechBear (general audience)   | `llama3.1:8b`                    | Most consistent, RAG fixes remaining gaps, zero refusals with RAG context |
| Live TechBear (technical audience) | `qwen2.5:7B` or `mistral:latest` | TBD by event type                                                         |
| Voice rewrite (async stage 2)      | `qwen2.5:7B`                     | Strong character voice, warm, handles profanity gracefully                |
| Fact/safety critique               | `mistral:latest`                 | Analytically sharp, but Q6 hallucination flagged — see below              |
| Voice critique                     | TBD                              | Needs dedicated evaluation                                                |
| Judge (scoring)                    | Claude API (external)            | Fully independent, no conflict of interest                                |
| Disqualified                       | `llama3.2:1b`                    | Capability too limited — flat refusals on legitimate helpdesk questions   |
| Conditional                        | `llama3.2:latest`                | 2 refusals in rag_full — unreliable under best conditions                 |

### Key findings

**`raw` mode is unsafe for public deployment** — confirmed across all models. The character file is load-bearing for safety, not just voice. Multiple models gave dangerous or incorrect advice in raw mode (e.g. instructing users to plug unknown USB drives into their computers to scan them). The character file suppresses these failure modes; RAG context amplifies the effect.

**`llama3.2:1b` disqualified** — flat `"I can't help with that"` refusals on Carol's locked laptop question (treating it as an illegal activity request) and Bob's profanity-laden but legitimate hardware question. Fundamental capability gap, not a prompt issue.

**`mistral:latest` Q6 hallucination** — in `rag_facts` mode rewrote the user's question in first person and answered itself ("Dear Tech Enthusiast, I'm experiencing issues with my laptop..."). Disqualifying for any role where response accuracy matters until root cause is identified.

**`llama3.1:8b` profanity refusal gap** — refuses Bob's question in `prompt_only` mode ("I cannot provide a response that contains profanity") but handles it correctly with RAG context. The character file alone is not overriding the base model's profanity filter. Character file should include explicit guidance on redirecting colorful language with TechBear sass rather than refusing.

**No character breaks** — zero AI self-disclosure ("I'm an AI / as an AI") across all 100 responses. Character file is working for persona containment.

**Event-tailored model selection is viable** — different models suit different audience types. `llama3.1:8b` for general/community audiences, potentially `qwen2.5:7B` or `mistral:latest` for technical audiences. Model selection per event type is a legitimate operational strategy, not over-engineering.

### Scoring ground truth established

Real published Ask TechBear column Q&A pairs extracted from the WordPress corpus (`column_questions_real.json`) replace the partially-authored `column_questions.json`. These canonical responses are the judge calibration set — the judge must score them 10/10 across all dimensions before the scoring system ships. Any dimension scoring below 10 on a canonical response indicates judge prompt miscalibration, not response quality issues.

---

## Async pipeline architecture — feature/v2.5-async-pipeline

_Branched from `feature/v2.0-main` after v2.2 benchmark merge. Supersedes the sketch in "Async inbox / column pipeline" above with a more detailed phase design._

### Full pipeline with critique layers

```pseudocode
SUBMISSION
    ↓
MODERATION LAYER
  - Blocklist: rapidfuzz (rule-based, instant)
  - Intent/sentiment: LLM prompt (frustration vs. directed attack)
    e.g. "My fucking laptop crashes" → frustration, pass
         "You fucking arsehole" → directed attack, queue
  - Human review queue + reason field (every override documented)
  - Training data accumulates for future fine-tuning
    ↓
FACTUAL PASS (llama3.1:8b + rag_facts)
  - Retrieves from techbear_facts only
  - Generates technically accurate plain-text draft
  - No character voice — correctness only
    ↓
FACT + SAFETY CRITIQUE (mistral:latest)
  - Technical accuracy (0-10)
  - Safety/guardrail compliance (0-10)
  - Flags: hallucinations, dangerous advice, missing steps
  - Output: critique JSON + pass/fail gate
    ↓ (only if passes)
VOICE PASS (qwen2.5:7B + rag_full)
  - Input: fact-checked artifact
  - Constraint: rephrase only — no add, no subtract
  - Retrieves from techbear_voice
  - Output: character artifact
    ↓
SEMANTIC FIDELITY CHECK (mistral:latest — analytical prompt)
  - Diffs fact artifact vs. voice artifact
  - Flags: changed_claims[], removed_claims[], added_claims[]
  - Pass criteria: no material changes
    ↓ (only if passes)
CHARACTER FIDELITY CHECK (dedicated judge prompt)
  - Character fidelity (0-10)
  - Regurgitation check (0-10) — penalizes verbatim voice chunk reuse
  - Structure compliance (0-10) — reaction/read/gospel/close rhythm
  - Word count compliance (0-10)
  - Anti-formulaic check — penalizes responses that feel like mad-libs
    ↓
EDITORIAL PASS (llama3.1:8b + character_editorial.md)
  - Input: character artifact + character file (editorial slice)
  - Flags anomalies as potential errors vs. intentional voice
  - Does NOT auto-correct — annotates for human review
  - "Red squiggly" model: writer has final say
    ↓
EDITORIAL CRITIQUE
  - Flesch-Kincaid readability (deterministic Python, no LLM)
  - Clarity (0-10)
  - Formatting compliance (0-10)
  - Grammar anomaly classification: possible_error vs. intentional_voice
    ↓
HUMAN REVIEW HANDOFF
  - Before draft (raw factual artifact)
  - After draft (final editorial artifact)
  - All automated scores per phase
  - Editorial flags with accept/reject decision fields
  - Human scores same rubric (0-10 per dimension)
  - Delta: automated score vs. human score per dimension
  - Divergence flagged for judge recalibration
    ↓
APPROVED DRAFT → REINGESTION LOOP
  - Approved drafts reingested into corpus
  - As-performed STT version preferred over written draft
  - Rejection notes captured as "bad TechBear" signal
  - Periodic review informs character file updates and critique prompt refinement
```

### Anti-formulaic design constraint

The reaction/read/gospel/close structure is a **rhythm guide, not a template**. The voice critique must explicitly penalize responses that feel like mad-libs — same opening energy every time, same transition phrases, same closing beat. Operationalized as:

- Check for repeated phrases across recent responses (corpus-level, not per-response)
- Penalize direct structural echoes of voice corpus examples
- Score "surprise" — does the response do something unexpected within the structure?

### Scoring system

- **Scale:** 0-10 (not 0-5) — finer gradient needed to distinguish nuanced differences in similar-quality outputs
- **Human vs. LLM scoring:** same rubric, independent passes, delta tracked
- **Calibration:** canonical column Q&A pairs must score 10/10 before judge ships
- **Calibration set:** deliberate selection covering all scope categories (IN_SCOPE, FUNNEL, OFF_TOPIC_FUN, OFF_TOPIC_PERSONAL, OFF_TOPIC_INAPPROPRIATE) plus at least one HellDesk Zone format response
- **Judge model:** Claude API (external, independent)
- **Training loop:** human accept/reject decisions on editorial flags feed classifier training data

---

## Character file refactor — planned

_Prerequisite for async pipeline efficiency. Full character file fed to every phase is wasteful and potentially counterproductive — voice instructions can bias factual passes toward performance over accuracy._

### Proposed split

```pseudocode
backend/character/
    character_full.md          # current file, kept for reference
    character_identity.md      # who TechBear is, core values, mission
    character_facts.md         # technical accuracy standards, scope rules,
                               # funnel triggers, safety boundaries
    character_voice.md         # rhythm, tone, metaphor style, structure,
                               # reaction/read/gospel/close, word count
    character_editorial.md     # readability targets, formatting rules,
                               # intentional grammar markers vs errors
    character_moderation.md    # scope taxonomy, intent classification
                               # guidance, hard boundaries
```

### Phase-to-file mapping

| Phase              | Character files                                    |
| ------------------ | -------------------------------------------------- |
| Moderation         | `character_moderation.md`                          |
| Factual pass       | `character_facts.md`                               |
| Fact critique      | `character_facts.md`                               |
| Voice pass         | `character_identity.md` + `character_voice.md`     |
| Character critique | `character_voice.md`                               |
| Editorial pass     | `character_identity.md` + `character_editorial.md` |
| Editorial critique | `character_editorial.md`                           |

### Future-proofing

This split enables the multi-character architecture — when NekkidBear or other personas come online, swap `character_voice.md` without touching facts or editorial layers. `character_facts.md` and `character_moderation.md` are likely shared across personas; voice and identity files are persona-specific.

### Known gap — profanity handling

`llama3.1:8b` refuses questions containing profanity in `prompt_only` mode, overriding character instructions with base model safety training. `character_moderation.md` must include explicit guidance: redirect colorful language with TechBear sass, do not refuse. Frustration-directed profanity ("my fucking laptop") is not a safety event.

---

## Benchmark cleanup utility — planned

Benchmark results accumulate quickly. Proposed `cleanup_benchmarks.py` with `--older-than` (days) and `--dry-run` flags. Design decisions:

- **Combined CSVs:** keep indefinitely (audit trail for model selection decisions)
- **Per-model/per-mode CSVs:** candidates for cleanup after N days (redundant once combined)
- **Version-based cleanup:** `v2_1_*` files are superseded by `v2_2_*` and can be cleaned explicitly
- **Never auto-delete** — manual invocation only, `--dry-run` required to preview before commit

---

## Cloudflare tunnel maintenance

Tunnel version should be kept current. `brew upgrade cloudflared` after each patch release. Restart tunnel after upgrade. Add to event-day checklist: confirm tunnel version is current before event start.

---

## Frontend additions — planned

### QR code overlay on slideshow (event tool)

- Persistent, non-interactive overlay on slideshow display
- Links directly to submit page
- Works on every attendee phone without touching the event laptop
- No button, no UX friction, no display interruption
- Implementation: static QR image generated once, CSS-overlaid on slideshow

### Demo view — `/demo` route (shareable + website)

Two distinct use cases, one implementation:

1. **Tester sharing** — send one URL without exposing `/dashboard`, `/slideshow`, or `/moderator` endpoints
2. **Gymnarctos Studios website embed** — public-facing, shows TechBear in action, drives interest

Design:

- Greatest hits carousel (read-only, subset of slideshow content)
- Live submit form embedded alongside
- No dashboard link, no slideshow controls, no moderator UI
- Shareable as standalone URL — `ask-techbear.gymnarctosstudiosllc.com/demo`

**Cloudflare implication:** expose `/demo` and `/submit` publicly, keep `/dashboard`, `/slideshow`, `/moderator` behind access controls. Meaningful security improvement over current setup.

---

## Pre-commit hook architecture — v2.5

### Two-file approach

Separate hook files per branch, both run on merge — catches v2.5 changes that accidentally break v2.0 constraints at commit time rather than at merge time.

```pseudocode
.githooks/                        # tracked by git
    pre-commit-v2.0-integrity     # existing v2.0 constraints
    pre-commit-v2.5-pipeline      # new v2.5 pipeline constraints
.git/hooks/
    pre-commit                    # dispatcher (not tracked)
```

Dispatcher pattern:

```bash
#!/bin/bash
bash .githooks/pre-commit-v2.0-integrity || exit 1
bash .githooks/pre-commit-v2.5-pipeline || exit 1
```

One-time dev setup after cloning:

```bash
chmod +x .githooks/*
ln -sf ../../.githooks/pre-commit .git/hooks/pre-commit
```

Document in README or `DEVELOPER_SETUP.md`.

### v2.5 pipeline constraints enforced

| Check                               | Enforces                                                |
| ----------------------------------- | ------------------------------------------------------- |
| No cross-phase imports              | Stage isolation — phases talk through orchestrator only |
| Voice pass no facts query           | Rephrase-only constraint                                |
| Factual pass no voice query         | Facts phase purity                                      |
| No `character_full.md` in pipeline  | Character file refactor compliance                      |
| Handoff writes JSON                 | Scores must be persisted, not just printed              |
| No direct Ollama calls in critiques | Forces pipeline abstraction                             |
| No zip files                        | Repo hygiene                                            |

### Hook as living architecture documentation

Each hook encodes the design constraints of its phase. Reading the hook tells you the rules. When constraints change, the hook changes — the commit history shows when and why a constraint was added or relaxed.

---

## Scaffold order — feature/v2.5-async-pipeline

1. **Move existing hook to `.githooks/`** — migrate v2.0 hook to tracked directory, add dispatcher, document setup
2. **`.githooks/pre-commit-v2.5-pipeline`** — new hook with pipeline constraints
3. **`backend/character/` split** — refactor character file into role-specific files
4. **`backend/services/pipeline/` stub files** — orchestrator + all phase stubs with docstrings and pass
5. **`DEVELOPER_SETUP.md`** — hook setup, venv setup, corpus seeding, environment health check
6. **Moderation layer** — blocklist integration + intent classifier + human review queue
7. **Factual pass** — wire `llama3.1:8b` + `rag_facts`, constrained to `character_facts.md`
8. **Fact + safety critique** — `mistral:latest`, accuracy + safety scoring
9. **Voice pass** — wire `qwen2.5:7B` + `rag_full`, rephrase-only constraint
10. **Semantic fidelity check** — diff fact vs voice artifact, flag material changes
11. **Character fidelity check** — regurgitation, structure, anti-formulaic scoring
12. **Editorial pass** — annotation model, red-squiggly flag format
13. **Editorial critique** — Flesch-Kincaid (deterministic) + LLM clarity scoring
14. **Handoff formatter** — before/after drafts, all scores, editorial flags, human score fields
15. **Judge calibration** — feed canonical Q&A pairs, confirm 10/10 before scoring ships
16. **Human review UI** — accept/reject editorial flags, score entry, delta tracking
