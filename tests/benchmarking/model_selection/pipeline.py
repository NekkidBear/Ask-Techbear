"""
Core inference pipeline for TechBear benchmarking system.

Design principle:
- NO personality prompts here
- NO character logic here
- ONLY orchestration of inputs -> model -> output
"""
# Standard library imports
import time
from typing import Any

# Third-party imports
import requests

OLLAMA_PORT = 11434


# =========================================================
# CORE INFERENCE
# =========================================================

def ollama_generate(host: str, model: str, prompt: str) -> dict[str, Any]:
    """
    Call Ollama /api/generate and return raw model output + metrics.
    """

    url = f"http://{host}:{OLLAMA_PORT}/api/generate"

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }

    t0 = time.perf_counter()
    r = requests.post(url, json=payload, timeout=300)
    t1 = time.perf_counter()

    r.raise_for_status()
    data = r.json()

    return {
        "response": data.get("response", ""),
        "total_time_s": round(t1 - t0, 3),
        "tokens_generated": data.get("eval_count", 0),
    }


# =========================================================
# PIPELINE ENTRY POINT
# =========================================================

def run_pipeline(
    question: str,
    model: str,
    host: str,
    prompt_builder,
) -> dict[str, Any]:
    """
    Fully decoupled pipeline:
    - prompt_builder is injected (critical design change)
    - allows benchmarking different character / RAG / critique setups
    """

    prompt = prompt_builder(question)

    return ollama_generate(
        host=host,
        model=model,
        prompt=prompt,
    )
