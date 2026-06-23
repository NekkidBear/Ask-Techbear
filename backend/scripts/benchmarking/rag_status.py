"""
RAG Confirmation Sanity Check
"""

from backend.scripts.benchmarking.pipeline import run_pipeline
from backend.services.rag.retriever import TechBearRetriever


TEST_QUESTION = "How do I reset a Linux password?"


def run_check():

    retriever = TechBearRetriever()

    modes = [
        "raw",
        "prompt_only",
        "rag_facts",
        "rag_full"
    ]

    print("\n=== RAG SANITY CHECK START ===\n")

    for mode in modes:

        print("\n----------------------------")
        print(f"MODE: {mode}")
        print("----------------------------\n")

        result = run_pipeline(
            question=TEST_QUESTION,
            model="llama3.1:8b",
            host="localhost",
            mode=mode,
            retriever=retriever
        )

        print("RESPONSE:\n")
        print(result["response"])

        print("\nMETRICS:")
        print(f"- latency: {result['total_time_s']}s")
        print(f"- tokens: {result['tokens_generated']}")

    print("\n=== RAG SANITY CHECK COMPLETE ===\n")


if __name__ == "__main__":
    run_check()
