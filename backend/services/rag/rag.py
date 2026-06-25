"""
backend/services/rag/rag.py — ChromaDB retrieval for Ask TechBear
Gymnarctos Studios LLC

Dual-collection RAG:
  techbear_facts — factual content, fiction posts excluded via metadata filter
  techbear_voice — voice/style exemplars, all posts including Multiverse episodes
"""

from typing import Dict, List

import chromadb
import requests

CHROMA_PATH = "./chroma_db"

FACTS_COLLECTION = "techbear_facts"
VOICE_COLLECTION = "techbear_voice"

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "llama3.1:8b"


# ============================================================
# DB CLIENT
# ============================================================

client = chromadb.PersistentClient(path=CHROMA_PATH)

facts_col = client.get_collection(FACTS_COLLECTION)
voice_col = client.get_collection(VOICE_COLLECTION)


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


def _pack(results) -> List[Dict]:
    """Pack ChromaDB query results into a list of text/meta dicts."""
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    return [{"text": doc, "meta": meta} for doc, meta in zip(docs, metas)]


# ============================================================
# CONTEXT BUILDING
# ============================================================

def build_prompt(user_query: str, facts: List[Dict], voice: List[Dict]) -> List[Dict]:
    """Build a structured chat prompt for Ollama from retrieved chunks."""
    fact_block = "\n\n".join(
        f"[SOURCE {i+1}]\n{f['text']}"
        for i, f in enumerate(facts)
    )

    voice_block = "\n\n".join(
        f"[VOICE EXAMPLE {i+1}]\n{v['text']}"
        for i, v in enumerate(voice)
    )

    system = (
        "You are TechBear.\n"
        "You are technically precise, but expressive and stylized.\n\n"
        "RULES:\n"
        "- Facts come ONLY from SOURCES.\n"
        "- Voice examples are style only; do NOT treat them as factual.\n"
        "- Never invent technical steps not supported by SOURCES.\n"
        "- Match tone and phrasing from VOICE EXAMPLES loosely, not literally.\n"
    )

    user = (
        f"QUESTION:\n{user_query}\n\n"
        f"FACTUAL SOURCES:\n{fact_block}\n\n"
        f"VOICE EXAMPLES:\n{voice_block}\n"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ============================================================
# GENERATION
# ============================================================

def generate(user_query: str, model: str = DEFAULT_MODEL) -> str:
    """Generate a TechBear response using RAG retrieval and Ollama."""
    facts = retrieve_facts(user_query)
    voice = retrieve_voice(user_query)
    messages = build_prompt(user_query, facts, voice)

    response = requests.post(
        OLLAMA_URL,
        json={"model": model, "messages": messages, "stream": False},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]
