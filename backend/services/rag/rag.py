# backend/services/rag.py

import chromadb
import requests
from typing import List, Dict, Any

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
    results = facts_col.query(
        query_texts=[query],
        n_results=k
    )

    return _pack(results)


def retrieve_voice(query: str, k: int = 4) -> List[Dict]:
    results = voice_col.query(
        query_texts=[query],
        n_results=k
    )

    return _pack(results)


def _pack(results) -> List[Dict]:
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]

    packed = []
    for doc, meta in zip(docs, metas):
        packed.append({
            "text": doc,
            "meta": meta
        })
    return packed


# ============================================================
# CONTEXT BUILDING
# ============================================================

def build_prompt(user_query: str, facts: List[Dict], voice: List[Dict]) -> List[Dict]:
    """
    Builds a structured chat prompt for Ollama.
    """

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

    user = f"""
QUESTION:
{user_query}

FACTUAL SOURCES:
{fact_block}

VOICE EXAMPLES:
{voice_block}
"""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ============================================================
# GENERATION
# ============================================================

def generate(user_query: str, model: str = DEFAULT_MODEL) -> str:
    facts = retrieve_facts(user_query)
    voice = retrieve_voice(user_query)

    messages = build_prompt(user_query, facts, voice)

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": model,
            "messages": messages,
            "stream": False
        },
        timeout=120
    )

    response.raise_for_status()
    return response.json()["message"]["content"]