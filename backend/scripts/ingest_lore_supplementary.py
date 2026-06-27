"""
backend/scripts/ingest_lore_supplementary.py
Ask TechBear — Gymnarctos Studios LLC

Ingests supplementary lore and voice documents that are not in the
WordPress CSV corpus into the appropriate ChromaDB collections.

Sources and routing:
    lore_bible.md          → techbear_lore  (lore_tier=canon_reference)
    Social intro script    → techbear_voice + techbear_lore (lore_tier=tall_tale)
    D20 roast table        → techbear_voice
    Bio examples doc       → techbear_lore  (lore_tier=tall_tale)
    Binge-watching list    → techbear_lore  (lore_tier=flavor)

These documents supplement the main corpus ingest — run AFTER
`ingest_corpus.py` has already populated the collections.

Uses upsert throughout — safe to re-run when new supplementary
documents are added.

Usage (from repo root):
    python -m backend.scripts.ingest_lore_supplementary
    python -m backend.scripts.ingest_lore_supplementary --dry-run
    python -m backend.scripts.ingest_lore_supplementary --force
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

import chromadb
import requests
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction

# =============================================================
# Configuration — mirrors ingest_corpus.py settings
# =============================================================

BACKEND_DIR = Path(__file__).resolve().parent.parent
CHROMA_PATH = BACKEND_DIR.parent / "chroma_db"
CORPUS_DIR = BACKEND_DIR / "corpus"
CHARACTER_DIR = BACKEND_DIR / "character"

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text"

VOICE_COLLECTION = "techbear_voice"
LORE_COLLECTION = "techbear_lore"

# Chunk sizes match ingest_corpus.py for consistency
VOICE_CHUNK_CHARS = 500
VOICE_OVERLAP_CHARS = 50
LORE_CHUNK_CHARS = 800
LORE_OVERLAP_CHARS = 100

# =============================================================
# Source document registry
# Each entry defines a supplementary document and its routing.
# Add new documents here — ingest handles the rest.
# =============================================================

SUPPLEMENTARY_SOURCES = [
    {
        "id": "lore_bible",
        "label": "TechBear Lore Bible",
        "path": CHARACTER_DIR / "lore_bible.md",
        "format": "markdown",
        "collections": ["lore"],
        "lore_tier": "canon_reference",
        "source_type": "lore_bible",
        "is_fiction": True,
        "consistency_required": True,
        "chunk_chars": LORE_CHUNK_CHARS,
        "overlap_chars": LORE_OVERLAP_CHARS,
    },
    {
        "id": "social_intro",
        "label": "TechBear Social Intro Script",
        "path": CORPUS_DIR / "Techbear_Social_Intro.md",
        "format": "markdown",
        "collections": ["voice", "lore"],
        "lore_tier": "tall_tale",
        "source_type": "social_script",
        "is_fiction": False,
        "consistency_required": False,
        "chunk_chars": VOICE_CHUNK_CHARS,
        "overlap_chars": VOICE_OVERLAP_CHARS,
    },
    {
        "id": "d20_roast",
        "label": "TechBear D20 Roast Table",
        "path": CORPUS_DIR / "Techbear's_D20_Roast_Table.md",
        "format": "markdown",
        "collections": ["voice"],
        "lore_tier": None,
        "source_type": "roast_table",
        "is_fiction": False,
        "consistency_required": False,
        "chunk_chars": VOICE_CHUNK_CHARS,
        "overlap_chars": VOICE_OVERLAP_CHARS,
    },
    {
        "id": "bio_examples",
        "label": "TechBear Bio Examples (Style Reference)",
        "path": CORPUS_DIR / "Techbear_bio_examples.md",
        "format": "markdown",
        "collections": ["voice"],
        "lore_tier": None,
        "source_type": "bio_style_examples",
        "is_fiction": False,
        "consistency_required": False,
        "chunk_chars": VOICE_CHUNK_CHARS,
        "overlap_chars": VOICE_OVERLAP_CHARS,
    },
    {
        "id": "lore_bios",
        "label": "TechBear Canonical Lore Bios",
        "path": CORPUS_DIR / "Techbear_lore_bios.md",
        "format": "markdown",
        "collections": ["lore"],
        "lore_tier": "tall_tale",
        "source_type": "bio_lore",
        "is_fiction": True,
        "consistency_required": False,
        "chunk_chars": LORE_CHUNK_CHARS,
        "overlap_chars": LORE_OVERLAP_CHARS,
    },
    {
        "id": "binge_watching",
        "label": "TechBear Binge-Watching List",
        "path": CORPUS_DIR / "Techbear's_binge-watching_list.md",
        "format": "markdown",
        "collections": ["lore"],
        "lore_tier": "flavor",
        "source_type": "binge_watching_list",
        "is_fiction": True,
        "consistency_required": False,
        "chunk_chars": LORE_CHUNK_CHARS,
        "overlap_chars": LORE_OVERLAP_CHARS,
    },
]


# =============================================================
# Text extraction
# =============================================================

def extract_markdown(path: Path) -> str:
    """Read markdown file as plain text."""
    return path.read_text(encoding="utf-8")


def extract_text_file(path: Path) -> str:
    """Read a plain text file."""
    return path.read_text(encoding="utf-8")


def extract_text(source: dict) -> str | None:
    """Extract text from a supplementary source based on its format."""
    path = source["path"]
    fmt = source["format"]

    if not path.exists():
        print(f"  ⚠ Not found: {path} — skipping")
        return None

    try:
        if fmt == "markdown":
            return extract_markdown(path)
        if fmt == "text":
            return extract_text_file(path)

        print(f"  ⚠ Unsupported format '{fmt}' for {path.name} — skipping")
        return None
    except OSError as exc:
        print(f"  ⚠ Extraction failed for {path.name}: {exc}")
        return None


# =============================================================
# Text processing — mirrors ingest_corpus.py
# =============================================================

def clean_text(text: str) -> str:
    """Normalize whitespace and strip common PDF/DOCX artifacts."""
    text = re.sub(r"\r\n|\r", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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
# Embedding — mirrors ingest_corpus.py
# =============================================================

def embed(texts: list[str]) -> list[list[float]]:
    """Embed text using Ollama batch endpoint with per-item fallback."""
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


def _flush_batch(
    collection,
    ids: list[str],
    docs: list[str],
    metas: list[dict],
    label: str,
) -> None:
    """Embed and upsert a batch into ChromaDB."""
    print(f"    [{label}] Embedding {len(ids)} chunk(s)...",
          end="", flush=True)
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
# Ingest
# =============================================================

def ingest_source(
    source: dict,
    text: str,
    voice_col,
    lore_col,
    dry_run: bool,
) -> dict[str, int]:
    """
    Chunk and ingest one supplementary source into its target collection(s).
    Returns chunk counts per collection.
    """
    counts = {"voice": 0, "lore": 0}
    src_id = source["id"]
    chunk_chars = source["chunk_chars"]
    overlap_chars = source["overlap_chars"]

    chunks = chunk_text(text, chunk_chars, overlap_chars)

    if not chunks:
        print(f"  ⚠ No chunks produced for {source['label']}")
        return counts

    print(f"  {len(chunks)} chunk(s) from {source['label']}")

    for collection_key in source["collections"]:
        collection = voice_col if collection_key == "voice" else lore_col

        batch_ids, batch_docs, batch_metas = [], [], []
        batch_size = 50

        for i, chunk in enumerate(chunks):
            chunk_id = f"supp_{src_id}_{collection_key}_chunk_{i}"
            meta = {
                "source_id": src_id,
                "source_label": source["label"],
                "source_type": source["source_type"],
                "is_fiction": source["is_fiction"],
                "chunk_index": i,
                "total_chunks": len(chunks),
                "supplementary": True,
            }
            if source.get("lore_tier"):
                meta["lore_tier"] = source["lore_tier"]
            if source.get("consistency_required") is not None:
                meta["consistency_required"] = source["consistency_required"]

            batch_ids.append(chunk_id)
            batch_docs.append(chunk)
            batch_metas.append(meta)
            counts[collection_key] += 1

            if len(batch_ids) >= batch_size:
                if not dry_run:
                    _flush_batch(collection, batch_ids, batch_docs, batch_metas,
                                 collection_key)
                batch_ids, batch_docs, batch_metas = [], [], []

        if batch_ids:
            if not dry_run:
                _flush_batch(collection, batch_ids, batch_docs, batch_metas,
                             collection_key)

    return counts


# =============================================================
# Main
# =============================================================

def main() -> None:
    """Parse arguments and ingest supplementary lore documents."""
    parser = argparse.ArgumentParser(
        description="Ingest supplementary lore/voice documents into ChromaDB"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be ingested without writing to ChromaDB",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-ingest even if chunks already exist (upsert overwrites)",
    )
    parser.add_argument(
        "--source",
        dest="source_id",
        default=None,
        help="Ingest a single source by ID (e.g. lore_bible, d20_roast)",
    )
    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN — no writes to ChromaDB\n")

    # Initialize ChromaDB
    if not CHROMA_PATH.exists():
        print(f"ChromaDB not found at {CHROMA_PATH}")
        print("Run ingest_corpus.py first to initialize collections.")
        sys.exit(1)

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    embed_fn = OllamaEmbeddingFunction(
        url=f"{OLLAMA_BASE_URL}/api/embeddings",
        model_name=EMBED_MODEL,
    )

    try:
        voice_col = client.get_collection(
            name=VOICE_COLLECTION, embedding_function=embed_fn  # type: ignore[arg-type]
        )
        lore_col = client.get_collection(
            name=LORE_COLLECTION, embedding_function=embed_fn  # type: ignore[arg-type]
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"Could not open collections: {exc}")
        print("Run ingest_corpus.py first.")
        sys.exit(1)

    print("Collections before ingest:")
    print(f"  {VOICE_COLLECTION}: {voice_col.count()} chunks")
    print(f"  {LORE_COLLECTION}: {lore_col.count()} chunks")
    print()

    # Filter sources if --source specified
    sources = SUPPLEMENTARY_SOURCES
    if args.source_id:
        sources = [s for s in sources if s["id"] == args.source_id]
        if not sources:
            ids = [s["id"] for s in SUPPLEMENTARY_SOURCES]
            print(f"Unknown source ID: {args.source_id}")
            print(f"Available: {ids}")
            sys.exit(1)

    total_voice = 0
    total_lore = 0

    for source in sources:
        print(f"→ {source['label']} ({source['format'].upper()})")

        text = extract_text(source)
        if not text:
            continue

        text = clean_text(text)
        print(f"  {len(text)} chars extracted")

        counts = ingest_source(source, text, voice_col, lore_col, args.dry_run)
        total_voice += counts.get("voice", 0)
        total_lore += counts.get("lore", 0)
        print()

    print("Done.")
    if args.dry_run:
        print(
            f"  Would have ingested: {total_voice} voice chunk(s), {total_lore} lore chunk(s)")
    else:
        print("Collections after ingest:")
        print(f"  {VOICE_COLLECTION}: {voice_col.count()} chunks")
        print(f"  {LORE_COLLECTION}: {lore_col.count()} chunks")
        print(f"  New this run: ~{total_voice} voice, ~{total_lore} lore")


if __name__ == "__main__":
    main()
