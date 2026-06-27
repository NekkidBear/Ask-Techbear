"""
scripts/ingest_corpus.py — Corpus ingestion for Ask TechBear RAG
Gymnarctos Studios LLC

Ingests the WordPress blog post corpus into three ChromaDB collections:

  techbear_facts  — factual technical content (is_fiction=False)
                    Bio sections and tall tale content excluded.

  techbear_voice  — voice/style exemplars, smaller chunks.
                    Includes all posts including Multiverse episodes.

  techbear_lore   — TechBear canon and tall tale background.
                    Sources: Multiverse episodes (lore_tier=canon)
                             Bio section tall tale callbacks (lore_tier=tall_tale)

Bio section detection:
    Each article is split at its "About TechBear" bio section.
    Body content → techbear_facts
    Bio content → techbear_lore (if canonical tall tale phrases detected)
                  discarded otherwise (contact/attribution only)

Fiction detection:
    Multiverse posts detected by title pattern and tags.
    Excluded from techbear_facts, ingested into techbear_lore as canon.
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
LORE_COLLECTION = "techbear_lore"

FACTS_CHUNK_CHARS = 1800
FACTS_OVERLAP_CHARS = 200
VOICE_CHUNK_CHARS = 500
VOICE_OVERLAP_CHARS = 50
LORE_CHUNK_CHARS = 800
LORE_OVERLAP_CHARS = 100

SERIES_PATTERNS = {
    "Maintenance Monday": r"maintenance monday",
    "Tech Tip Tuesday": r"tech tip tuesday",
    "Thoughtful Thursday": r"thoughtful thursday",
    "Workflow Wednesday": r"workflow wednesday",
    "Ask TechBear": r"ask tech ?bear",
    "Guide to the Multiverse": r"guide to the multiverse",
}

VOICE_MARKERS = [
    r"\btechnocub", r"\bsugar\b", r"\bhoney\b", r"\bdarling\b",
    r"\bdiva\b", r"\bsequin", r"\bsass", r"\bbear\b",
    r"\bglam", r"doggoneit", r"precious",
]

VOICE_MIN_SCORE = 1

# Fiction detection — Multiverse posts are canon lore
FICTION_TITLE_PATTERNS = [
    r"guide to the multiverse",
    r"friday funday.*multiverse",
]
FICTION_TAG_MARKERS = [
    "TechBearsGuideToTheMultiverse",
    "GuideToTheMultiverse",
    "MultiverseAdventures",
]

# Bio section boundary markers
BIO_BOUNDARY_PATTERNS = [
    r"about (the )?tech ?bear",
    r"about the author",
    r"greetings,? techno?cubs",
    r"emerging from the deepest",
    r"from the deepest.*digital",
    r"techbear is (a |the |an )(self-|allegedly |fabulously )",
    r"techbear.*alter ego of jason",
    r"jason.*gymnarctos studios",
    r"your (favorite|fabulous|sassy).*tech.?(bear|diva|guru)",
]

# Canonical tall tale phrases — bio chunks containing these go to lore
CANONICAL_TALL_TALE_PATTERNS = [
    r"debugged (the )?(NASA|Y2K|Matrix|Pentagon|mission control|quantum)",
    r"legend has it",
    r"single.handedly (debugged|fixed|saved|solved)",
    r"rusty keyboard and sheer willpower",
    r"ancient assembly language scrolls",
    r"lost city of Silicon Valley",
    r"bedazzled (stylus|keyboard)",
    r"millennia of experience",
    r"sequined lab coat",
    r"debugging systems for the Pentagon",
    r"accidentally invent",
    r"root access to the mainframe of the universe",
    r"quantum computer.*three energy drinks",
    r"personal tech advisor to three different (royal|head)",
    r"techno.titan of the twenty.second century",
    r"digital deity who claims",
    r"emerged fully formed",
    r"caffeinated code whisperer",
    r"predict.*lottery",
    r"debug.*quantum entanglement",
]

_compiled_bio = [re.compile(p, re.IGNORECASE) for p in BIO_BOUNDARY_PATTERNS]
_compiled_canon = [re.compile(p, re.IGNORECASE)
                   for p in CANONICAL_TALL_TALE_PATTERNS]


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
    Detect Multiverse fiction posts by title pattern and tags.
    Title-based detection is primary — some posts have empty tags.
    """
    for pattern in FICTION_TITLE_PATTERNS:
        if re.search(pattern, title, re.IGNORECASE):
            return True
    return any(
        marker.lower() in tags.lower()
        for marker in FICTION_TAG_MARKERS
    )


def detect_bio_boundary(sentences: list[str]) -> int | None:
    """
    Find the sentence index where the About TechBear bio section begins.
    Returns None if no bio section detected.
    Searches from the 70% mark onwards to avoid false positives in article body.
    """
    start_search = int(len(sentences) * 0.70)
    for i, sent in enumerate(sentences[start_search:], start=start_search):
        for pat in _compiled_bio:
            if pat.search(sent):
                return i
    return None


def has_canonical_tall_tale(text: str) -> tuple[bool, list[str]]:
    """
    Check if text contains canonical TechBear tall tale phrases.
    Returns (has_lore, list_of_matched_phrases).
    """
    matched = []
    for pat in _compiled_canon:
        m = pat.search(text)
        if m:
            matched.append(m.group()[:80])
    return bool(matched), matched


def split_body_and_bio(content: str) -> tuple[str, str | None]:
    """
    Split article content into (body, bio) at the About TechBear boundary.
    Returns (full_content, None) if no bio section detected.
    """
    sentences = re.split(r"(?<=[.!?])\s+", content)
    bio_start = detect_bio_boundary(sentences)

    if bio_start is None:
        return content, None

    body = " ".join(sentences[:bio_start]).strip()
    bio = " ".join(sentences[bio_start:]).strip()
    return body, bio


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


# =============================================================
# Corpus loading
# =============================================================

def load_corpus() -> list[dict]:
    """Load, clean, and annotate WordPress CSV corpus."""
    posts = []

    with open(CORPUS_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            post_id = int(row["ID"])

            if post_id in EXCLUDED_IDS:
                continue

            raw_content = strip_html(row["Content"])
            title = row["Title"]
            tags = row.get("Tags", "")

            is_fiction = is_fiction_post(title, tags)

            if is_fiction:
                # Multiverse posts — full content goes to lore as canon
                row["clean_content"] = raw_content
                row["body_content"] = raw_content
                row["bio_content"] = None
                row["is_fiction"] = True
                row["lore_tier"] = "canon"
                row["has_canonical_lore"] = True
                row["canonical_phrases"] = []
            else:
                # Factual posts — split body from bio
                body, bio = split_body_and_bio(raw_content)
                has_lore, phrases = has_canonical_tall_tale(bio or "")

                row["clean_content"] = raw_content
                row["body_content"] = body
                row["bio_content"] = bio
                row["is_fiction"] = False
                row["lore_tier"] = None
                row["has_canonical_lore"] = has_lore
                row["canonical_phrases"] = phrases

            row["series"] = detect_series(title)
            posts.append(row)

    return posts


# =============================================================
# Chroma setup
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
        for name in [FACTS_COLLECTION, VOICE_COLLECTION, LORE_COLLECTION]:
            try:
                client.delete_collection(name)
                print(f"  Deleted existing collection: {name}")
            except Exception as e:  # pylint: disable=broad-exception-caught
                # ChromaDB raises NotFoundError, ValueError, or RuntimeError
                # inconsistently across versions — all are safe to ignore here
                print(f"  Delete skipped for {name}: {e}")
        return None

    facts_col = client.get_or_create_collection(
        name=FACTS_COLLECTION,
        embedding_function=embed_fn,
        metadata={
            "description": "TechBear factual technical content",
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
    lore_col = client.get_or_create_collection(
        name=LORE_COLLECTION,
        embedding_function=embed_fn,
        metadata={
            "description": "TechBear canon lore and tall tale background",
            "embed_model": EMBED_MODEL,
            "chunk_chars": LORE_CHUNK_CHARS,
        },
    )

    return facts_col, voice_col, lore_col


def already_populated(facts_col, voice_col, lore_col) -> bool:
    """Check if collections already contain data."""
    counts = {
        "facts": facts_col.count(),
        "voice": voice_col.count(),
        "lore": lore_col.count(),
    }
    if any(c > 0 for c in counts.values()):
        print(
            f"\nCollections already populated "
            f"(facts={counts['facts']}, "
            f"voice={counts['voice']}, "
            f"lore={counts['lore']})."
        )
        print("Run with --force to rebuild.")
        return True
    return False


# =============================================================
# Ingestion
# =============================================================

def ingest_facts(collection, posts: list[dict]) -> None:
    """
    Ingest body content of non-fiction posts into techbear_facts.
    Bio sections excluded. is_fiction=False filter applied at retrieval time.
    """
    batch_ids, batch_docs, batch_metas = [], [], []
    batch_size = 50
    total = 0

    for post in posts:
        if post["is_fiction"]:
            continue  # Multiverse posts go to lore only

        post_id = post["ID"]
        content = post["body_content"]  # Body only — bio excluded
        if not content:
            continue

        chunks = chunk_text(content, FACTS_CHUNK_CHARS, FACTS_OVERLAP_CHARS)

        for i, chunk in enumerate(chunks):
            batch_ids.append(f"facts_{post_id}_chunk_{i}")
            batch_docs.append(chunk)
            batch_metas.append({
                "post_id": int(post_id),
                "title": post["Title"],
                "series": post["series"],
                "date": post["Date"][:10],
                "tags": post.get("Tags", "")[:500],
                "voice_score": voice_score(chunk),
                "chunk_index": i,
                "total_chunks": len(chunks),
                "is_fiction": False,
                "content_type": "article",
            })
            total += 1

            if len(batch_ids) >= batch_size:
                _flush_batch(collection, batch_ids,
                             batch_docs, batch_metas, "facts")
                batch_ids, batch_docs, batch_metas = [], [], []

    if batch_ids:
        _flush_batch(collection, batch_ids, batch_docs, batch_metas, "facts")

    print(f"  [facts] Done — {total} chunks ingested")


def ingest_voice(collection, posts: list[dict]) -> None:
    """
    Ingest all posts into techbear_voice using voice score filtering.
    Includes Multiverse posts — they have excellent voice exemplars.
    """
    batch_ids, batch_docs, batch_metas = [], [], []
    batch_size = 50
    total = 0
    skipped = 0

    for post in posts:
        post_id = post["ID"]
        content = post["clean_content"]  # Full content for voice
        if not content:
            continue

        chunks = chunk_text(content, VOICE_CHUNK_CHARS, VOICE_OVERLAP_CHARS)

        for i, chunk in enumerate(chunks):
            score = voice_score(chunk)
            if score < VOICE_MIN_SCORE:
                skipped += 1
                continue

            batch_ids.append(f"voice_{post_id}_chunk_{i}")
            batch_docs.append(chunk)
            batch_metas.append({
                "post_id": int(post_id),
                "title": post["Title"],
                "series": post["series"],
                "date": post["Date"][:10],
                "tags": post.get("Tags", "")[:500],
                "voice_score": score,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "is_fiction": post["is_fiction"],
                "content_type": "voice",
            })
            total += 1

            if len(batch_ids) >= batch_size:
                _flush_batch(collection, batch_ids,
                             batch_docs, batch_metas, "voice")
                batch_ids, batch_docs, batch_metas = [], [], []

    if batch_ids:
        _flush_batch(collection, batch_ids, batch_docs, batch_metas, "voice")

    print(f"  [voice] Done — {total} chunks ingested, {skipped} skipped")


def ingest_lore(collection, posts: list[dict]) -> None:
    """
    Ingest lore content into techbear_lore:
        - Multiverse episodes: full content, lore_tier=canon
        - Bio sections with canonical tall tale phrases: lore_tier=tall_tale

    Never used for factual validation — lore consistency only.
    """
    batch_ids, batch_docs, batch_metas = [], [], []
    batch_size = 50
    total = 0
    canon_count = 0
    tall_tale_count = 0

    for post in posts:
        post_id = post["ID"]

        if post["is_fiction"]:
            # Full Multiverse episode as canon lore
            content = post["clean_content"]
            lore_tier = "canon"
            source_type = "multiverse_episode"
            consistency_required = True
        elif post["has_canonical_lore"] and post["bio_content"]:
            # Bio section tall tale callbacks
            content = post["bio_content"]
            lore_tier = "tall_tale"
            source_type = "article_callback"
            consistency_required = False
        else:
            continue

        if not content:
            continue

        chunks = chunk_text(content, LORE_CHUNK_CHARS, LORE_OVERLAP_CHARS)

        for i, chunk in enumerate(chunks):
            chunk_has_lore, phrases = has_canonical_tall_tale(chunk)

            batch_ids.append(f"lore_{post_id}_chunk_{i}")
            batch_docs.append(chunk)
            batch_metas.append({
                "post_id": int(post_id),
                "title": post["Title"],
                "series": post["series"],
                "date": post["Date"][:10],
                "tags": post.get("Tags", "")[:500],
                "is_fiction": True,
                "lore_tier": lore_tier,
                "source_type": source_type,
                "consistency_required": consistency_required,
                "has_canonical_phrases": chunk_has_lore,
                "canonical_phrases": ", ".join(phrases[:3]),
                "chunk_index": i,
                "total_chunks": len(chunks),
            })
            total += 1
            if lore_tier == "canon":
                canon_count += 1
            else:
                tall_tale_count += 1

            if len(batch_ids) >= batch_size:
                _flush_batch(collection, batch_ids,
                             batch_docs, batch_metas, "lore")
                batch_ids, batch_docs, batch_metas = [], [], []

    if batch_ids:
        _flush_batch(collection, batch_ids, batch_docs, batch_metas, "lore")

    print(
        f"  [lore] Done — {total} chunks ingested "
        f"({canon_count} canon, {tall_tale_count} tall_tale)"
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
# Main
# =============================================================

def main() -> None:
    """Ingest the WordPress corpus into ChromaDB facts, voice, and lore collections."""
    parser = argparse.ArgumentParser(
        description="Ask TechBear corpus ingestion"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete and rebuild all collections",
    )
    args = parser.parse_args()

    if not CORPUS_PATH.exists():
        print("Corpus not found.")
        sys.exit(1)

    client = init_chroma()
    print(f"ChromaDB at {CHROMA_PATH}")

    embed_fn = init_embedding_function()
    result = init_collections(client, embed_fn, args.force)

    if args.force:
        init_collections(client, embed_fn, force=True)

        result = init_collections(client, embed_fn, force=False)
        if result is None:
            raise RuntimeError("Failed to initialize Chroma collections.")

        facts_col, voice_col, lore_col = result
    else:
        result = init_collections(client, embed_fn, force=False)
        if result is None:
            raise RuntimeError("Failed to initialize Chroma collections.")

        facts_col, voice_col, lore_col = result

        if already_populated(facts_col, voice_col, lore_col):
            sys.exit(0)

    print("\nLoading corpus...")
    posts = load_corpus()
    fiction = sum(1 for p in posts if p["is_fiction"])
    with_lore = sum(
        1 for p in posts if p["has_canonical_lore"] and not p["is_fiction"])
    print(
        f"Loaded {len(posts)} posts ({fiction} fiction, {with_lore} with tall tale bios)")

    print("\nIngesting facts (body content only, fiction excluded)...")
    ingest_facts(facts_col, posts)

    print("\nIngesting voice (all posts, voice-scored)...")
    ingest_voice(voice_col, posts)

    print("\nIngesting lore (Multiverse canon + tall tale bio sections)...")
    ingest_lore(lore_col, posts)

    print("\nDone.")
    print(f"  {FACTS_COLLECTION}: {facts_col.count()} chunks")
    print(f"  {VOICE_COLLECTION}: {voice_col.count()} chunks")
    print(f"  {LORE_COLLECTION}: {lore_col.count()} chunks")


if __name__ == "__main__":
    main()
