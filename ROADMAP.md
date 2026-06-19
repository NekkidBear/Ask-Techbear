# Ask TechBear — Roadmap / Future Ideas

## v2 Ideas

### Adaptive moderation

The blocklist/moderation system should learn from operator overrides over time:

- If a question was auto-rejected (blocklist or LLM topic filter) but the operator
  approves it anyway → treat as a false positive signal, consider loosening that
  term's fuzzy match threshold or removing it
- If a question passed moderation but the operator manually rejects it → treat as
  a false negative signal, consider adding the relevant term/pattern to the blocklist
- Could be implemented as a simple "moderation override" log table that an admin
  view surfaces periodically for review, rather than fully automatic adjustment
  (fully automatic risks drift in either direction without human sanity-check)

### Topic duplication detection

Avoid 3+ near-identical questions ("my wifi is broken") cluttering a single
session. Lives alongside the RAG/embedding work below rather than the
blocklist, since this is a semantic-similarity problem, not a pattern-match
problem. Once a session-history embedding store exists (see pipeline below),
a new question's embedding could be compared against this session's
already-answered questions; if similarity is high, surface that to the
operator dashboard ("this looks similar to Q#4 — reuse that answer, or note
as a fresh take?") rather than auto-merging, since operator judgment matters
here — a near-duplicate phrasing might still deserve its own performed
answer for a different attendee.

### Two-stage generation pipeline with dual RAG (voice/fact separation)

**The problem this solves:** the current single-prompt design asks one model
(llama3.2) to simultaneously be factually accurate, in-voice (including
TechBear's characteristic vocabulary, metaphor style, and humor — not just
the personality *rules*, but the actual texture of how he talks), length-
constrained, and content-boundary-aware, all in one generation call. That's
a lot of competing objectives for one inference pass, and prompt engineering
for voice can fight prompt engineering for accuracy within the same context.

**The proposed architecture — two generation stages, each with its own RAG
source, doing genuinely different jobs:**

- **Stage 1 — fact draft.** Sanitized question + RAG retrieval against the
  existing technical knowledge base (TechBear's published articles on device
  maintenance, cybersecurity, WiFi, backup strategy, small business IT —
  i.e. today's `RAG_CONTEXT`). Output: a terse, plain-language, no-character
  answer. No sass, no metaphor, just "is this correct." Possibly reuse
  llama3.2:1b (already running for moderation) to avoid adding a third model
  and keep local resource usage down — worth benchmarking whether the 1b
  model is accurate enough for this job before committing to it over the
  full 3b model.

- **Stage 2 — voice rewrite.** Plain answer from stage 1, run through a
  *second* RAG retrieval — this time against a corpus of Jason's exported
  TechBear blog posts, not the technical knowledge base. Jason confirmed the
  blog posts are written in TechBear's persona throughout (not Jason's own
  essay-explaining voice with TechBear as a branding wrapper), so the corpus
  doesn't need a voice-labeling/curation pass to separate registers — it's
  already consistent. The retrieved passages serve as live few-shot examples
  of TechBear's actual phrasing, recurring terms, and metaphor style, which
  should transfer to a small local model far more reliably than asking it to
  interpret named personality references (Paul Lynde, Dolly Parton, etc.) in
  the abstract — see `voice_adjectives.md` for the adjective-based fallback/
  supplement to those references either way.

  One tuning wrinkle to watch for: blog-post TechBear can sustain a longer
  bit or a paragraph-length metaphor in writing, while live-tabling TechBear
  needs to be tighter and faster-cut (current character file already
  constrains drafts to 150–250 words and favors short punchy sentences for
  the same reason). Even within an all-TechBear corpus, retrieval should
  probably favor his shorter/punchier passages (zingers, one-liners, tight
  metaphors) over his longer discursive stretches, since those compress
  better into a live-read draft. May need a length filter or chunk-size
  tuning on the blog corpus rather than retrieving arbitrary-length passages.

  Stage 2 also applies the static character rules (structure, content
  boundaries, refusal pattern, the profanity-scold behavior) on top of the
  retrieved voice examples — these don't need their own RAG source, they're
  fixed instructions, same as today's character file.

**Where Conversation History / running gags / audience context fit:**
Rolling session context (today's `SessionContext` table / `ROLLING_CONTEXT`)
most naturally informs stage 2, since it's about consistency of delivery
across a session, not factual correctness. Running gags (a bit reused or
escalated across a session) would need actual session state — likely a new
column/table tracking which gags have been used and how often, closer to a
session-state feature than a corpus or a file. Audience context (family-
friendly vs. mature-skewing crowd, already partially present via the
"doggoneit" vs ad-libbed "dammit" distinction in the refusal pattern) is
probably just a single setting threaded into stage 2's prompt — e.g.
`Session.audience_mode` — not a separate retrieval source. Worth deferring
detailed design on running gags specifically until after a live event, since
it's hard to know what shape "gags across a session" should take without
having run one.

**Honest costs, not just upside, before building this:**

- Roughly doubles inference calls per question. Latency matters live, on
  local hardware (Mac, Ollama), with an audience present and Jason
  performing in real time — worth actually timing stage 1 + stage 2
  end-to-end before assuming it's fast enough, not just assuming it scales.
- Doubles the failure surface — two prompts that can each go wrong
  independently (stage 1 hallucinates a fact; stage 2 garbles the voice or
  drops the rewrite entirely) instead of one. Means dashboard review may
  need to show both stages' output, not just the final draft, so Jason can
  tell which stage failed if something looks off.
- Needs the blog corpus actually chunked and embedded (ChromaDB +
  nomic-embed-text, already planned for the technical knowledge base) before
  any of this works — this is real setup work, not a prompt change.
- Multi-query RAG (decomposing a complex question into sub-queries against
  the knowledge base before stage 1 synthesis — e.g. a WiFi question
  separately retrieving "router troubleshooting" and "ISP vs in-home
  network" chunks) is a natural pairing with this pipeline, and lower-risk
  to add since it's pure retrieval tuning, not a new generation stage. Could
  potentially be tried independently, before the full two-stage split.

**Decision:** ship the simpler single-prompt design for the 6/20 event, treat
this as the detailed v2 plan rather than attempt it under time pressure.
Prototype and time it only after a live event has actually been run.

(Add more v2 ideas here as they come up — voice output, pre-scripted response
triggers, corpus auto-update webhook, analytics dashboard, multi-event archive,
Philip co-host mode — see original system design doc for the fuller v2 list.)

## additional v2 Ideas

### Summarize and format for presentation view

Auto-generate the `presentation_versions` display text via LLM rather than
hand-seeding. Natural fit as a stage 3 pass once the two-stage RAG pipeline
exists, or as a flag on stage 2 output.

### Multi-stage RAG pipeline (voice/fact separation)

See existing detailed design notes below.

### Realtime response loop — personal Q&A feedback

Even if a question isn't highlighted, the submitter sees TechBear's response
to their own question. Requires issuing an anonymous session token at
submission time so the frontend can poll or subscribe to the right response.
FastAPI SSE or WebSocket is the natural transport. Identity/token design is
the prerequisite — start there before touching the delivery layer.

## TODO: Fix the favicon display image

  Favicon is displaying the default browser globe instead of the custom Gymnarctos pawprint, despite the renamed image.
