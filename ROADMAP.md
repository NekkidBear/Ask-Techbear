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

(Add more v2 ideas here as they come up — voice output, pre-scripted response
triggers, corpus auto-update webhook, analytics dashboard, multi-event archive,
Philip co-host mode — see original system design doc for the fuller v2 list.)
