"""
Post-processing + visualization for TechBear benchmark results.
"""

import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path


def load_results(csv_path: str) -> pd.DataFrame:
    return pd.read_csv(csv_path)


# ---------------------------------------------------------
# Basic aggregations
# ---------------------------------------------------------

def summarize(df: pd.DataFrame):
    return df.groupby(["model", "mode"]).agg(
        avg_latency=("latency_s", "mean"),
        avg_tokens=("tokens", "mean"),
        count=("response", "count")
    ).reset_index()


# ---------------------------------------------------------
# Visualization 1: Latency comparison
# ---------------------------------------------------------

def plot_latency(summary_df: pd.DataFrame):
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

def plot_tokens(summary_df: pd.DataFrame):
    pivot = summary_df.pivot(
        index="model", columns="mode", values="avg_tokens")

    pivot.plot(kind="bar", figsize=(12, 6))
    plt.title("Average Token Usage by Model and Mode")
    plt.ylabel("Tokens")
    plt.tight_layout()
    plt.show()


# ---------------------------------------------------------
# CLI entry
# ---------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)

    args = parser.parse_args()

    df = load_results(args.file)
    summary = summarize(df)

    print(summary)

    plot_latency(summary)
    plot_tokens(summary)
