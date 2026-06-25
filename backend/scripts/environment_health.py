"""
TechBear Environment Health Check

Benchmark preflight verifier. Checks corpus population, embedding
model availability, and character file integrity before a benchmark
run. Application-level checks (Postgres, backend, frontend) are
handled by AskTB.sh.
"""

import sys
import requests
from chromadb import errors as chromadb_errors

from backend.scripts.character_loader import load_character_prompt
from backend.services.rag.retriever import TechBearRetriever


OLLAMA_URL = "http://localhost:11434"
REQUIRED_MODELS = ["llama3.1:8b", "nomic-embed-text"]

MIN_FACTS = 10
MIN_VOICE = 5

PASS = "✅"
WARN = "⚠️ "
FAIL = "❌"


# =========================================================
# INDIVIDUAL CHECKS
# =========================================================

def check_ollama() -> bool:
    """Verify Ollama is running and required models are available."""
    print("Checking Ollama...")

    try:
        r = requests.get(OLLAMA_URL, timeout=5)
        r.raise_for_status()
    except (requests.ConnectionError, requests.Timeout) as e:
        print(f"  {FAIL} Ollama not responding at {OLLAMA_URL}: {e}")
        return False

    print(f"  {PASS} Ollama is up.")

    # Check required models
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]

        all_present = True
        for m in REQUIRED_MODELS:
            if any(m in model for model in models):
                print(f"  {PASS} Model present: {m}")
            else:
                print(f"  {FAIL} Model missing: {m} — run: ollama pull {m}")
                all_present = False

        return all_present

    except (requests.ConnectionError, requests.JSONDecodeError) as e:
        print(f"  {WARN} Could not check model list: {e}")
        return False


def check_corpus() -> bool:
    """Verify ChromaDB collections are populated above minimum thresholds."""
    print("Checking corpus...")

    try:
        retriever = TechBearRetriever()
        counts = retriever.collection_counts()

        facts_count = counts["techbear_facts"]
        voice_count = counts["techbear_voice"]

        facts_ok = facts_count >= MIN_FACTS
        voice_ok = voice_count >= MIN_VOICE

        if facts_ok:
            print(f"  {PASS} techbear_facts: {facts_count} documents")
        else:
            print(
                f"  {FAIL} techbear_facts: {facts_count} documents "
                f"(minimum {MIN_FACTS}) — run ingest_corpus.py"
            )

        if voice_ok:
            print(f"  {PASS} techbear_voice: {voice_count} documents")
        else:
            print(
                f"  {FAIL} techbear_voice: {voice_count} documents "
                f"(minimum {MIN_VOICE}) — run ingest_corpus.py"
            )

        return facts_ok and voice_ok

    except (chromadb_errors.ChromaError, chromadb_errors.NotFoundError, ValueError) as e:
        print(f"  {FAIL} Could not connect to ChromaDB: {e}")
        return False


def check_character() -> bool:
    """Verify character file loads without error."""
    print("Checking character file...")

    try:
        text = load_character_prompt()

        if not text or not text.strip():
            print(f"  {FAIL} Character file loaded but is empty.")
            return False

        word_count = len(text.split())
        print(f"  {PASS} Character file loaded ({word_count} words).")
        return True

    except (FileNotFoundError, IOError, OSError) as e:
        print(f"  {FAIL} Character file failed to load: {e}")
        return False


# =========================================================
# ORCHESTRATOR
# =========================================================

def run_all_checks(exit_on_fail: bool = False) -> bool:
    """
    Run all environment checks and report results.

    Returns True if all checks pass. If exit_on_fail is True,
    calls sys.exit(1) on any failure — useful for CLI use.
    """
    print("\n── TechBear Environment Health Check ──────────────\n")

    results = {
        "ollama": check_ollama(),
        "corpus": check_corpus(),
        "character": check_character(),
    }

    print("\n── Summary ─────────────────────────────────────────")
    all_pass = all(results.values())

    for name, passed in results.items():
        status = PASS if passed else FAIL
        print(f"  {status} {name}")

    if all_pass:
        print(f"\n{PASS} All checks passed. Ready to benchmark.\n")
    else:
        print(
            f"\n{FAIL} One or more checks failed. Fix issues before benchmarking.\n")
        if exit_on_fail:
            sys.exit(1)

    return all_pass


# =========================================================
# CLI
# =========================================================

if __name__ == "__main__":
    run_all_checks(exit_on_fail=True)
