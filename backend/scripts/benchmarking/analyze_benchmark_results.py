"""
Post-processing + visualization for TechBear benchmark results.
"""

import pandas as pd
import matplotlib.pyplot as plt


def load_results(csv_path: str) -> pd.DataFrame:
    """Load benchmark CSV into a DataFrame."""
    return pd.read_csv(csv_path)


# ---------------------------------------------------------
# Basic aggregations
# ---------------------------------------------------------

def summarize(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate latency, tokens, and response count by model and mode."""
    return df.groupby(["model", "mode"]).agg(
        avg_latency=("latency_s", "mean"),
        avg_tokens=("tokens", "mean"),
        count=("response", "count")
    ).reset_index()


# ---------------------------------------------------------
# Visualization 1: Latency comparison
# ---------------------------------------------------------

def plot_latency(summary_df: pd.DataFrame) -> None:
    """Bar chart of average latency by model and mode."""
    pivot = summary_df.pivot(
        index="model", columns="mode", values="avg_latency")

    pivot.plot(kind="bar", figsize=(12, 6))
    plt.title("Average Latency by Model and Mode")
    plt.ylabel("Seconds")
    plt.tight_layout()
    plt.show()


# ---------------------------------------------------------
# Visualization 2: Token usage
# ---------------------------------------------------------

def plot_tokens(summary_df: pd.DataFrame) -> None:
    """Bar chart of average token usage by model and mode."""
    pivot = summary_df.pivot(
        index="model", columns="mode", values="avg_tokens")

    pivot.plot(kind="bar", figsize=(12, 6))
    plt.title("Average Token Usage by Model and Mode")
    plt.ylabel("Tokens")
    plt.tight_layout()
    plt.show()


# ---------------------------------------------------------
# Programmatic entry point (called from benchmark_models.py)
# ---------------------------------------------------------

def run_analysis(csv_path: str) -> None:
    """Load results from csv_path, print summary, and render charts."""
    results_df = load_results(csv_path)
    summary = summarize(results_df)
    print(summary)
    plot_latency(summary)
    plot_tokens(summary)


# ---------------------------------------------------------
# CLI entry
# ---------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)

    args = parser.parse_args()

    results_df = load_results(args.file)
    summary = summarize(results_df)

    print(summary)

    plot_latency(summary)
    plot_tokens(summary)
