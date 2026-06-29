"""
Ask TechBear v2.7 — Episode Isolation for Lore Retrieval
Gymnarctos Studios LLC

Post-processes ChromaDB lore retrieval results to prevent cross-episode
contamination before chunks are passed to the factual pass.

The problem this solves
-----------------------
The lore collection contains two kinds of documents:

  1. multiverse_episode  — chunks from individual episode posts (post_id scoped)
  2. lore_bible          — a single multi-episode reference document (24 chunks)

When a question asks about a specific episode (e.g. "What was the Jurassic Park
incident?"), ChromaDB returns the correct episode chunks AND adjacent lore_bible
chunks that summarise *other* episodes. The factual pass receives all of them
and synthesises across episodes — producing answers that mix Jurassic Park with
Voyager's coffee replicator.

The fix
-------
1. Detect whether a dominant episode is present in the retrieved chunks.
   A dominant episode is defined as: >= MIN_EPISODE_CHUNKS multiverse_episode
   chunks sharing the same post_id.

2. If a dominant episode is found:
   - Keep all multiverse_episode chunks for that episode.
   - Demote lore_bible canon_reference chunks (they cover multiple episodes
     and are the contamination source). Keep lore_bible chunks only if they
     are supplementary=True AND the dominant episode is already well-represented
     (i.e. we have enough episode chunks that lore_bible adds context, not noise).
   - Keep tall_tale chunks regardless — they are generic TechBear mythology,
     not episode-specific, so they do not cause contamination.

3. Return an EpisodeContext dataclass alongside the filtered chunk list.
   The factual pass and fact_critique receive this context so they can
   explicitly evaluate episode relevance rather than general lore consistency.

4. If no dominant episode is found (question is lore-general, or a tall_tale
   question with no single episode), return chunks unmodified with no episode
   context. The pipeline behaviour is unchanged for non-episode questions.

Integration points
------------------
Call isolate_episode_chunks() in the retrieval phase, after ChromaDB returns
results and before chunks are formatted into the factual pass prompt.

Pass the returned EpisodeContext into:
  - The factual pass prompt (via character_facts.md {EPISODE_CONTEXT} placeholder)
  - The fact_critique phase scores dict (as "episode_context")
  - The result artifact retrieval_diagnostics (for test harness visibility)
"""

from __future__ import annotations

from dataclasses import dataclass, field


# Minimum multiverse_episode chunks with the same post_id to declare
# that episode dominant. 2 is conservative — a single stray chunk
# from an unrelated episode that happens to mention "Jurassic Park"
# should not trigger isolation.
MIN_EPISODE_CHUNKS = 2

# source_type values that represent episode-scoped content
EPISODE_SOURCE_TYPES = {"multiverse_episode"}

# source_type values that represent the multi-episode reference document.
# These are the contamination source when a dominant episode is present.
REFERENCE_SOURCE_TYPES = {"lore_bible"}

# lore_tier values that are episode-generic and safe to keep regardless
SAFE_TIERS = {"tall_tale"}


@dataclass
class EpisodeContext:
    """
    Episode identity surfaced by the isolation pass.

    Passed to the factual pass and fact_critique so they can evaluate
    'Is this the right episode?' rather than only 'Is this valid lore?'

    Attributes
    ----------
    post_id : int | None
        The WordPress post ID of the dominant episode. None if no dominant
        episode was found.
    title : str
        Human-readable episode title for use in prompts.
    episode_isolated : bool
        True if contaminating chunks were removed. False if chunks were
        returned unmodified (no dominant episode, or no contamination).
    chunks_removed : int
        Number of lore_bible/reference chunks removed from the context.
    dominant_chunk_count : int
        Number of episode-specific chunks retained for the dominant episode.
    """
    post_id: int | None = None
    title: str = ""
    episode_isolated: bool = False
    chunks_removed: int = 0
    dominant_chunk_count: int = 0
    all_post_ids: list[int] = field(default_factory=list)

    def to_prompt_block(self) -> str:
        """
        Render episode context as a prompt instruction block.

        Inserted into the factual pass prompt and fact_critique rubric
        when episode_isolated is True.
        """
        if not self.episode_isolated or self.post_id is None:
            return ""

        return (
            f"## Episode Scope\n\n"
            f"This question is specifically about the following TechBear "
            f"Multiverse episode:\n\n"
            f"  Title: {self.title}\n"
            f"  Post ID: {self.post_id}\n\n"
            f"Answer using ONLY details from this episode. Do not reference "
            f"events, clients, or technical details from other Multiverse "
            f"episodes. If the retrieved context contains details from other "
            f"episodes, ignore them.\n"
        )

    def to_critique_block(self) -> str:
        """
        Render episode context as a critique rubric item.

        Inserted into the fact_critique lore prompt when episode_isolated
        is True. The critique model uses this to detect and flag cross-episode
        contamination that survived the retrieval filter.
        """
        if not self.episode_isolated or self.post_id is None:
            return ""

        return (
            f"## Episode Relevance Check\n\n"
            f"The question is specifically about this Multiverse episode:\n\n"
            f"  Title: {self.title}\n"
            f"  Post ID: {self.post_id}\n\n"
            f"Evaluate whether the draft answer addresses THIS episode "
            f"specifically. Flag as an accuracy failure if the answer:\n"
            f"  - References events from a different Multiverse episode\n"
            f"  - Substitutes a different client, location, or incident\n"
            f"  - Gives a generic 'TechBear does tech support in fiction' "
            f"answer without episode-specific details\n\n"
            f"A correct answer should include at least one detail that is "
            f"unique to this episode (client name, specific incident, "
            f"location, or resolution).\n"
        )

    def to_dict(self) -> dict:
        """Serialise for result artifact storage."""
        return {
            "post_id": self.post_id,
            "title": self.title,
            "episode_isolated": self.episode_isolated,
            "chunks_removed": self.chunks_removed,
            "dominant_chunk_count": self.dominant_chunk_count,
            "all_post_ids": self.all_post_ids,
        }


def _get_post_id(chunk: dict) -> int | None:
    """Extract post_id from chunk metadata. Returns None if absent."""
    return chunk.get("meta", {}).get("post_id")


def _get_source_type(chunk: dict) -> str:
    """Extract source_type from chunk metadata. Returns empty string if absent."""
    return chunk.get("meta", {}).get("source_type", "")


def _get_lore_tier(chunk: dict) -> str:
    """Extract lore_tier from chunk metadata."""
    return chunk.get("meta", {}).get("lore_tier", "")


def _get_title(chunk: dict) -> str:
    """Extract title from chunk metadata."""
    return chunk.get("meta", {}).get("title", "")


def _find_dominant_episode(
    lore_chunks: list[dict],
) -> tuple[int | None, str, int]:
    """
    Identify the dominant episode among retrieved lore chunks.

    Counts multiverse_episode chunks per post_id. If the highest count
    meets MIN_EPISODE_CHUNKS, that post_id is dominant.

    Returns
    -------
    (dominant_post_id, title, count)
        dominant_post_id is None if no episode meets the threshold.
        title is the episode title from the dominant chunks.
        count is the number of chunks for the dominant episode.
    """
    episode_counts: dict[int, int] = {}
    episode_titles: dict[int, str] = {}

    for chunk in lore_chunks:
        if _get_source_type(chunk) not in EPISODE_SOURCE_TYPES:
            continue
        post_id = _get_post_id(chunk)
        if post_id is None:
            continue
        episode_counts[post_id] = episode_counts.get(post_id, 0) + 1
        if post_id not in episode_titles:
            episode_titles[post_id] = _get_title(chunk)

    if not episode_counts:
        return None, "", 0

    dominant_post_id = max(episode_counts, key=lambda k: episode_counts[k])
    dominant_count = episode_counts[dominant_post_id]

    if dominant_count < MIN_EPISODE_CHUNKS:
        return None, "", 0

    return (
        dominant_post_id,
        episode_titles.get(dominant_post_id, ""),
        dominant_count,
    )


def isolate_episode_chunks(
    lore_chunks: list[dict],
) -> tuple[list[dict], EpisodeContext]:
    """
    Post-process lore retrieval results to remove cross-episode contamination.

    Parameters
    ----------
    lore_chunks : list[dict]
        Raw lore chunks from ChromaDB. Each chunk is a dict with
        'text' and 'meta' keys, as returned by the retrieval phase.

    Returns
    -------
    (filtered_chunks, episode_context)
        filtered_chunks : list[dict]
            Chunks safe to pass to the factual pass. Reference chunks that
            would introduce cross-episode contamination are removed.
            Ordering is preserved (ChromaDB similarity rank maintained).
        episode_context : EpisodeContext
            Episode identity and isolation metadata. Pass to factual pass
            prompt and fact_critique phase.

    Notes
    -----
    If no dominant episode is found, returns the original list unmodified
    and an EpisodeContext with episode_isolated=False. The pipeline
    behaviour for non-episode lore questions is unchanged.
    """
    if not lore_chunks:
        return lore_chunks, EpisodeContext()

    all_post_ids = [
        _get_post_id(c)
        for c in lore_chunks
        if _get_source_type(c) in EPISODE_SOURCE_TYPES
        and _get_post_id(c) is not None
    ]

    dominant_post_id, title, dominant_count = _find_dominant_episode(
        lore_chunks)

    if dominant_post_id is None:
        # No dominant episode — return unmodified, no isolation
        return lore_chunks, EpisodeContext(
            all_post_ids=list(
                set(pid for pid in all_post_ids if pid is not None))
        )

    # Filter: keep episode chunks for the dominant post_id,
    # keep safe-tier chunks (tall_tale), remove lore_bible reference chunks.
    filtered: list[dict] = []
    removed = 0

    for chunk in lore_chunks:
        source_type = _get_source_type(chunk)
        lore_tier = _get_lore_tier(chunk)
        post_id = _get_post_id(chunk)

        if source_type in EPISODE_SOURCE_TYPES:
            if post_id == dominant_post_id:
                # Correct episode — always keep
                filtered.append(chunk)
            else:
                # Different episode chunk — remove to prevent contamination
                removed += 1

        elif source_type in REFERENCE_SOURCE_TYPES:
            # lore_bible chunks summarise multiple episodes.
            # Remove when a dominant episode is present — these are the
            # primary contamination source.
            removed += 1

        elif lore_tier in SAFE_TIERS:
            # tall_tale chunks are episode-generic mythology — safe to keep
            filtered.append(chunk)

        else:
            # Unknown source type — keep conservatively, don't remove
            filtered.append(chunk)

    unique_post_ids = list(set(pid for pid in all_post_ids if pid is not None))

    context = EpisodeContext(
        post_id=dominant_post_id,
        title=title,
        episode_isolated=True,
        chunks_removed=removed,
        dominant_chunk_count=dominant_count,
        all_post_ids=unique_post_ids,
    )

    return filtered, context
