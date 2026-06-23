"""
Core inference and experimental pipeline for TechBear benchmarking system.
"""

import time
import requests

OLLAMA_PORT = 11434


# =========================================================
# SYSTEM PROMPT
# =========================================================

TECHBEAR_SYSTEM = """
You are TechBear, a friendly IT generalist and helpdesk specialist.

Rules:
- Be technically accurate
- Do not hallucinate missing steps
- Use provided context when available
""".strip()


# =========================================================
# CORE INFERENCE
# =========================================================

def ollama_generate(host, model, prompt, system=""):
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
# PROMPT BUILDER (MODE-DRIVEN)
# =========================================================

def build_prompt(question, rag_context=None, voice_context=None, mode="raw"):

    if mode == "raw":
        return f"QUESTION:\n{question}"

    if mode == "prompt_only":
        return f"{TECHBEAR_SYSTEM}\n\nQUESTION:\n{question}"

    if mode == "rag_facts":
        return f"""
{TECHBEAR_SYSTEM}

FACTUAL CONTEXT:
{rag_context or ""}

QUESTION:
{question}
""".strip()

    if mode == "rag_full":
        return f"""
{TECHBEAR_SYSTEM}

FACTUAL CONTEXT:
{rag_context or ""}

VOICE EXAMPLES:
{voice_context or ""}

QUESTION:
{question}
""".strip()

    raise ValueError(f"Unknown mode: {mode}")


# =========================================================
# PIPELINE ENTRY POINT
# =========================================================

def run_pipeline(question, model, host, mode="raw", retriever=None):

    rag_context = None
    voice_context = None

    if mode in ["rag_facts", "rag_full"] and retriever:
        rag_context = retriever.get_facts(question)

    if mode == "rag_full" and retriever:
        voice_context = retriever.get_voice(question)

    prompt = build_prompt(
        question,
        rag_context=rag_context,
        voice_context=voice_context,
        mode=mode
    )

    return ollama_generate(
        host=host,
        model=model,
        prompt=prompt,
        system=""
    )
