"""
TechBear Model Benchmark Orchestration Script.

This script executes controlled benchmarking experiments across multiple
language models hosted via Ollama. It evaluates performance across a
standardized question corpus.

Responsibilities:
- Load benchmark datasets
- Execute model inference across multiple LLMs
- Manage benchmark runs and persistence of results
- Provide reproducible CLI-driven execution
"""

import argparse
import csv
from datetime import datetime
from pathlib import Path

from data_loader import load_questions
from pipeline import ollama_generate, build_prompt, TECHBEAR_SYSTEM


OUTPUT_DIR = Path("benchmark_results")
OUTPUT_DIR.mkdir(exist_ok=True)


# -------------------------------------------------------------------
# Core benchmark runner
# -------------------------------------------------------------------

def run_benchmark(host, model, questions):
    """
    Runs benchmark evaluation for a single model over a question set.

    For each question:
    - Builds prompt via build_prompt()
    - Sends request to Ollama via ollama_generate()
    - Collects response and performance metrics

    Args:
        host (str): Ollama host address
        model (str): Model name to evaluate
        questions (list[dict]): Question dataset

    Returns:
        list[dict]: Benchmark results per question
    """
    results = []

    for i, item in enumerate(questions, 1):
        qid = item["id"]
        question = item["question"]

        print(f"[{i}/{len(questions)}] {question[:60]}...")

        prompt = build_prompt(question)

        result = ollama_generate(
            host=host,
            model=model,
            prompt=prompt,
            system=TECHBEAR_SYSTEM
        )

        results.append({
            "id": qid,
            "attendee_name": item["attendee_name"],
            "question": question,
            "model": model,
            "response": result["response"],
            "latency_s": result["total_time_s"],
            "tokens": result["tokens_generated"]
        })

    return results


# -------------------------------------------------------------------
# Output layer
# -------------------------------------------------------------------

def write_csv(results, path):
    """
    Writes benchmark results to a CSV file.

    Args:
        results (list[dict]): Benchmark output
        path (Path): Output file path
    """
    if not results:
        return

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)


# -------------------------------------------------------------------
# CLI entrypoint
# -------------------------------------------------------------------

def main():
    """
    Entry point for benchmark execution.

    - Parses CLI arguments
    - Loads dataset
    - Runs benchmarks across selected models
    - Writes per-model and combined outputs
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument(
        "--models",
        nargs="+",
        default=["qwen2.5:7b", "mistral:latest", "llama3.1:8b"]
    )
    parser.add_argument("--limit", type=int, default=None)

    args = parser.parse_args()

    print("\nTechBear Benchmark v1 (PURE MODEL MODE)\n")

    questions = load_questions(limit=args.limit)
    print(f"Loaded {len(questions)} questions\n")

    all_results = []

    for model in args.models:
        print(f"\nRunning model: {model}\n")

        results = run_benchmark(
            host=args.host,
            model=model,
            questions=questions
        )

        all_results.extend(results)

        fname = OUTPUT_DIR / (
            f"v1_{model.replace(':', '_')}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        )

        write_csv(results, fname)
        print(f"Saved {fname}")

    combined = OUTPUT_DIR / (
        f"v1_combined_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    )

    write_csv(all_results, combined)
    print(f"\nDone. Combined: {combined}")


if __name__ == "__main__":
    main()
