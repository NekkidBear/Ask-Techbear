"""
backend/scripts/lint_check.py
Ask TechBear — Gymnarctos Studios LLC

Runs flake8, pylint, and pyright across the backend, writes timestamped
reports to lint_output/, and prints a summary to stdout.

Usage (from repo root):
    python -m backend.scripts.lint_check
    python -m backend.scripts.lint_check --tool flake8
    python -m backend.scripts.lint_check --tool pylint
    python -m backend.scripts.lint_check --tool pyright
    python -m backend.scripts.lint_check --summary-only

Output:
    lint_output/lint_flake8_YYYYMMDD_HHMMSS.txt
    lint_output/lint_pylint_YYYYMMDD_HHMMSS.txt
    lint_output/lint_pyright_YYYYMMDD_HHMMSS.txt
    lint_output/lint_summary_YYYYMMDD_HHMMSS.txt

lint_output/ is gitignored — reports are not committed.
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# =============================================================
# Configuration
# =============================================================

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
OUTPUT_DIR = REPO_ROOT / "lint_output"

TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

# Directories to exclude from all tools
EXCLUDE_DIRS = [
    "backend/venv",
    "backend/__pycache__",
    "alembic",
]

# Known suppressible patterns — flagged here so triage is faster.
# These are genuine library false positives, not code quality issues.
# Add new entries when a suppression is intentionally added to source.
KNOWN_SUPPRESSIONS = [
    # SQLAlchemy func.now() — valid at runtime, not statically resolvable
    "func.now",
    # Alembic op.* — injected at migration runtime
    "alembic",
    # Broad exception in ingestion scripts — intentional degraded-mode pattern
    "broad-exception-caught",
    # pylint false positive on ChromaDB's dynamically typed returns
    "no-member",
]

TOOL_CONFIGS = {
    "flake8": {
        "cmd": [
            sys.executable, "-m", "flake8",
            "backend/",
            "--exclude", ",".join(EXCLUDE_DIRS),
            "--format", "%(path)s:%(row)d:%(col)d: %(code)s %(text)s",
        ],
        "output_file": OUTPUT_DIR / f"lint_flake8_{TIMESTAMP}.txt",
        # E=error, F=pyflakes, W=warning, C=complexity
        "error_codes": ["E", "F", "W", "C"],
        # Syntax errors and undefined names
        "fatal_codes": ["E9", "F8"],
    },
    "pylint": {
        "cmd": [
            sys.executable, "-m", "pylint",
            "backend/",
            "--ignore-paths", "|".join(EXCLUDE_DIRS),
            "--output-format", "text",
            "--score", "yes",
        ],
        "output_file": OUTPUT_DIR / f"lint_pylint_{TIMESTAMP}.txt",
        "error_codes": ["E", "W", "C", "R"],
        "fatal_codes": ["E"],
    },
    "pyright": {
        "cmd": [
            sys.executable, "-m", "pyright",
            "backend/"
        ],
        "output_file": OUTPUT_DIR / f"lint_pyright_{TIMESTAMP}.txt",
        "error_codes": ["error", "warning", "information"],
        "fatal_codes": ["error"],
    },
}


# =============================================================
# Runner
# =============================================================

def run_tool(name: str, config: dict, summary_only: bool) -> dict:
    """
    Run a single lint tool as a subprocess.
    Returns a result dict with counts and output path.
    """
    cmd = config["cmd"]
    output_file = config["output_file"]

    if not summary_only:
        print(f"\n{'=' * 60}")
        print(f"Running {name}...")
        print(f"{'=' * 60}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        output = result.stdout + result.stderr
    except FileNotFoundError:
        output = f"{name} not found — install with: pip install {name} --break-system-packages\n"
        return {
            "name": name,
            "available": False,
            "output": output,
            "output_file": None,
            "error_count": 0,
            "warning_count": 0,
            "fatal_count": 0,
            "score": None,
        }

    # Write full output to file
    OUTPUT_DIR.mkdir(exist_ok=True)
    output_file.write_text(output, encoding="utf-8")

    if not summary_only:
        print(output[:3000])
        if len(output) > 3000:
            print(f"... (truncated — see {output_file.name} for full output)")

    # Parse counts
    error_count = 0
    warning_count = 0
    fatal_count = 0
    score = None

    lines = output.splitlines()

    if name == "flake8":
        for line in lines:
            if not line.strip():
                continue
            # Format: path:row:col: CODE message
            match = re.search(r":\s+([EWCF])(\d+)\s", line)
            if match:
                code_type = match.group(1)
                code_num = match.group(1) + match.group(2)
                if code_type in ("E", "F"):
                    error_count += 1
                    if any(code_num.startswith(f) for f in config["fatal_codes"]):
                        fatal_count += 1
                elif code_type in ("W", "C"):
                    warning_count += 1

    elif name == "pylint":
        for line in lines:
            # Pylint score line
            score_match = re.search(
                r"Your code has been rated at ([\d.]+)/10", line)
            if score_match:
                score = float(score_match.group(1))
            # Message lines: path:line:col: X#### (message-id) description
            msg_match = re.search(r":\d+:\d+: ([EWCRF])\d+", line)
            if msg_match:
                code_type = msg_match.group(1)
                if code_type == "E":
                    error_count += 1
                    fatal_count += 1
                elif code_type in ("W", "C", "R"):
                    warning_count += 1

    elif name == "pyright":
        for line in lines:
            if "error:" in line.lower():
                error_count += 1
                fatal_count += 1
            elif "warning:" in line.lower():
                warning_count += 1
        # Pyright summary line
        summary_match = re.search(
            r"(\d+) error[s]?, (\d+) warning[s]?", output
        )
        if summary_match:
            error_count = int(summary_match.group(1))
            warning_count = int(summary_match.group(2))
            fatal_count = error_count

    return {
        "name": name,
        "available": True,
        "output": output,
        "output_file": output_file,
        "error_count": error_count,
        "warning_count": warning_count,
        "fatal_count": fatal_count,
        "score": score,
    }


# =============================================================
# Summary
# =============================================================

def write_summary(results: list[dict]) -> Path:
    """Write a human-readable summary of all tool results."""
    lines = [
        "Ask TechBear — Lint Check Summary",
        f"Generated: {datetime.now().isoformat()}",
        "=" * 60,
        "",
    ]

    all_clean = True

    for r in results:
        if not r["available"]:
            lines.append(f"⚠  {r['name'].upper()}: not installed")
            lines.append("")
            continue

        status = "✅" if r["fatal_count"] == 0 else "❌"
        if r["fatal_count"] > 0:
            all_clean = False

        lines.append(f"{status} {r['name'].upper()}")
        lines.append(f"   Errors:   {r['error_count']}")
        lines.append(f"   Warnings: {r['warning_count']}")
        if r["score"] is not None:
            lines.append(f"   Score:    {r['score']}/10")
        if r["output_file"]:
            lines.append(f"   Report:   {r['output_file'].name}")
        lines.append("")

    lines.append("=" * 60)
    if all_clean:
        lines.append("✅ All tools passed with no fatal errors.")
    else:
        lines.append("❌ Fatal errors detected — review reports above.")

    lines.append("")
    lines.append(
        "Known suppression categories (verify before adding noqa/pylint: disable):")
    for s in KNOWN_SUPPRESSIONS:
        lines.append(f"  - {s}")

    summary_text = "\n".join(lines)

    OUTPUT_DIR.mkdir(exist_ok=True)
    summary_file = OUTPUT_DIR / f"lint_summary_{TIMESTAMP}.txt"
    summary_file.write_text(summary_text, encoding="utf-8")

    print("\n" + summary_text)
    return summary_file


# =============================================================
# Main
# =============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run lint checks across the Ask TechBear backend"
    )
    parser.add_argument(
        "--tool",
        choices=["flake8", "pylint", "pyright"],
        default=None,
        help="Run a single tool only (default: all)",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Suppress per-tool stdout output, show summary only",
    )
    args = parser.parse_args()

    tools = (
        {args.tool: TOOL_CONFIGS[args.tool]}
        if args.tool
        else TOOL_CONFIGS
    )

    OUTPUT_DIR.mkdir(exist_ok=True)
    print(f"Lint output directory: {OUTPUT_DIR}")
    print(f"Run timestamp: {TIMESTAMP}")

    results = []
    for name, config in tools.items():
        result = run_tool(name, config, args.summary_only)
        results.append(result)

    summary_file = write_summary(results)
    print(f"\nSummary written to: {summary_file}")

    # Exit non-zero if any fatal errors found
    if any(r["fatal_count"] > 0 for r in results if r["available"]):
        sys.exit(1)


if __name__ == "__main__":
    main()
