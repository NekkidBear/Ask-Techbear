"""
backend/scripts/repurpose_draft.py — TechBear Voice Repurposer
Ask TechBear — Gymnarctos Studios LLC

Takes corporate-voice (Jason-authored) blog posts from the WordPress corpus
and runs them through the voice pass only, producing a TechBear-voiced draft
suitable for editorial review.

Unlike the full async pipeline, this script:
  - Skips moderation, factual pass, and critique phases
    (the source content is Jason's own reviewed work — accuracy is assumed)
  - Runs the educational structuring pass to produce the lesson arc scaffold
  - Runs the voice pass against the scaffold using live RAG retrieval
  - Writes output drafts to repurpose_output/ for human review

The output is a DRAFT, not a finished column. Jason edits and approves
before any repurposed content is published or ingested into the corpus.

Usage (from repo root):
    python -m backend.scripts.repurpose_draft --post-ids 725 1071 1255
    python -m backend.scripts.repurpose_draft --csv path/to/corpus.csv --post-ids 725
    python -m backend.scripts.repurpose_draft --list-candidates --csv path/to/corpus.csv

Candidate identification:
    --list-candidates scans the corpus CSV for posts likely written in Jason's
    corporate voice (no TechBear markers) and prints them for review.
    Non-urgent — intended for periodic corpus curation, not daily use.

Output:
    repurpose_output/<post_id>_<slug>.md for each post processed
"""

import argparse
import csv
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.services.pipeline import educational_pass, voice_pass
from backend.services.rag import rag as rag_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("repurpose_output")
OUTPUT_DIR.mkdir(exist_ok=True)

# Markers that indicate a post is already TechBear-voiced
TECHBEAR_MARKERS = [
    "technocub",
    "honey,",
    "sugar,",
    "darling,",
    "sweetie,",
    "doggoneit",
    "helpdesk water",
    "sequin",
    "multiverse",
    "guide to the multiverse",
    "helloooo",
]

# Post type slugs that are always Jason-voice (structural series)
JASON_VOICE_SERIES = [
    "values wednesday",
    "thoughtful thursday",
    "workflow wednesday",
    "maintenance monday",
    "continuous growth",
    "innovation with purpose",
    "inclusion by design",
    "clear communication",
    "accessibility in technology",
]


# =============================================================
# HTML stripping
# =============================================================

def _strip_html(raw: str) -> str:
    """Strip WordPress block markup and HTML tags from post content."""
    raw = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)
    raw = re.sub(r"<[^>]+>", "", raw)
    for entity, replacement in [
        ("&amp;", "&"),
        ("&nbsp;", " "),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&#8217;", "'"),
        ("&#8220;", '"'),
        ("&#8221;", '"'),
        ("&#8216;", "'"),
        ("&rsquo;", "'"),
        ("&ldquo;", '"'),
        ("&rdquo;", '"'),
    ]:
        raw = raw.replace(entity, replacement)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def _is_techbear_voiced(title: str, content: str) -> bool:
    """Return True if a post appears to already be in TechBear's voice."""
    combined = (title + " " + content).lower()
    return any(marker in combined for marker in TECHBEAR_MARKERS)


def _is_jason_voice_candidate(title: str, content: str) -> bool:
    """Return True if a post looks like a Jason-voice corporate blog post."""
    title_lower = title.lower()
    if any(series in title_lower for series in JASON_VOICE_SERIES):
        return True

    # Not TechBear-voiced and doesn't reference the multiverse or D20 / roast content
    return not _is_techbear_voiced(title, content)


# =============================================================
# Corpus loader
# =============================================================

def load_corpus(csv_path: str) -> dict[str, dict]:
    """
    Load the WordPress export CSV and return a dict keyed by post ID string.
    Each value is a dict with title, content (stripped), permalink, date.
    """
    posts = {}
    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("Post Type") != "post":
                continue
            post_id = row["ID"]
            raw_content = row.get("Content", "")
            posts[post_id] = {
                "id": post_id,
                "title": row.get("Title", ""),
                "content": _strip_html(raw_content),
                "permalink": row.get("Permalink", ""),
                "date": row.get("Date", ""),
            }
    return posts


def list_candidates(posts: dict[str, dict]) -> None:
    """Print posts that appear to be Jason-voice candidates for repurposing."""
    print("\nCandidate posts for TechBear voice repurposing:\n")
    candidates = [
        p for p in posts.values()
        if _is_jason_voice_candidate(p["title"], p["content"])
    ]

    if not candidates:
        print("  No candidates found.")
        return

    for p in candidates:
        print(f"  [{p['id']}] {p['title'][:75]}")

    print(f"\n{len(candidates)} candidate(s) found.")
    print("Run with --post-ids to process specific posts.\n")


# =============================================================
# Repurposing pipeline (educational + voice passes only)
# =============================================================

def _build_artifact_from_post(post: dict) -> dict:
    """
    Construct a minimal pipeline artifact from a post dict.
    The factual draft is the stripped post content — accuracy assumed.
    """
    submission = {
        "id": f"repurpose_{post['id']}",
        "attendee_name": "repurpose",
        "question": post["title"],
        "source": "repurpose",
        "expected_scope": "IN_SCOPE",
        "conversation_depth": 0,
        "rolling_context": "",
        "batch_context": [],
        "retrieval_mode": "factual",
    }

    try:
        chunks = rag_service.retrieve_voice(post["title"], k=5)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning(
            "Voice retrieval failed for post %s: %s",
            post["id"],
            exc,
        )
        chunks = []

    artifact = {
        "submission": submission,
        "scores": {},
        "flags": {},
        "drafts": {
            "factual": post["content"],
        },
        "retrieval": {
            "facts": [],
            "voice": chunks,
            "lore": [],
            "retrieval_mode": "factual",
        },
        "passed": True,
        "failure_reason": None,
        "loop_counts": {},
    }
    return artifact


def repurpose_post(post: dict) -> dict:
    """
    Run a single post through educational structuring + voice rewrite.
    Returns the completed artifact.
    """
    logger.info("[%s] %s", post["id"], post["title"][:60])

    artifact = _build_artifact_from_post(post)

    logger.info("  → educational_pass")
    artifact = educational_pass.run(artifact)
    if not artifact.get("passed", True):
        logger.warning(
            "  educational_pass failed: %s",
            artifact.get("failure_reason"),
        )
        return artifact

    logger.info("  → voice_pass")
    artifact = voice_pass.run(artifact)
    if not artifact.get("passed", True):
        logger.warning(
            "  voice_pass failed: %s",
            artifact.get("failure_reason"),
        )
        return artifact

    word_count = len(
        (artifact.get("drafts", {}).get("voice") or "").split()
    )
    logger.info("  ✓ draft complete (%d words)", word_count)
    return artifact


# =============================================================
# Output writer
# =============================================================

def _slugify(title: str) -> str:
    """Convert a post title into a filesystem-safe slug."""
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")[:50]


def write_output(post: dict, artifact: dict) -> Path:
    """Write the repurposed draft to a markdown file in repurpose_output/."""
    slug = _slugify(post["title"])
    filename = OUTPUT_DIR / f"{post['id']}_{slug}.md"

    voice_draft = artifact.get("drafts", {}).get("voice", "")
    edu_structure = artifact.get("drafts", {}).get("educational_structure", "")
    failure = artifact.get("failure_reason", "")
    passed = artifact.get("passed", True)
    status = "COMPLETE" if passed and voice_draft else "FAILED"
    failed_message = f"_Generation failed: {failure}_"
    original_excerpt = " ".join(post["content"].split()[:500])

    content = f"""# Repurposed Draft — {post['title']}

**Source post ID:** {post['id']}
**Source URL:** {post['permalink']}
**Original date:** {post['date']}
**Repurposed at:** {datetime.now(timezone.utc).isoformat()}
**Status:** {status}

---

## TechBear Draft

{voice_draft if voice_draft else failed_message}

---

## Educational Structure (scaffold reference)

{edu_structure if edu_structure else "_Not generated._"}

---

## Original Post (first 500 words)

{original_excerpt}
"""

    filename.write_text(content, encoding="utf-8")
    logger.info("  Written to %s", filename)
    return filename


# =============================================================
# Entry point
# =============================================================

def main() -> None:
    """Parse CLI arguments and repurpose selected posts into TechBear drafts."""
    parser = argparse.ArgumentParser(
        description="Repurpose Jason-voice posts into TechBear drafts"
    )
    parser.add_argument(
        "--post-ids",
        nargs="+",
        metavar="ID",
        help="Post IDs to repurpose (space-separated)",
    )
    parser.add_argument(
        "--csv",
        default="backend/corpus/Post_corpus (1).csv",
        help="Path to WordPress export CSV (default: backend/corpus/Post_corpus (1).csv)",
    )
    parser.add_argument(
        "--list-candidates",
        action="store_true",
        help="Scan corpus and list candidate posts for repurposing, then exit",
    )
    args = parser.parse_args()

    if not Path(args.csv).exists():
        logger.error("CSV not found: %s", args.csv)
        sys.exit(2)

    posts = load_corpus(args.csv)
    logger.info("Loaded %d posts from %s", len(posts), args.csv)

    if args.list_candidates:
        list_candidates(posts)
        sys.exit(0)

    if not args.post_ids:
        parser.error("--post-ids is required unless --list-candidates is used")

    results = {"succeeded": [], "failed": []}

    for post_id in args.post_ids:
        post = posts.get(str(post_id))
        if post is None:
            logger.warning(
                "Post ID %s not found in corpus — skipping", post_id)
            results["failed"].append(post_id)
            continue

        if _is_techbear_voiced(post["title"], post["content"]):
            logger.warning(
                "Post %s ('%s') appears already TechBear-voiced — skipping. "
                "Use --force to override.",
                post_id,
                post["title"][:50],
            )
            results["failed"].append(post_id)
            continue

        artifact = repurpose_post(post)
        output_path = write_output(post, artifact)

        if artifact.get("passed", True) and artifact.get("drafts", {}).get("voice"):
            results["succeeded"].append(str(output_path))
        else:
            results["failed"].append(post_id)

    print()
    print("Repurpose run complete.")
    print(f"  Succeeded: {len(results['succeeded'])}")

    for path in results["succeeded"]:
        print(f"    {path}")

    if results["failed"]:
        print(f"  Failed/skipped: {len(results['failed'])}")
        for fid in results["failed"]:
            print(f"    post_id={fid}")

    print()
    sys.exit(0 if not results["failed"] else 1)


if __name__ == "__main__":
    main()
