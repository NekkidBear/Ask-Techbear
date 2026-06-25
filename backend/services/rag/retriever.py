"""
Retriever wrapper for TechBear RAG system.

Converts ChromaDB query results into prompt-ready context blocks.
"""

import chromadb
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction


OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "nomic-embed-text"


class TechBearRetriever:
    """
    Retrieves fact and voice context from ChromaDB collections
    using Ollama embeddings for semantic search.
    """

    def __init__(self, db_path: str = "./chroma_db"):
        self._embed_fn = OllamaEmbeddingFunction(
            url=OLLAMA_EMBED_URL,
            model_name=EMBED_MODEL,
        )

        self.client = chromadb.PersistentClient(path=db_path)

        self.facts = self.client.get_or_create_collection(
            "techbear_facts",
            embedding_function=self._embed_fn  # type: ignore[arg-type]
        )
        self.voice = self.client.get_or_create_collection(
            "techbear_voice",
            embedding_function=self._embed_fn  # type: ignore[arg-type]
        )

    # -----------------------------------------------------
    # HEALTH
    # -----------------------------------------------------

    def collection_counts(self) -> dict[str, int]:
        """Return document counts for both collections."""
        return {
            "techbear_facts": self.facts.count(),
            "techbear_voice": self.voice.count(),
        }

    # -----------------------------------------------------
    # FACT RETRIEVAL
    # -----------------------------------------------------

    def get_facts(self, query: str, k: int = 6) -> str:
        """Retrieve and format fact chunks relevant to query."""
        results = self.facts.query(
            query_texts=[query],
            n_results=k
        )

        doc_lists = results.get("documents") or [[]]
        docs = doc_lists[0]

        return self._format(docs, label="FACT")

    # -----------------------------------------------------
    # VOICE RETRIEVAL
    # -----------------------------------------------------

    def get_voice(self, query: str, k: int = 4) -> str:
        """Retrieve and format voice example chunks relevant to query."""
        results = self.voice.query(
            query_texts=[query],
            n_results=k
        )

        doc_lists = results.get("documents") or [[]]
        docs = doc_lists[0]

        return self._format(docs, label="VOICE")

    # -----------------------------------------------------
    # FORMATTER
    # -----------------------------------------------------

    def _format(self, docs: list[str], label: str) -> str:
        """Format retrieved docs into numbered prompt blocks."""
        return "\n\n".join(
            f"[{label} {i+1}]\n{doc}"
            for i, doc in enumerate(docs)
        )


# =========================================================
# LAZY SINGLETON
# =========================================================

class _RetrieverSingleton:
    """Holds a single shared TechBearRetriever instance."""

    _instance: TechBearRetriever | None = None

    @classmethod
    def get(cls) -> TechBearRetriever:
        """Return shared TechBearRetriever, initializing if needed."""
        if cls._instance is None:
            cls._instance = TechBearRetriever()
        return cls._instance


def get_retriever() -> TechBearRetriever:
    """Return shared TechBearRetriever instance."""
    return _RetrieverSingleton.get()
