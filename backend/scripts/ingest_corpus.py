"""
scripts/ingest_corpus.py — Corpus ingestion for Ask TechBear RAG
Gymnarctos Studios LLC

Ingests the WordPress blog post corpus into two ChromaDB collections:

  techbear_facts  — larger chunks, preserves full explanations and
                    step-by-step sequences.

  techbear_voice  — smaller chunks, targets zingers, metaphors, and
                    punchy passages.

Both collections use the same 71-post dataset (4 pre-pivot posts excluded).
"""

import argparse
import csv
import os
import re
import sys
import time
from pathlib import Path

import chromadb
import requests
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction


# =============================================================
# Configuration
# =============================================================

CORPUS_PATH = (
    Path(__file__).resolve().parent.parent / "corpus" / "Post_corpus (1).csv"
)
CHROMA_PATH = Path(__file__).resolve().parent.parent.parent / "chroma_db"

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text"

EXCLUDED_IDS = {725, 737, 1255, 1758}

FACTS_COLLECTION = "techbear_facts"
VOICE_COLLECTION = "techbear_voice"

FACTS_CHUNK_CHARS = 1800
FACTS_OVERLAP_CHARS = 200

VOICE_CHUNK_CHARS = 500
VOICE_OVERLAP_CHARS = 50

SERIES_PATTERNS = {
    "Maintenance Monday": r"maintenance monday",
    "Tech Tip Tuesday": r"tech tip tuesday",
    "Thoughtful Thursday": r"thoughtful thursday",
    "Workflow Wednesday": r"workflow wednesday",
    "Ask TechBear": r"ask tech ?bear",
    "Guide to the Multiverse": r"guide to the multiverse",
}

# Posts that are fiction/lore — excluded from facts retrieval, kept in voice.
# Detected by title since some Multiverse posts have empty tags.
FICTION_TITLE_PATTERNS = [
    r"guide to the multiverse",
    r"friday funday.*multiverse",
]

FICTION_TAG_MARKERS = [
    "TechBearsGuideToTheMultiverse",
    "GuideToTheMultiverse",
    "MultiverseAdventures",
]

VOICE_MARKERS = [
    r"\btechnocub", r"\bsugar\b", r"\bhoney\b", r"\bdarling\b",
    r"\bdiva\b", r"\bsequin", r"\bsass", r"\bbear\b",
    r"\bglam", r"doggoneit", r"precious",
]

VOICE_MIN_SCORE = 1


# =============================================================
# Text processing
# =============================================================

def strip_html(text: str) -> str:
    """Remove HTML and Gutenberg artifacts, normalize whitespace."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def detect_series(title: str) -> str:
    """Detect content series from title."""
    for name, pattern in SERIES_PATTERNS.items():
        if re.search(pattern, title, re.IGNORECASE):
            return name
    return "standalone"


def is_fiction_post(title: str, tags: str) -> bool:
    """
    Detect fiction/lore posts that should not be used as factual sources.
    Checks title patterns first (reliable even when tags are empty),
    then tag markers as a secondary signal.
    """
    for pattern in FICTION_TITLE_PATTERNS:
        if re.search(pattern, title, re.IGNORECASE):
            return True
    for marker in FICTION_TAG_MARKERS:
        if marker.lower() in tags.lower():
            return True
    return False


def voice_score(text: str) -> int:
    """Score chunk for voice-likeness."""
    return sum(
        1 for m in VOICE_MARKERS
        if re.search(m, text, re.IGNORECASE)
    )


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text into overlapping sentence-aware chunks."""
    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        if end >= len(text):
            chunk = text[start:].strip()
            if chunk:
                chunks.append(chunk)
            break

        search_start = start + int(chunk_size * 0.8)
        boundary = text.rfind(". ", search_start, end)

        if boundary != -1:
            end = boundary + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        start = end - overlap

    return chunks


# =============================================================
# Embedding
# =============================================================

def embed(texts: list[str]) -> list[list[float]]:
    """
    Embed text using Ollama.

    Uses batch endpoint if available, otherwise falls back to per-item calls.
    """
    try:
        r = requests.post(
            f"{OLLAMA_BASE_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": texts},
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
        if "embeddings" in data:
            return data["embeddings"]
    except requests.RequestException:
        pass

    embeddings = []
    for t in texts:
        r = requests.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": t},
            timeout=60,
        )
        r.raise_for_status()
        embeddings.append(r.json()["embedding"])

    return embeddings


# =============================================================
# Corpus loading
# =============================================================

def load_corpus() -> list[dict]:
    """Load and clean WordPress CSV corpus."""
    posts = []

    with open(CORPUS_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            post_id = int(row["ID"])

            if post_id in EXCLUDED_IDS:
                continue

            row["clean_content"] = strip_html(row["Content"])
            row["series"] = detect_series(row["Title"])
            row["is_fiction"] = is_fiction_post(
                row["Title"], row.get("Tags", ""))
            posts.append(row)

    return posts


# =============================================================
# Chroma setup (refactored)
# =============================================================

def init_chroma():
    """Initialize persistent ChromaDB client."""
    CHROMA_PATH.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(CHROMA_PATH))


def init_embedding_function():
    """Create Ollama embedding function wrapper."""
    return OllamaEmbeddingFunction(
        url=f"{OLLAMA_BASE_URL}/api/embeddings",
        model_name=EMBED_MODEL,
    )


def init_collections(client, embed_fn, force: bool):
    """Create or reset Chroma collections."""
    if force:
        for name in [FACTS_COLLECTION, VOICE_COLLECTION]:
            try:
                client.delete_collection(name)
                print(f"  Deleted existing collection: {name}")
            except (ValueError, RuntimeError) as e:
                # Chroma raises runtime errors inconsistently across versions
                # Safe to ignore "not found" cases during reset
                print(f"  Delete skipped for {name}: {e}")
        return None

    facts_col = client.get_or_create_collection(
        name=FACTS_COLLECTION,
        embedding_function=embed_fn,
        metadata={
            "description": "TechBear factual content",
            "embed_model": EMBED_MODEL,
            "chunk_chars": FACTS_CHUNK_CHARS,
        },
    )

    voice_col = client.get_or_create_collection(
        name=VOICE_COLLECTION,
        embedding_function=embed_fn,
        metadata={
            "description": "TechBear voice exemplars",
            "embed_model": EMBED_MODEL,
            "chunk_chars": VOICE_CHUNK_CHARS,
        },
    )

    return facts_col, voice_col


def already_populated(facts_col, voice_col) -> bool:
    """Check if collections already contain data."""
    if facts_col.count() > 0 or voice_col.count() > 0:
        print(
            f"\nCollections already populated "
            f"(facts={facts_col.count()}, voice={voice_col.count()})."
        )
        print("Run with --force to rebuild.")
        return True
    return False


# =============================================================
# Ingestion
# =============================================================

def ingest_collection(
    collection: chromadb.Collection,
    posts: list[dict],
    chunk_size: int,
    overlap: int,
    voice_filter: bool,
    label: str,
) -> None:
    """Chunk, embed, and ingest posts into a collection."""
    total = 0
    skipped = 0

    batch_ids, batch_docs, batch_metas = [], [], []
    batch_size = 50

    for post in posts:
        post_id = post["ID"]
        title = post["Title"]
        series = post["series"]
        date = post["Date"][:10]
        tags = post["Tags"][:500]
        content = post["clean_content"]

        chunks = chunk_text(content, chunk_size, overlap)

        for i, chunk in enumerate(chunks):
            score = voice_score(chunk)

            if voice_filter and score < VOICE_MIN_SCORE:
                skipped += 1
                continue

            batch_ids.append(f"{post_id}_chunk_{i}")
            batch_docs.append(chunk)
            batch_metas.append({
                "post_id": int(post_id),
                "title": title,
                "series": series,
                "date": date,
                "tags": tags,
                "voice_score": score,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "is_fiction": is_fiction_post(title, tags),
            })

            total += 1

            if len(batch_ids) >= batch_size:
                _flush_batch(collection, batch_ids,
                             batch_docs, batch_metas, label)
                batch_ids, batch_docs, batch_metas = [], [], []

    if batch_ids:
        _flush_batch(collection, batch_ids, batch_docs, batch_metas, label)

    print(
        f"  [{label}] Done — {total} chunks ingested"
        + (f", {skipped} skipped" if voice_filter else "")
    )


def _flush_batch(collection, ids, docs, metas, label: str) -> None:
    """Embed and upsert batch into ChromaDB."""
    print(f"  [{label}] Embedding batch of {len(ids)}...", end="", flush=True)

    t0 = time.perf_counter()
    embeddings = embed(docs)
    print(f" {time.perf_counter() - t0:.1f}s")

    collection.upsert(
        ids=ids,
        documents=docs,
        embeddings=embeddings,
        metadatas=metas,
    )


# =============================================================
# Main orchestration
# =============================================================

def main() -> None:
    """Ingest the WordPress corpus into ChromaDB facts and voice collections."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if not CORPUS_PATH.exists():
        print("Corpus not found.")
        sys.exit(1)

    client = init_chroma()
    print(f"ChromaDB at {CHROMA_PATH}")

    embed_fn = init_embedding_function()
    result = init_collections(client, embed_fn, args.force)

    if args.force:
        # --force deleted collections and returned None; re-init clean
        facts_col, voice_col = init_collections(client, embed_fn, force=False)
    else:
        facts_col, voice_col = result  # type: ignore[misc]

    if not args.force and already_populated(facts_col, voice_col):
        sys.exit(0)

    print("\nLoading corpus...")
    posts = load_corpus()
    print(f"Loaded {len(posts)} posts")

    print("\nIngesting facts...")
    ingest_collection(
        facts_col,
        posts,
        FACTS_CHUNK_CHARS,
        FACTS_OVERLAP_CHARS,
        voice_filter=False,
        label="facts",
    )

    print("\nIngesting voice...")
    ingest_collection(
        voice_col,
        posts,
        VOICE_CHUNK_CHARS,
        VOICE_OVERLAP_CHARS,
        voice_filter=True,
        label="voice",
    )

    print("\nDone.")
    print(f"{FACTS_COLLECTION}: {facts_col.count()}")
    print(f"{VOICE_COLLECTION}: {voice_col.count()}")


if __name__ == "__main__":
    main()
