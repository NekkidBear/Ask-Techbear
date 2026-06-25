"""
backend/services/rag/rag.py — ChromaDB retrieval for Ask TechBear
Gymnarctos Studios LLC

Three-collection RAG:
  techbear_facts — factual technical content, fiction and bio excluded
  techbear_voice — voice/style exemplars, all posts including Multiverse
  techbear_lore  — TechBear canon (Multiverse) and tall tale callbacks

Retrieval modes:
  factual  — facts + voice (technical questions)
  lore     — lore + voice (canon/character questions e.g. "have you met Janeway?")
  hybrid   — facts + lore + voice (questions mixing technical and lore)
  tall_tale — lore (tall_tale tier only) + voice (TechBear background questions)
"""

from typing import Dict, List

import chromadb
import requests

CHROMA_PATH = "./chroma_db"

FACTS_COLLECTION = "techbear_facts"
VOICE_COLLECTION = "techbear_voice"
LORE_COLLECTION = "techbear_lore"

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "llama3.1:8b"


# ============================================================
# DB CLIENT
# ============================================================

client = chromadb.PersistentClient(path=CHROMA_PATH)

facts_col = client.get_collection(FACTS_COLLECTION)
voice_col = client.get_collection(VOICE_COLLECTION)
lore_col = client.get_collection(LORE_COLLECTION)


# ============================================================
# RETRIEVAL
# ============================================================

def retrieve_facts(query: str, k: int = 6) -> List[Dict]:
    """Retrieve factual chunks, excluding fiction/lore posts."""
    results = facts_col.query(
        query_texts=[query],
        n_results=k,
        where={"is_fiction": False},
    )
    return _pack(results)


def retrieve_voice(query: str, k: int = 4) -> List[Dict]:
    """Retrieve voice/style exemplar chunks including Multiverse episodes."""
    results = voice_col.query(
        query_texts=[query],
        n_results=k,
    )
    return _pack(results)


def retrieve_lore(
    query: str,
    k: int = 6,
    lore_tier: str | None = None,
) -> List[Dict]:
    """
    Retrieve lore chunks from the canon and tall tale collection.

    Args:
        query: the question text
        k: number of chunks to retrieve
        lore_tier: optional filter — "canon" | "tall_tale" | None (both)

    Returns:
        List of chunk dicts with text and metadata.
    """
    where = {}
    if lore_tier is not None:
        where = {"lore_tier": lore_tier}

    results = lore_col.query(
        query_texts=[query],
        n_results=k,
        where=where if where else None,
    )
    return _pack(results)


def retrieve_for_mode(
    query: str,
    retrieval_mode: str = "factual",
) -> Dict[str, List[Dict]]:
    """
    Route retrieval based on question type.

    Returns a dict with keys: facts, voice, lore
    Callers use whichever keys are relevant to their phase.

    Modes:
        factual   — technical questions (default)
        lore      — canon/character questions
        hybrid    — technical + lore mixed
        tall_tale — TechBear background/origin questions
    """
    voice = retrieve_voice(query)

    if retrieval_mode == "lore":
        return {
            "facts": [],
            "voice": voice,
            "lore": retrieve_lore(query, k=6),
        }

    if retrieval_mode == "tall_tale":
        return {
            "facts": [],
            "voice": voice,
            "lore": retrieve_lore(query, k=6, lore_tier="tall_tale"),
        }

    if retrieval_mode == "hybrid":
        return {
            "facts": retrieve_facts(query, k=4),
            "voice": voice,
            "lore": retrieve_lore(query, k=4),
        }

    # Default: factual
    return {
        "facts": retrieve_facts(query, k=6),
        "voice": voice,
        "lore": [],
    }


def _pack(results) -> List[Dict]:
    """Pack ChromaDB query results into a list of text/meta dicts."""
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    return [{"text": doc, "meta": meta} for doc, meta in zip(docs, metas)]


# ============================================================
# CONTEXT BUILDING
# ============================================================

def build_prompt(
    user_query: str,
    facts: List[Dict],
    voice: List[Dict],
    lore: List[Dict] | None = None,
) -> List[Dict]:
    """Build a structured chat prompt for Ollama from retrieved chunks."""
    fact_block = "\n\n".join(
        f"[SOURCE {i+1}]\n{f['text']}"
        for i, f in enumerate(facts)
    ) if facts else "(No factual sources retrieved.)"

    voice_block = "\n\n".join(
        f"[VOICE EXAMPLE {i+1}]\n{v['text']}"
        for i, v in enumerate(voice)
    ) if voice else "(No voice examples retrieved.)"

    lore_block = ""
    if lore:
        lore_block = "\n\nLORE CONTEXT (TechBear canon — use for character consistency):\n"
        lore_block += "\n\n".join(
            f"[LORE {i+1} | tier={c['meta'].get('lore_tier', 'unknown')}]\n{c['text']}"
            for i, c in enumerate(lore)
        )

    system = (
        "You are TechBear.\n"
        "You are technically precise, but expressive and stylized.\n\n"
        "RULES:\n"
        "- Facts come ONLY from SOURCES.\n"
        "- Lore context is TechBear canon — stay consistent with it.\n"
        "- Voice examples are style only; do NOT treat them as factual.\n"
        "- Never invent technical steps not supported by SOURCES.\n"
        "- Match tone and phrasing from VOICE EXAMPLES loosely, not literally.\n"
    )

    user = (
        f"QUESTION:\n{user_query}\n\n"
        f"FACTUAL SOURCES:\n{fact_block}\n\n"
        f"VOICE EXAMPLES:\n{voice_block}"
        + lore_block
        + "\n"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ============================================================
# GENERATION
# ============================================================

def generate(
    user_query: str,
    model: str = DEFAULT_MODEL,
    retrieval_mode: str = "factual",
) -> str:
    """Generate a TechBear response using routed RAG retrieval and Ollama."""
    chunks = retrieve_for_mode(user_query, retrieval_mode)
    messages = build_prompt(
        user_query,
        facts=chunks["facts"],
        voice=chunks["voice"],
        lore=chunks.get("lore", []),
    )

    response = requests.post(
        OLLAMA_URL,
        json={"model": model, "messages": messages, "stream": False},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]
