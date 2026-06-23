"""
TechBear Model Benchmark Orchestration Script (v2.1)
- Now supports dynamic Ollama model discovery
"""

import argparse
import csv
from datetime import datetime
from pathlib import Path

import requests

from backend.scripts.benchmarking.data_loader import load_questions
from backend.scripts.benchmarking.pipeline import run_pipeline


OUTPUT_DIR = Path("benchmark_results")
OUTPUT_DIR.mkdir(exist_ok=True)


MODES = [
    "raw",
    "prompt_only",
    "rag_facts",
    "rag_full",
]


# =========================================================
# Model discovery (NEW)
# =========================================================

def list_ollama_models(host: str) -> list[str]:
    """Fetch installed Ollama models dynamically."""
    url = f"http://{host}:11434/api/tags"
    r = requests.get(url, timeout=5)
    r.raise_for_status()

    data = r.json()
    return [m["name"] for m in data.get("models", [])]


def filter_models(models: list[str]) -> list[str]:
    """
    Remove obviously non-generative or irrelevant models
    (e.g., embeddings-only models).
    """
    return [
        m for m in models
        if "embed" not in m.lower()
    ]


# =========================================================
# Benchmark runner
# =========================================================

def run_benchmark(host, model, questions, mode, retriever=None):
    results = []

    for i, item in enumerate(questions, 1):
        print(f"[{model} | {mode}] {i}/{len(questions)}")

        result = run_pipeline(
            question=item["question"],
            model=model,
            host=host,
            mode=mode,
            retriever=retriever,
        )

        results.append({
            "id": item["id"],
            "attendee_name": item["attendee_name"],
            "question": item["question"],
            "model": model,
            "mode": mode,
            "response": result["response"],
            "latency_s": result["total_time_s"],
            "tokens": result["tokens_generated"],
        })

    return results


def write_csv(results, path):
    if not results:
        return

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)


# =========================================================
# Main
# =========================================================

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--host", default="localhost")

    parser.add_argument(
        "--models",
        nargs="+",
        default=None,
        help="Models to benchmark. If omitted, uses all Ollama models."
    )

    parser.add_argument(
        "--modes",
        nargs="+",
        default=MODES,
        help="Benchmark modes to run."
    )

    parser.add_argument("--limit", type=int, default=None)

    args = parser.parse_args()

    print("\nTechBear Benchmark v2.1 (Dynamic Ollama Models)\n")

    questions = load_questions(limit=args.limit)
    print(f"Loaded {len(questions)} questions\n")

    # =====================================================
    # Model selection (UPDATED)
    # =====================================================

    if args.models:
        models = args.models
        print("Using CLI-provided models.")
    else:
        print("No --models provided. Discovering Ollama models...")
        models = list_ollama_models(args.host)
        models = filter_models(models)

    print(f"\nModels to benchmark: {models}\n")

    # =====================================================
    # Run benchmarks
    # =====================================================

    all_results = []

    for model in models:
        for mode in args.modes:

            print(f"\n=== {model} | {mode} ===\n")

            results = run_benchmark(
                host=args.host,
                model=model,
                questions=questions,
                mode=mode,
                retriever=None  # plug in RAG later
            )

            all_results.extend(results)

            fname = OUTPUT_DIR / (
                f"v2_1_{model.replace(':', '_')}_{mode}_"
                f"{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            )

            write_csv(results, fname)
            print(f"Saved {fname}")

    combined = OUTPUT_DIR / (
        f"v2_1_combined_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    )

    write_csv(all_results, combined)

    print(f"\nDone. Combined results: {combined}")


if __name__ == "__main__":
    main()
