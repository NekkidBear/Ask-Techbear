# TechBear — Editorial Standards

Gymnarctos Studios LLC

---

This file governs the editorial annotation layer. It is used by the editorial pass and editorial critique phases only.
The editorial pass annotates — it does not auto-correct. The writer (Jason) has final say on all flags.

## The "Red Squiggly" Model

This phase flags anomalies as potential errors OR intentional voice choices.
It does not rewrite. It does not correct. It surfaces decisions for human review.

Flag format:

```text
FLAG: <location> | <type: possible_error | intentional_voice> | <reason>
```

## Intentional Grammar Markers (Do Not Flag as Errors)

These are established TechBear voice features and should be classified as `intentional_voice` if flagged at all:

- Sentence fragments used for punch ("Run. Now. Immediately.")
- Comma splices used for rhythm ("You clicked it, didn't you, sugar.")
- ALL CAPS single words for emphasis ("your system is BEGGING for mercy")
- Em-dashes for dramatic pause ("Honey — and I say this with love — what were you thinking.")
- Starting sentences with "And" or "But" for cadence
- Invented compound words or portmanteaus that land as jokes
- Rhetorical questions used as structural beats
- Second-person address mid-answer ("Here's what YOU'RE going to do")

## Error Patterns (Flag as `possible_error`)

- Homophones used incorrectly (their/there/they're, its/it's, your/you're) where context clearly indicates error
- Technical terminology misspelled in a way that changes meaning (e.g., "rouge" server instead of "rogue")
- A factual claim that appears to contradict the factual draft (flag for human review, do not correct)
- A step in a procedure that appears to be in the wrong order
- A number or count inconsistency ("three steps" followed by four bullets)
- Unclosed parenthetical or quotation within a sentence

## Readability Targets

- **Flesch-Kincaid Reading Ease**: Target 60–80 (conversational, accessible). Below 50 flags for "too dense."
- **Sentence length**: Flag any sentence over 35 words as a potential pacing issue for live reading.
- **Paragraph length**: Flag any paragraph over 4 sentences for live readability.
- **Word count**: Flag if response is outside the target range for the generation mode:
  - Live mode: 150–250 words
  - Batch mode: 250–500 words
  - Article mode: 500+ words
  - Default to live mode targets if mode is not specified.

## Formatting Compliance

- No markdown headers in final response (this is a spoken-word piece, not a document)
- No numbered lists (convert to flowing prose or TechBear's characteristic rhetorical structures)
- Em-dashes and ALL CAPS are permitted per voice standards
- Parenthetical asides are permitted but flag if more than 2 in one response

## The Draft to Review

"""
{VOICE_DRAFT}
"""

## Factual Draft Reference

(Use this to check for claim consistency. Do not correct voice choices.)

"""
{FACTUAL_DRAFT}
"""
