"""
scripts/ingest_corpus.py — Corpus ingestion for Ask TechBear RAG
Gymnarctos Studios LLC

Ingests the WordPress blog post corpus into two ChromaDB collections:

  techbear_facts  — larger chunks, preserves full explanations and
                    step-by-step sequences. Used by Stage 1 (fact draft)
                    to ground technical accuracy.

  techbear_voice  — smaller chunks, targets zingers, metaphors, and
                    tight punchy passages. Used by Stage 2 (voice rewrite)
                    to give the model live examples of TechBear's register.

Both collections use the same 71-post dataset (the 4 pre-pivot
corporate-voice posts are excluded by ID at load time).

Run with:
    python -m backend.scripts.ingest_corpus
    python -m backend.scripts.ingest_corpus --force   # re-embed from scratch

Prerequisites:
    pip install chromadb ollama
    ollama pull nomic-embed-text
"""

import argparse
import csv
import os
import re
import sys
import time
from pathlib import Path
# from xmlrpc import client

import chromadb
import requests
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction

# =============================================================
# Configuration
# =============================================================

CORPUS_PATH = Path(__file__).resolve().parent.parent / \
    "corpus" / "Post_corpus (1).csv"
CHROMA_PATH = Path(__file__).resolve().parent.parent.parent / "chroma_db"

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text"

# Pre-pivot corporate-voice posts — excluded from both collections
EXCLUDED_IDS = {725, 737, 1255, 1758}

# Collection names
FACTS_COLLECTION = "techbear_facts"
VOICE_COLLECTION = "techbear_voice"

# Chunking parameters
FACTS_CHUNK_CHARS = 1800    # ~450 tokens — preserves full explanations
FACTS_OVERLAP_CHARS = 200   # ~50 tokens overlap

VOICE_CHUNK_CHARS = 500     # ~125 tokens — tight punchy passages
VOICE_OVERLAP_CHARS = 50    # minimal overlap; voice chunks stand alone

# Series detection — used as metadata for filtering
SERIES_PATTERNS = {
    "Maintenance Monday":  r"maintenance monday",
    "Tech Tip Tuesday":    r"tech tip tuesday",
    "Thoughtful Thursday": r"thoughtful thursday",
    "Workflow Wednesday":  r"workflow wednesday",
    "Ask TechBear":        r"ask tech ?bear",
}

# Voice quality markers — used to score chunks for voice collection
# Chunks with more markers are better exemplars of TechBear's register
VOICE_MARKERS = [
    r"\btechnocub", r"\bsugar\b", r"\bhoney\b", r"\bdarling\b",
    r"\bdiva\b", r"\bsequin", r"\bsass", r"\bbear\b",
    r"\bglam", r"doggoneit", r"darling", r"precious",
]

# Minimum voice marker hits for a chunk to enter the voice collection
# Chunks scoring 0 are skipped — they're pure technical content with
# no character register and don't help Stage 2.
VOICE_MIN_SCORE = 1


# =============================================================
# Text cleaning
# =============================================================

def strip_html(text: str) -> str:
    """
    Strips WordPress Gutenberg block comments and HTML tags,
    then collapses whitespace to single spaces.
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def detect_series(title: str) -> str:
    """Returns the series name if the post title matches a known series."""
    for name, pattern in SERIES_PATTERNS.items():
        if re.search(pattern, title, re.IGNORECASE):
            return name
    return "standalone"


def voice_score(text: str) -> int:
    """
    Count how many distinct voice markers appear in a text chunk.
    Used to filter voice chunks — higher = better exemplar.
    """
    return sum(
        1 for m in VOICE_MARKERS
        if re.search(m, text, re.IGNORECASE)
    )


# =============================================================
# Chunking
# =============================================================

def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """
    Splits text into overlapping character-based chunks.

    Character-based (not token-based) because we don't want to
    import a tokenizer just for chunking — nomic-embed-text handles
    its own tokenization. At ~4 chars/token, chunk_size=1800 ≈ 450
    tokens, well within nomic-embed-text's 8192 token context.

    Tries to split at sentence boundaries ('. ') to avoid cutting
    mid-sentence. Falls back to hard split if no boundary is found.
    """
    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        if end >= len(text):
            # Last chunk — take the rest
            chunk = text[start:].strip()
            if chunk:
                chunks.append(chunk)
            break

        # Try to break at a sentence boundary within the last 20% of the chunk
        search_start = start + int(chunk_size * 0.8)
        boundary = text.rfind(". ", search_start, end)

        if boundary != -1:
            end = boundary + 1  # include the period

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        start = end - overlap

    return chunks


# =============================================================
# Embedding via Ollama
# =============================================================

def embed(texts: list[str]) -> list[list[float]]:
    """
    Calls the Ollama embeddings endpoint for a list of texts.
    Returns a list of embedding vectors.

    Ollama's /api/embed endpoint (v0.3+) accepts a batch via
    the 'input' field. Falls back to single-call /api/embeddings
    for older Ollama versions.
    """
    # Try batch endpoint first (Ollama >= 0.3)
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
    except Exception:
        pass

    # Fallback: single-call loop via legacy endpoint
    embeddings = []
    for text in texts:
        r = requests.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=60,
        )
        r.raise_for_status()
        embeddings.append(r.json()["embedding"])

    return embeddings


# =============================================================
# Loading corpus
# =============================================================

def load_corpus() -> list[dict]:
    """
    Loads and cleans the WordPress export CSV.
    Returns a list of post dicts with clean_content added.
    Excludes pre-pivot corporate-voice posts by ID.
    """
    posts = []
    with open(CORPUS_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            post_id = int(row["ID"])
            if post_id in EXCLUDED_IDS:
                continue
            row["clean_content"] = strip_html(row["Content"])
            row["series"] = detect_series(row["Title"])
            posts.append(row)

    return posts


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
    """
    Chunks all posts, optionally filters by voice score,
    embeds, and upserts into the given ChromaDB collection.

    voice_filter=True: skip chunks below VOICE_MIN_SCORE
    voice_filter=False: ingest all chunks regardless of voice score
    """
    total_chunks = 0
    skipped_chunks = 0
    batch_ids = []
    batch_docs = []
    batch_metas = []
    BATCH_SIZE = 50  # embed and upsert in batches to avoid memory spikes

    for post in posts:
        post_id = post["ID"]
        title = post["Title"]
        series = post["series"]
        date = post["Date"][:10]  # YYYY-MM-DD only
        tags = post["Tags"][:500]  # truncate very long tag strings
        content = post["clean_content"]

        chunks = chunk_text(content, chunk_size, overlap)

        for i, chunk in enumerate(chunks):
            score = voice_score(chunk)

            if voice_filter and score < VOICE_MIN_SCORE:
                skipped_chunks += 1
                continue

            chunk_id = f"{post_id}_chunk_{i}"
            meta = {
                "post_id": int(post_id),
                "title": title,
                "series": series,
                "date": date,
                "tags": tags,
                "voice_score": score,
                "chunk_index": i,
                "total_chunks": len(chunks),
            }

            batch_ids.append(chunk_id)
            batch_docs.append(chunk)
            batch_metas.append(meta)
            total_chunks += 1

            # Flush batch
            if len(batch_ids) >= BATCH_SIZE:
                _flush_batch(collection, batch_ids,
                             batch_docs, batch_metas, label)
                batch_ids, batch_docs, batch_metas = [], [], []

    # Final partial batch
    if batch_ids:
        _flush_batch(collection, batch_ids, batch_docs, batch_metas, label)

    print(
        f"  [{label}] Done — {total_chunks} chunks ingested"
        + (f", {skipped_chunks} skipped (low voice score)" if voice_filter else "")
    )


def _flush_batch(
    collection: chromadb.Collection,
    ids: list[str],
    docs: list[str],
    metas: list[dict],
    label: str,
) -> None:
    """Embeds and upserts a batch of chunks into ChromaDB."""
    print(f"  [{label}] Embedding batch of {len(ids)}...", end="", flush=True)
    t0 = time.perf_counter()
    embeddings = embed(docs)
    elapsed = time.perf_counter() - t0
    print(f" {elapsed:.1f}s")

    collection.upsert(
        ids=ids,
        documents=docs,
        embeddings=embeddings,
        metadatas=metas,
    )


# =============================================================
# Entry point
# =============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Ingest TechBear corpus into ChromaDB"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete and recreate collections before ingesting",
    )
    args = parser.parse_args()

    # Verify corpus exists
    if not CORPUS_PATH.exists():
        print(f"ERROR: Corpus not found at {CORPUS_PATH}")
        sys.exit(1)

    # Verify Ollama + embedding model are available
    print(f"Checking Ollama at {OLLAMA_BASE_URL}...")
    try:
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        if not any(EMBED_MODEL in m for m in models):
            print(f"ERROR: {EMBED_MODEL} not found in Ollama.")
            print(f"Run: ollama pull {EMBED_MODEL}")
            sys.exit(1)
        print(f"  Ollama OK — {EMBED_MODEL} available")
    except Exception as e:
        print(f"ERROR: Can't reach Ollama — {e}")
        sys.exit(1)

    # Connect to ChromaDB
    CHROMA_PATH.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    print(f"ChromaDB at {CHROMA_PATH}")

    # Handle --force: delete existing collections
    if args.force:
        for name in [FACTS_COLLECTION, VOICE_COLLECTION]:
            try:
                client.delete_collection(name)
                print(f"  Deleted existing collection: {name}")
            except Exception:
                pass

    # Get or create collections
    _embed_fn = OllamaEmbeddingFunction(
        url=f"{OLLAMA_BASE_URL}/api/embeddings",
        model_name=EMBED_MODEL,
    )

    facts_col = client.get_or_create_collection(
        name=FACTS_COLLECTION,
        embedding_function=_embed_fn,
        metadata={
            "description": "TechBear factual content — full explanations and step-by-step guidance",
            "embed_model": EMBED_MODEL,
            "chunk_chars": FACTS_CHUNK_CHARS,
        },
    )


    voice_col = client.get_or_create_collection(
        name=VOICE_COLLECTION,
        embedding_function=_embed_fn,
        metadata={
            "description": "TechBear voice exemplars — zingers, metaphors, punchy passages",
            "embed_model": EMBED_MODEL,
            "chunk_chars": VOICE_CHUNK_CHARS,
        },
    )

# Skip if already populated and --force not passed
if not args.force:
    facts_count = facts_col.count()
    voice_count = voice_col.count()
    if facts_count > 0 or voice_count > 0:
        print(
            f"\nCollections already populated "
            f"(facts={facts_count}, voice={voice_count})."
        )
        print("Run with --force to re-embed from scratch.")
        sys.exit(0)

    # Load and clean corpus
    print("\nLoading corpus...")
    posts = load_corpus()
    print(f"  {len(posts)} posts loaded (4 pre-pivot posts excluded)")

    # Ingest facts collection
    print(f"\nIngesting [{FACTS_COLLECTION}]")
    print(
        f"  chunk_size={FACTS_CHUNK_CHARS} chars, overlap={FACTS_OVERLAP_CHARS} chars")
    ingest_collection(
        collection=facts_col,
        posts=posts,
        chunk_size=FACTS_CHUNK_CHARS,
        overlap=FACTS_OVERLAP_CHARS,
        voice_filter=False,   # all chunks, technical content included
        label="facts",
    )

    # Ingest voice collection
    print(f"\nIngesting [{VOICE_COLLECTION}]")
    print(
        f"  chunk_size={VOICE_CHUNK_CHARS} chars, overlap={VOICE_OVERLAP_CHARS} chars"
        f"  (min voice score: {VOICE_MIN_SCORE})"
    )
    ingest_collection(
        collection=voice_col,
        posts=posts,
        chunk_size=VOICE_CHUNK_CHARS,
        overlap=VOICE_OVERLAP_CHARS,
        voice_filter=True,    # only chunks with TechBear voice markers
        label="voice",
    )

    # Summary
    print(f"\n{'='*50}")
    print(f"Ingestion complete.")
    print(f"  {FACTS_COLLECTION}: {facts_col.count()} chunks")
    print(f"  {VOICE_COLLECTION}: {voice_col.count()} chunks")
    print(f"  ChromaDB stored at: {CHROMA_PATH}")
    print(f"\nNext step: wire retrieval into backend/services/rag.py")


if __name__ == "__main__":
    main()
