"""
TechBear Async Pipeline — handoff.py

Human review handoff formatter.
Assembles complete review package:
  - before draft (factual artifact)
  - after draft (editorial artifact)
  - all automated scores per phase
  - editorial flags with accept/reject fields
  - human score fields (same rubric, 0-10)
  - delta fields: automated vs human per dimension
Output: JSON written to disk for human review UI.
"""

import json
from pathlib import Path

HANDOFF_DIR = Path("handoff_output")


def run(artifact: dict) -> dict:
    """
    Execute this pipeline phase.

    Args:
        artifact: incoming pipeline state dict

    Returns:
        updated pipeline state dict
    """
    HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
    output_path = HANDOFF_DIR / f"handoff_{artifact.get('submission_id', 'unknown')}.json"
    with open(output_path, "w") as f:
        json.dump(artifact, f, indent=2)
    raise NotImplementedError("Phase not yet implemented")
