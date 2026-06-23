"""
Retriever wrapper for TechBear RAG system.

Converts ChromaDB query results into prompt-ready context blocks.
"""

import chromadb
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction


OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "nomic-embed-text"


class TechBearRetriever:

    def __init__(self, db_path="./chroma_db"):
        self._embed_fn = OllamaEmbeddingFunction(
            url=OLLAMA_EMBED_URL,
            model_name=EMBED_MODEL,
        )

        self.client = chromadb.PersistentClient(path=db_path)

        self.facts = self.client.get_collection(
            "techbear_facts",
            embedding_function=self._embed_fn
        )
        self.voice = self.client.get_collection(
            "techbear_voice",
            embedding_function=self._embed_fn
        )

    # -----------------------------------------------------
    # FACT RETRIEVAL
    # -----------------------------------------------------

    def get_facts(self, query, k=6):
        results = self.facts.query(
            query_texts=[query],
            n_results=k
        )

        docs = results.get("documents", [[]])[0]

        return self._format(docs, label="FACT")

    # -----------------------------------------------------
    # VOICE RETRIEVAL
    # -----------------------------------------------------

    def get_voice(self, query, k=4):
        results = self.voice.query(
            query_texts=[query],
            n_results=k
        )

        docs = results.get("documents", [[]])[0]

        return self._format(docs, label="VOICE")

    # -----------------------------------------------------
    # FORMATTER
    # -----------------------------------------------------

    def _format(self, docs, label):
        return "\n\n".join(
            f"[{label} {i+1}]\n{doc}"
            for i, doc in enumerate(docs)
        )