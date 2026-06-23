"""
TechBear Model Benchmark Orchestration Script (v2)
"""

import argparse
import csv
from datetime import datetime
from pathlib import Path

from data_loader import load_questions
from pipeline import run_pipeline


OUTPUT_DIR = Path("benchmark_results")
OUTPUT_DIR.mkdir(exist_ok=True)


MODES = [
    "raw",
    "prompt_only",
    "rag_facts",
    "rag_full"
]


def run_benchmark(host, model, questions, mode, retriever=None):

    results = []

    for i, item in enumerate(questions, 1):
        print(f"[{model} | {mode}] {i}/{len(questions)}")

        result = run_pipeline(
            question=item["question"],
            model=model,
            host=host,
            mode=mode,
            retriever=retriever
        )

        results.append({
            "id": item["id"],
            "attendee_name": item["attendee_name"],
            "question": item["question"],
            "model": model,
            "mode": mode,
            "response": result["response"],
            "latency_s": result["total_time_s"],
            "tokens": result["tokens_generated"]
        })

    return results


def write_csv(results, path):
    if not results:
        return

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--host", default="localhost")

    parser.add_argument(
        "--models",
        nargs="+",
        default=["qwen2.5:7b", "mistral:latest", "llama3.1:8b"]
    )

    parser.add_argument(
        "--modes",
        nargs="+",
        default=MODES
    )

    parser.add_argument("--limit", type=int, default=None)

    args = parser.parse_args()

    print("\nTechBear Benchmark v2 (RAG-READY)\n")

    questions = load_questions(limit=args.limit)
    print(f"Loaded {len(questions)} questions\n")

    all_results = []

    for model in args.models:
        for mode in args.modes:

            print(f"\n=== {model} | {mode} ===\n")

            results = run_benchmark(
                host=args.host,
                model=model,
                questions=questions,
                mode=mode,
                retriever=None  # plug in next step
            )

            all_results.extend(results)

            fname = OUTPUT_DIR / (
                f"v2_{model.replace(':', '_')}_{mode}_"
                f"{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            )

            write_csv(results, fname)
            print(f"Saved {fname}")

    combined = OUTPUT_DIR / (
        f"v2_combined_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    )

    write_csv(all_results, combined)

    print(f"\nDone. Combined: {combined}")


if __name__ == "__main__":
    main()
    