"""
TechBear Model Benchmark Orchestration Script (v2.2)

Changes:
- Removes ALL prompt duplication
- Uses character_loader.py as sole source of persona
- Adds analysis + matplotlib reporting hook
- Keeps raw CSV exports
- Supports dynamic Ollama model discovery
- Wires real RAG retrieval via TechBearRetriever
- Adds environment health check before benchmark run
"""

import argparse
import csv
from datetime import datetime
from pathlib import Path
import sys

import requests

from backend.scripts.benchmarking.data_loader import load_questions
from backend.scripts.benchmarking.pipeline import run_pipeline
from backend.scripts.character_loader import load_character_prompt
from backend.scripts.benchmarking.analyze_benchmark_results import run_analysis
from backend.services.rag.retriever import get_retriever
from backend.scripts.environment_health import run_all_checks


OUTPUT_DIR = Path("benchmark_results")
OUTPUT_DIR.mkdir(exist_ok=True)


MODES = [
    "raw",
    "prompt_only",
    "rag_facts",
    "rag_full",
]


# =========================================================
# MODEL DISCOVERY
# =========================================================

def list_ollama_models(host: str) -> list[str]:
    """Query local Ollama instance and return all available model names."""
    url = f"http://{host}:11434/api/tags"
    r = requests.get(url, timeout=10)
    r.raise_for_status()

    data = r.json()
    return [m["name"] for m in data.get("models", [])]


def filter_models(models: list[str]) -> list[str]:
    """Exclude embedding models from the benchmark run."""
    return [m for m in models if "embed" not in m.lower()]


# =========================================================
# PROMPT BUILDERS (ONLY PLACE CHARACTER IS USED)
# =========================================================

def build_prompt_builder(character_text: str, mode: str):
    """
    Returns a function(question) -> prompt.

    This isolates ALL persona injection to one place.
    RAG retrieval is performed live per question for rag_facts
    and rag_full modes.
    """

    def builder(question: str) -> str:

        if mode == "raw":
            return f"QUESTION:\n{question}"

        if mode == "prompt_only":
            return f"{character_text}\n\nQUESTION:\n{question}"

        if mode == "rag_facts":
            facts = get_retriever().get_facts(question)
            return (
                f"{character_text}\n\nFACTS CONTEXT:\n{facts}"
                f"\n\nQUESTION:\n{question}"
            )

        if mode == "rag_full":
            facts = get_retriever().get_facts(question)
            voice = get_retriever().get_voice(question)
            return (
                f"{character_text}\n\nFACTS CONTEXT:\n{facts}"
                f"\n\nVOICE CONTEXT:\n{voice}"
                f"\n\nQUESTION:\n{question}"
            )

        raise ValueError(f"Unknown mode: {mode}")

    return builder


# =========================================================
# BENCHMARK EXECUTION
# =========================================================

def run_benchmark(host: str, model: str, mode: str, questions: list, prompt_builder) -> list:
    """Run all questions through the pipeline for a given model and mode."""
    results = []

    for i, item in enumerate(questions, 1):
        print(f"[{model}] [{mode}] {i}/{len(questions)}")

        result = run_pipeline(
            question=item["question"],
            model=model,
            host=host,
            prompt_builder=prompt_builder,
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


def write_csv(results: list, path: Path) -> None:
    """Write a list of result dicts to a CSV file."""
    if not results:
        return

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)


# =========================================================
# MAIN
# =========================================================

def main() -> None:
    """Entry point for TechBear benchmark orchestration."""
    parser = argparse.ArgumentParser()

    parser.add_argument("--host", default="localhost")
    parser.add_argument("--models", nargs="+", default=None)
    parser.add_argument("--modes", nargs="+", default=MODES)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--skip-health",
        action="store_true",
        help="Skip environment health checks (not recommended)"
    )

    args = parser.parse_args()

    print("\nTechBear Benchmark v2.2 (Character-Decoupled)\n")

    # =====================================================
    # HEALTH CHECK
    # =====================================================

    if not args.skip_health:
        print("Running environment health checks...\n")
        passed = run_all_checks()
        if not passed:
            print(
                "Fix failing checks before benchmarking, "
                "or use --skip-health to bypass."
            )
            sys.exit(1)
    # =====================================================
    # SETUP
    # =====================================================

    questions = load_questions(limit=args.limit)
    print(f"Loaded {len(questions)} questions\n")

    character_text = load_character_prompt()

    if args.models:
        models = args.models
    else:
        print("Discovering Ollama models...")
        models = filter_models(list_ollama_models(args.host))

    print(f"Models: {models}\n")
    print(f"Modes: {args.modes}\n")

    all_results = []

    # =====================================================
    # RUN
    # =====================================================

    for model in models:
        for mode in args.modes:

            print(f"\n=== {model} | {mode} ===\n")

            prompt_builder = build_prompt_builder(character_text, mode)

            results = run_benchmark(
                host=args.host,
                model=model,
                mode=mode,
                questions=questions,
                prompt_builder=prompt_builder,
            )

            all_results.extend(results)

            fname = OUTPUT_DIR / (
                f"v2_2_{model.replace(':', '_')}_{mode}_"
                f"{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            )

            write_csv(results, fname)
            print(f"Saved {fname}")

    combined = OUTPUT_DIR / (
        f"v2_2_combined_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    )

    write_csv(all_results, combined)
    print(f"\nCSV Complete: {combined}")

    # =====================================================
    # ANALYSIS HOOK
    # =====================================================

    print("\nRunning analysis + charts...\n")
    run_analysis(str(combined))


if __name__ == "__main__":
    main()
