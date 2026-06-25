"""
Single source of truth for TechBear character definition.
All systems (benchmark, live, async) must import from here.
"""

from pathlib import Path

CHARACTER_PATH = Path(__file__).resolve().parents[1] / "character_file.md"


def load_character_prompt() -> str:
    """Load TechBear system prompt from markdown file."""
    if not CHARACTER_PATH.exists():
        raise FileNotFoundError(f"Character file missing: {CHARACTER_PATH}")

    return CHARACTER_PATH.read_text(encoding="utf-8")
