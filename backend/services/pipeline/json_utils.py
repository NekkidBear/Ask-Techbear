"""
TechBear Async Pipeline — json_utils.py

Shared JSON parsing and recovery utilities for pipeline phases.

All LLM-facing phases import from here rather than maintaining local
parse variants. Consistent recovery behavior across moderation,
fact_critique, character_critique, and editorial phases.

Parse telemetry (item 12):
    parse_llm_json_with_telemetry() returns both the parsed dict and a
    telemetry dict recording whether parsing succeeded, required repair,
    or failed. Phases store this in artifact["diagnostics"] for calibration.
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


def parse_llm_json_with_telemetry(raw: str) -> tuple[dict, dict]:
    """
    Parse LLM JSON output and return parse telemetry alongside the result.

    Item 12: structured parse telemetry for pipeline calibration.
    Phases store the telemetry dict in artifact["diagnostics"] so that
    parse failure rates can be tracked per model over time.

    Telemetry dict shape:
        {
            "parse_success": bool,      # True if any parse succeeded
            "parse_repaired": bool,     # True if fence-strip or extract was needed
            "parse_failed": bool,       # True if all attempts failed
            "repair_method": str | None # "fence_strip" | "extract_object" |
                                        # "fence_strip_and_extract" | None
        }

    Args:
        raw: Raw string output from an LLM response.

    Returns:
        (parsed_dict, telemetry_dict)

    Raises:
        ValueError: If no valid JSON object can be extracted.
        json.JSONDecodeError: If extracted content cannot be parsed.
    """
    telemetry: dict = {
        "parse_success": False,
        "parse_repaired": False,
        "parse_failed": False,
        "repair_method": None,
    }

    stripped = raw.strip()
    fence_stripped = False

    # Attempt 1: strip fences if present
    if stripped.startswith("```"):
        stripped = "\n".join(
            line for line in stripped.splitlines()
            if not line.strip().startswith("```")
        ).strip()
        fence_stripped = True

    # Attempt 2: direct parse
    try:
        result = json.loads(stripped)
        telemetry["parse_success"] = True
        if fence_stripped:
            telemetry["parse_repaired"] = True
            telemetry["repair_method"] = "fence_strip"
        return result, telemetry
    except json.JSONDecodeError:
        pass

    # Attempt 3: extract first JSON object (handles trailing commentary)
    try:
        extracted = extract_first_json_object(stripped)
        result = json.loads(extracted)
        telemetry["parse_success"] = True
        telemetry["parse_repaired"] = True
        telemetry["repair_method"] = (
            "fence_strip_and_extract" if fence_stripped else "extract_object"
        )
        return result, telemetry
    except (ValueError, json.JSONDecodeError) as exc:
        telemetry["parse_failed"] = True
        raise exc
