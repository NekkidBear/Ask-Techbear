"""
Core inference and experimental pipeline for TechBear benchmarking system.

This module contains all model interaction logic, including:
- LLM inference via Ollama
- Prompt construction
- Optional (feature-gated) experimental components:
  - Scope classification
  - Retrieval-augmented generation (RAG)
  - LLM-as-judge evaluation

Design principles:
- v1 baseline is pure model inference (no RAG, no eval, no classification)
- Feature flags control progressive enhancement
- Strict separation between prompt building and inference execution
"""

import time
import requests

OLLAMA_PORT = 11434


# =========================================================
# FEATURE FLAGS (disabled by default for v1)
# =========================================================

SCOPE_ENABLED = False
EVAL_ENABLED = False
RAG_ENABLED = False


# =========================================================
# CORE INFERENCE LAYER
# =========================================================

def ollama_generate(host, model, prompt, system=""):
    """
    Send a prompt to an Ollama model and return the response with metrics.

    This is the only function responsible for model inference.

    Args:
        host (str): Ollama host address.
        model (str): Model name (e.g., llama3.1:8b).
        prompt (str): Final formatted prompt string.
        system (str): System prompt used for behavior conditioning.

    Returns:
        dict: Model response and performance metrics.
    """
    url = f"http://{host}:{OLLAMA_PORT}/api/generate"

    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False
    }

    t0 = time.perf_counter()
    r = requests.post(url, json=payload, timeout=300)
    t1 = time.perf_counter()

    r.raise_for_status()
    data = r.json()

    return {
        "response": data.get("response", ""),
        "total_time_s": round(t1 - t0, 3),
        "tokens_generated": data.get("eval_count", 0)
    }


# =========================================================
# PROMPT CONSTRUCTION LAYER
# =========================================================

def build_prompt(question, rag_context=None):
    """
    Build final prompt sent to the model.

    v1 behavior:
        - returns raw question only

    future behavior (if RAG_ENABLED):
        - injects retrieved context

    Args:
        question (str): User question
        rag_context (str | None): Retrieved context (optional)

    Returns:
        str: formatted prompt
    """
    if RAG_ENABLED and rag_context:
        return f"CONTEXT:\n{rag_context}\n\nQUESTION:\n{question}"

    return f"QUESTION:\n{question}"


# =========================================================
# FEATURE: SCOPE CLASSIFICATION (disabled in v1)
# =========================================================

def classify_scope(*_args, **_kwargs):
    """
    Placeholder for future phase (scope classification).

    Disabled in v1 benchmark runs.
    """
    if not SCOPE_ENABLED:
        return {"scope": "DISABLED", "reasoning": "scope disabled in v1"}

    return {}


# =========================================================
# FEATURE: LLM EVALUATION (disabled in v1)
# =========================================================

def evaluate_response(*_args, **_kwargs):
    """
    Placeholder for LLM-as-judge evaluation system.

    Disabled in v1 benchmark runs.
    """
    if not EVAL_ENABLED:
        return {
            "voice_adherence": None,
            "technical_accuracy": None,
            "note": "evaluation disabled in v1"
        }

    return {}


# =========================================================
# SYSTEM PROMPT (TechBear character)
# =========================================================

TECHBEAR_SYSTEM = """
You are TechBear, a friendly IT generalist and helpdesk specialist...
(keep your full system prompt here unchanged)
""".strip()
