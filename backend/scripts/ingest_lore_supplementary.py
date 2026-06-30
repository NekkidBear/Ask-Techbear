"""
backend/scripts/ingest_lore_supplementary.py
Ask TechBear — Gymnarctos Studios LLC

Ingests supplementary lore and voice documents that are not in the
WordPress CSV corpus into the appropriate ChromaDB collections.

Sources and routing:
    lore_bible.md          → techbear_lore  (episode-boundary split)
                             One document per published episode.
                             Preamble and recurring elements as separate docs.
                             Episode 9 (BSG DRAFT) excluded — unpublished/volatile.

    Social intro script    → techbear_voice + techbear_lore (lore_tier=tall_tale)
    D20 roast table        → techbear_voice
    Bio examples doc       → techbear_voice
    Lore bios              → techbear_lore  (bio-boundary split, one doc per episode)
    Binge-watching list    → techbear_lore  (lore_tier=flavor)

Episode → WordPress post_id mapping (used for episode-targeted retrieval):
    Episode 1 — Rocky Horror / Transylvania    post_id=1649
    Episode 2 — Jurassic Park                  post_id=1744
    Episode 3 — Voyager / Delta Quadrant       post_id=1851
    Episode 4 — Star Wars                      post_id=1929
    Episode 5 — 2001: A Space Odyssey / HAL    post_id=2049
    Episode 6 — Deep Space Nine (Part 1)       post_id=2122
    Episode 7 — Deep Space Nine (Part 2)       post_id=2130
    Episode 8 — Discworld / Unseen University  post_id=3419
    Episode 9 — BSG (DRAFT)                    EXCLUDED

These documents supplement the main corpus ingest — run AFTER
`ingest_corpus.py` has already populated the collections.

Uses upsert throughout — safe to re-run when supplementary documents change.

Usage (from repo root):
    python -m backend.scripts.ingest_lore_supplementary
    python -m backend.scripts.ingest_lore_supplementary --dry-run
    python -m backend.scripts.ingest_lore_supplementary --force
    python -m backend.scripts.ingest_lore_supplementary --source lore_bible
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
# Episode → WordPress post_id mapping
# Used to tag lore_bible episode chunks so episode-targeted
# secondary retrieval (v2.8 item 8) can filter by post_id.
# Only published episodes are included — drafts are excluded.
# =============================================================

EPISODE_POST_IDS: dict[str, int] = {
    "Episode 1":  1649,   # Rocky Horror / Transylvania
    "Episode 2":  1744,   # Jurassic Park
    "Episode 3":  1851,   # Voyager / Delta Quadrant
    "Episode 4":  1929,   # Star Wars
    "Episode 5":  2049,   # 2001: A Space Odyssey / HAL 9000
    "Episode 6":  2122,   # Deep Space Nine (Part 1)
    "Episode 7":  2130,   # Deep Space Nine (Part 2)
    "Episode 8":  3419,   # Discworld / Unseen University
    # Episode 9 (BSG) intentionally omitted — unpublished draft
}

# Lore bios post_id mapping — keyed by Bio section header substring
LORE_BIO_POST_IDS: dict[str, list[int]] = {
    "DS9":          [2122, 2130],
    "Star Wars":    [1929],
    "Discworld":    [3419],
    "Delta Quadrant": [1851],
}


# =============================================================
# Source document registry
# Standard sources use the existing token-count chunking path.
# lore_bible and lore_bios use the episode/bio boundary split path.
# =============================================================

SUPPLEMENTARY_SOURCES = [
    {
        "id": "lore_bible",
        "label": "TechBear Lore Bible",
        "path": CHARACTER_DIR / "lore_bible.md",
        "format": "markdown",
        "collections": ["lore"],
        "ingest_mode": "episode_boundary",   # custom split — not token chunks
        "lore_tier": "canon_reference",
        "source_type": "lore_bible",
        "is_fiction": True,
        "consistency_required": True,
    },
    {
        "id": "social_intro",
        "label": "TechBear Social Intro Script",
        "path": CORPUS_DIR / "Techbear_Social_Intro.md",
        "format": "markdown",
        "collections": ["voice", "lore"],
        "ingest_mode": "token_chunks",
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
        "ingest_mode": "token_chunks",
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
        "ingest_mode": "token_chunks",
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
        "ingest_mode": "bio_boundary",       # custom split — not token chunks
        "lore_tier": "tall_tale",
        "source_type": "bio_lore",
        "is_fiction": True,
        "consistency_required": False,
    },
    {
        "id": "binge_watching",
        "label": "TechBear Binge-Watching List",
        "path": CORPUS_DIR / "Techbear's_binge-watching_list.md",
        "format": "markdown",
        "collections": ["lore"],
        "ingest_mode": "token_chunks",
        "lore_tier": "flavor",
        "source_type": "binge_watching_list",
        "is_fiction": True,
        "consistency_required": False,
        "chunk_chars": LORE_CHUNK_CHARS,
        "overlap_chars": LORE_OVERLAP_CHARS,
    },
]


# =============================================================
# Episode boundary splitting — lore_bible.md
# =============================================================

def split_lore_bible_by_episode(text: str) -> list[dict]:
    """
    Split lore_bible.md into one document per published episode plus
    separate documents for the preamble and recurring canon elements.

    Skips Episode 9 (BSG) — unpublished draft, excluded to prevent
    volatile draft content from contaminating retrieval.

    Returns a list of dicts:
        {
            "section_id": str,      # e.g. "episode_2", "preamble", "recurring"
            "label": str,           # human-readable label
            "text": str,            # section text
            "episode_number": int | None,
            "post_id": int | None,  # WordPress post ID for targeted retrieval
            "lore_tier": str,
            "published": bool,
        }
    """
    sections = []

    # Split on episode headers, keeping the header in each section
    # Pattern: ### Episode N — Title (at start of line)
    episode_pattern = re.compile(
        r"^(### Episode \d+[^\n]*)", re.MULTILINE
    )
    parts = episode_pattern.split(text)

    # parts[0] = everything before the first episode header (preamble)
    # parts[1], parts[2] = first header, first body
    # parts[3], parts[4] = second header, second body, etc.

    preamble = parts[0].strip()

    # Split preamble into pre-episode sections:
    # everything up to "## Known Multiverse Assignments" stays as preamble
    # "## Recurring Canon Elements" onward becomes its own section
    recurring_pattern = re.compile(
        r"^## Recurring Canon Elements.*", re.MULTILINE | re.DOTALL
    )
    recurring_match = recurring_pattern.search(preamble)

    if recurring_match:
        pre_preamble = preamble[:recurring_match.start()].strip()
        recurring_text = preamble[recurring_match.start():].strip()
    else:
        pre_preamble = preamble
        recurring_text = None

    # Known Multiverse Assignments header and anything after it but before
    # the first episode header is structural — fold into preamble
    if pre_preamble:
        sections.append({
            "section_id": "preamble",
            "label": "Lore Bible — Core Philosophy and Canon Rules",
            "text": pre_preamble,
            "episode_number": None,
            "post_id": None,
            "lore_tier": "canon_reference",
            "published": True,
        })

    if recurring_text:
        sections.append({
            "section_id": "recurring_canon",
            "label": "Lore Bible — Recurring Canon Elements",
            "text": recurring_text,
            "episode_number": None,
            "post_id": None,
            "lore_tier": "canon_reference",
            "published": True,
        })

    # Process episode sections
    episode_pairs = list(zip(parts[1::2], parts[2::2]))
    for header, body in episode_pairs:
        # Extract episode number
        ep_num_match = re.search(r"Episode (\d+)", header)
        if not ep_num_match:
            continue

        ep_num = int(ep_num_match.group(1))
        ep_key = f"Episode {ep_num}"

        # Skip unpublished drafts
        if "DRAFT" in header or "unpublished" in header.lower():
            print(f"    Skipping {header.strip()[:60]} — draft/unpublished")
            continue

        # Skip episodes not in our post_id map (safety net)
        post_id = EPISODE_POST_IDS.get(ep_key)
        if post_id is None:
            print(f"    Skipping {header.strip()[:60]} — no post_id mapping")
            continue

        section_text = f"{header}\n{body}".strip()

        sections.append({
            "section_id": f"episode_{ep_num}",
            "label": f"Lore Bible — {header.strip()}",
            "text": section_text,
            "episode_number": ep_num,
            "post_id": post_id,
            "lore_tier": "canon_reference",
            "published": True,
        })

    return sections


# =============================================================
# Bio boundary splitting — lore_bios.md
# =============================================================

def split_lore_bios_by_episode(text: str) -> list[dict]:
    """
    Split Techbear_lore_bios.md into one document per bio section
    plus a separate document for the Canon Lore Notes section.

    Each bio section is tagged with the post_id(s) of the episode(s)
    it came from, enabling episode-targeted retrieval.

    Returns a list of dicts with the same shape as split_lore_bible_by_episode.
    """
    sections = []

    # Split on ## Bio: headers
    bio_pattern = re.compile(r"^(## Bio:[^\n]*)", re.MULTILINE)
    parts = bio_pattern.split(text)

    preamble = parts[0].strip()
    if preamble:
        sections.append({
            "section_id": "lore_bios_preamble",
            "label": "Lore Bios — Introduction",
            "text": preamble,
            "episode_number": None,
            "post_id": None,
            "lore_tier": "tall_tale",
            "published": True,
        })

    bio_pairs = list(zip(parts[1::2], parts[2::2]))
    for i, (header, body) in enumerate(bio_pairs):
        # Match post_id(s) from the LORE_BIO_POST_IDS mapping.
        # Match against header only — body text may contain references
        # to other episodes (e.g. Delta Quadrant bio mentions raktajino
        # which would incorrectly match the DS9 key if body is searched).
        post_ids: list[int] = []
        for key, ids in LORE_BIO_POST_IDS.items():
            if key.lower() in header.lower():
                post_ids = ids
                break

        section_text = f"{header}\n{body}".strip()

        # Detect Canon Lore Notes section — cross-episode, no post_id
        is_canon_notes = "Canon Lore Notes" in header
        section_id = (
            "lore_bios_canon_notes" if is_canon_notes
            else f"lore_bios_section_{i}"
        )
        lore_tier = "canon_reference" if is_canon_notes else "tall_tale"

        sections.append({
            "section_id": section_id,
            "label": f"Lore Bios — {header.strip()}",
            "text": section_text,
            "episode_number": None,
            "post_id": post_ids[0] if len(post_ids) == 1 else None,
            # post_ids stored in metadata for multi-episode bios
            "post_ids": post_ids,
            "lore_tier": lore_tier,
            "published": True,
        })

    return sections


# =============================================================
# Force-clear supplementary chunks
# =============================================================

def _delete_supplementary_chunks(
    voice_col,
    lore_col,
    source_id: str | None = None,
) -> None:
    """
    Delete supplementary chunks from voice and lore collections.

    If source_id is provided, deletes only chunks from that source
    (identified by metadata source_id field). Otherwise deletes all
    chunks where supplementary=True, leaving corpus-ingested chunks
    (from ingest_corpus.py) untouched.

    Uses ChromaDB where-clause delete — does not touch the collection
    itself, so ingest_corpus.py data is never affected.
    """
    where: dict
    if source_id:
        # Delete only chunks from this specific source
        where = {"$and": [
            {"supplementary": {"$eq": True}},
            {"source_id": {"$eq": source_id}},
        ]}
        print(
            f"  --force: deleting existing chunks for source_id='{source_id}'")
    else:
        # Delete all supplementary chunks across all sources
        where = {"supplementary": {"$eq": True}}
        print("  --force: deleting all supplementary chunks")

    for col, label in [(voice_col, VOICE_COLLECTION), (lore_col, LORE_COLLECTION)]:
        try:
            result = col.get(where=where, include=[])
            count = len(result.get("ids") or [])
            if count > 0:
                col.delete(where=where)
                print(f"  Deleted {count} chunk(s) from {label}")
            else:
                print(f"  No matching chunks in {label}")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # ChromaDB raises if no results match — safe to continue
            print(f"  Delete skipped for {label}: {exc}")


# =============================================================
# Text extraction
# =============================================================

def extract_text(source: dict) -> str | None:
    """Extract text from a supplementary source based on its format."""
    path = source["path"]

    if not path.exists():
        print(f"  ⚠ Not found: {path} — skipping")
        return None

    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"  ⚠ Extraction failed for {path.name}: {exc}")
        return None


# =============================================================
# Text processing — mirrors ingest_corpus.py
# =============================================================

def clean_text(text: str) -> str:
    """Normalize whitespace and strip common artifacts."""
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
# Ingest — episode boundary mode
# =============================================================

def ingest_episode_boundary(
    source: dict,
    sections: list[dict],
    lore_col,
    dry_run: bool,
) -> int:
    """
    Ingest pre-split episode sections into techbear_lore.

    Each section is ingested as one or more chunks (if the section is
    larger than LORE_CHUNK_CHARS). Episode sections in the lore_bible
    are typically 640-1159 chars — smaller than the chunk size — so
    most will produce exactly one chunk per section.

    Chunk IDs encode both the source and section for auditability:
        supp_{source_id}_{section_id}_chunk_{i}

    Returns total chunk count ingested.
    """
    src_id = source["id"]
    total = 0
    batch_ids, batch_docs, batch_metas = [], [], []
    batch_size = 50

    for section in sections:
        section_id = section["section_id"]
        section_text = clean_text(section["text"])

        if not section_text:
            continue

        # Chunk within the section boundary — no cross-section overlap
        chunks = chunk_text(section_text, LORE_CHUNK_CHARS, LORE_OVERLAP_CHARS)

        ep_num = section.get("episode_number")
        post_id = section.get("post_id")
        post_ids = section.get("post_ids", [])

        print(
            f"  {section['label'][:60]} → "
            f"{len(chunks)} chunk(s)"
            + (f" | post_id={post_id}" if post_id else "")
        )

        for i, chunk in enumerate(chunks):
            chunk_id = f"supp_{src_id}_{section_id}_chunk_{i}"

            meta: dict = {
                "source_id": src_id,
                "source_label": source["label"],
                "source_type": source["source_type"],
                "section_id": section_id,
                "is_fiction": source["is_fiction"],
                "lore_tier": section["lore_tier"],
                "consistency_required": source.get("consistency_required", False),
                "supplementary": True,
                "published": section.get("published", True),
                "chunk_index": i,
                "total_chunks": len(chunks),
            }

            # Episode metadata for targeted retrieval
            if ep_num is not None:
                meta["episode_number"] = ep_num
            if post_id is not None:
                meta["post_id"] = post_id
            # For multi-episode bios, store comma-separated post_ids as string
            if post_ids:
                meta["post_ids"] = ",".join(str(p) for p in post_ids)
                # Also set post_id to first for single-filter retrieval
                if not post_id and len(post_ids) == 1:
                    meta["post_id"] = post_ids[0]

            batch_ids.append(chunk_id)
            batch_docs.append(chunk)
            batch_metas.append(meta)
            total += 1

            if len(batch_ids) >= batch_size:
                if not dry_run:
                    _flush_batch(lore_col, batch_ids,
                                 batch_docs, batch_metas, "lore")
                batch_ids, batch_docs, batch_metas = [], [], []

    if batch_ids:
        if not dry_run:
            _flush_batch(lore_col, batch_ids, batch_docs, batch_metas, "lore")

    return total


# =============================================================
# Ingest — standard token chunk mode
# =============================================================

def ingest_source_token_chunks(
    source: dict,
    text: str,
    voice_col,
    lore_col,
    dry_run: bool,
) -> dict[str, int]:
    """
    Chunk and ingest one supplementary source into its target collection(s)
    using standard token-count chunking. Returns chunk counts per collection.
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
            meta: dict = {
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
        help=(
            "Delete existing supplementary chunks before re-ingesting. "
            "Scoped to --source if specified; otherwise clears all supplementary chunks. "
            "Never touches corpus-ingested chunks (ingest_corpus.py data is safe)."
        ),
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
            # type: ignore[arg-type]
            name=VOICE_COLLECTION, embedding_function=embed_fn  # type: ignore[arg-type]
        )
        lore_col = client.get_collection(
            # type: ignore[arg-type]
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

    # --force: delete existing supplementary chunks before re-ingesting.
    # Scoped to the specific source_id when --source is also specified,
    # so a targeted re-ingest doesn't wipe unrelated supplementary chunks.
    # Corpus-ingested chunks (supplementary=False) are never touched.
    if args.force and not args.dry_run:
        _delete_supplementary_chunks(
            voice_col, lore_col,
            source_id=args.source_id,
        )
        print()

    total_voice = 0
    total_lore = 0

    for source in sources:
        print(
            f"→ {source['label']} ({source['format'].upper() if 'format' in source else 'MD'})")

        text = extract_text(source)
        if not text:
            continue

        text = clean_text(text)
        print(f"  {len(text)} chars extracted")

        ingest_mode = source.get("ingest_mode", "token_chunks")

        if ingest_mode == "episode_boundary":
            # lore_bible.md — split by episode header
            sections = split_lore_bible_by_episode(text)
            print(f"  {len(sections)} section(s) identified")
            count = ingest_episode_boundary(
                source, sections, lore_col, args.dry_run)
            total_lore += count

        elif ingest_mode == "bio_boundary":
            # lore_bios.md — split by bio header
            sections = split_lore_bios_by_episode(text)
            print(f"  {len(sections)} section(s) identified")
            count = ingest_episode_boundary(
                source, sections, lore_col, args.dry_run)
            total_lore += count

        else:
            # Standard token-chunk path for all other sources
            counts = ingest_source_token_chunks(
                source, text, voice_col, lore_col, args.dry_run
            )
            total_voice += counts.get("voice", 0)
            total_lore += counts.get("lore", 0)

        print()

    print("Done.")
    if args.dry_run:
        print(
            f"  Would have ingested: {total_voice} voice chunk(s), "
            f"{total_lore} lore chunk(s)"
        )
    else:
        print("Collections after ingest:")
        print(f"  {VOICE_COLLECTION}: {voice_col.count()} chunks")
        print(f"  {LORE_COLLECTION}: {lore_col.count()} chunks")
        print(f"  New this run: ~{total_voice} voice, ~{total_lore} lore")


if __name__ == "__main__":
    main()
