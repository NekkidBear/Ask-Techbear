"""
TechBear Async Pipeline — factual_pass.py

Factual generation pass.
Model: llama3.1:8b + rag_facts (techbear_facts collection only).
Character context: character_facts.md only — no voice instructions.
Constraint: technical accuracy over performance. No character voice.
Output: plain-text fact artifact.
"""


def run(artifact: dict) -> dict:
    """
    Execute this pipeline phase.

    Args:
        artifact: incoming pipeline state dict

    Returns:
        updated pipeline state dict
    """
    raise NotImplementedError("Phase not yet implemented")
