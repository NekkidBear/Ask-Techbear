"""
RAG Confirmation Sanity Check
"""

from collections.abc import Callable

from backend.scripts.benchmarking.pipeline import run_pipeline
from backend.services.rag.retriever import TechBearRetriever


TEST_QUESTION = "How do I reset a Linux password?"
MODEL = "llama3.1:8b"
HOST = "localhost"

PromptBuilder = Callable[[str], str]


def build_raw_prompt(question: str) -> str:
    """Return the raw question without system or RAG context."""
    return question


def build_prompt_only_prompt(question: str) -> str:
    """Return the question with a minimal tech-support instruction."""
    return (
        "Answer the following tech support question clearly and practically.\n\n"
        f"Question: {question}"
    )


def make_rag_facts_prompt_builder(retriever: TechBearRetriever) -> PromptBuilder:
    """Create a prompt builder that injects factual RAG context."""

    def build_prompt(question: str) -> str:
        facts = retriever.get_facts(question)
        return (
            "Use the following factual context to answer the tech support question.\n\n"
            f"{facts}\n\n"
            f"Question: {question}"
        )

    return build_prompt


def make_rag_full_prompt_builder(retriever: TechBearRetriever) -> PromptBuilder:
    """Create a prompt builder that injects fact and voice RAG context."""

    def build_prompt(question: str) -> str:
        facts = retriever.get_facts(question)
        voice = retriever.get_voice(question)
        return (
            "Use the following factual and voice context to answer the tech support question.\n\n"
            f"{facts}\n\n"
            f"{voice}\n\n"
            f"Question: {question}"
        )

    return build_prompt


def run_check() -> None:
    """Run a smoke test across benchmark prompt and RAG modes."""
    retriever = TechBearRetriever()

    modes: dict[str, PromptBuilder] = {
        "raw": build_raw_prompt,
        "prompt_only": build_prompt_only_prompt,
        "rag_facts": make_rag_facts_prompt_builder(retriever),
        "rag_full": make_rag_full_prompt_builder(retriever),
    }

    print("\n=== RAG SANITY CHECK START ===\n")

    for mode, prompt_builder in modes.items():
        print("\n----------------------------")
        print(f"MODE: {mode}")
        print("----------------------------\n")

        result = run_pipeline(
            question=TEST_QUESTION,
            model=MODEL,
            host=HOST,
            prompt_builder=prompt_builder,
        )

        print("RESPONSE:\n")
        print(result["response"])

        print("\nMETRICS:")
        print(f"- latency: {result['total_time_s']}s")
        print(f"- tokens: {result['tokens_generated']}")

    print("\n=== RAG SANITY CHECK COMPLETE ===\n")


if __name__ == "__main__":
    run_check()
