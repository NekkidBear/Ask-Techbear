"""
TechBear Async Pipeline — json_utils.py

Shared JSON parsing and recovery utilities for pipeline phases.

All LLM-facing phases import from here rather than maintaining local
parse variants. Consistent recovery behavior across moderation,
fact_critique, character_critique, and editorial phases.
"""

import json


def extract_first_json_object(raw: str) -> str:
    """
    Extract the first complete JSON object from an LLM response.

    Tolerates cases where the model returns valid JSON followed by
    extra commentary, while avoiding false brace matches inside strings.

    Args:
        raw: Raw string output from an LLM response.

    Returns:
        The first complete JSON object as a string.

    Raises:
        ValueError: If no JSON object is found or the object is unclosed.
    """
    raw = raw.strip()
    start = raw.find("{")

    if start == -1:
        raise ValueError("No JSON object found in LLM response")

    depth = 0
    in_string = False
    escape = False

    for i, ch in enumerate(raw[start:], start=start):
        if escape:
            escape = False
            continue

        if ch == "\\":
            escape = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1

            if depth == 0:
                return raw[start:i + 1]

    raise ValueError("Unclosed JSON object in LLM response")


def parse_llm_json(raw: str) -> dict:
    """
    Parse LLM JSON output, repairing common response formatting failures.

    Handles two failure modes seen across pipeline models:
      1. Model wraps JSON in markdown fences (```json ... ```)
      2. Model appends commentary after a valid JSON object

    Strips fences first, then attempts direct parse. Falls back to
    extract_first_json_object on JSONDecodeError to handle trailing
    commentary without losing the valid JSON payload.

    Args:
        raw: Raw string output from an LLM response.

    Returns:
        Parsed dict from the LLM response.

    Raises:
        ValueError: If no valid JSON object can be extracted.
        json.JSONDecodeError: If extracted content cannot be parsed.
    """
    raw = raw.strip()

    if raw.startswith("```"):
        raw = "\n".join(
            line for line in raw.splitlines()
            if not line.strip().startswith("```")
        ).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        extracted = extract_first_json_object(raw)
        return json.loads(extracted)
