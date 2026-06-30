"""
TechBear Pipeline — log_reader.py

Human-readable display utility for structured pipeline log files.

Parses the structured text format produced by logging_config.PipelineFormatter
and renders it as a filterable, color-coded terminal display.

Usage:
    # Read most recent log file
    python -m backend.scripts.log_reader

    # Read a specific file
    python -m backend.scripts.log_reader --file logs/pipeline_20260630_130000.log

    # Filter by phase
    python -m backend.scripts.log_reader --phase moderation
    python -m backend.scripts.log_reader --phase fact_critique

    # Filter by level
    python -m backend.scripts.log_reader --level WARNING
    python -m backend.scripts.log_reader --level DEBUG

    # Filter by question ID (matches any field containing the value)
    python -m backend.scripts.log_reader --question lore_004

    # Filter by arbitrary field value (grep-style)
    python -m backend.scripts.log_reader --grep retrieval_mode=lore
    python -m backend.scripts.log_reader --grep retry=

    # Combine filters
    python -m backend.scripts.log_reader --phase fact_critique --level WARNING

    # Show only pipeline phase transitions (INFO level orchestrator lines)
    python -m backend.scripts.log_reader --phase orchestrator --level INFO

    # Tail mode — follow a live log file
    python -m backend.scripts.log_reader --tail

    # Summary mode — count lines per phase and level, no body
    python -m backend.scripts.log_reader --summary
"""

import argparse
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# ANSI color codes — graceful fallback on non-color terminals
# ---------------------------------------------------------------------------


def _supports_color() -> bool:
    """Return True if the terminal supports ANSI color codes."""
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


USE_COLOR = _supports_color()


class Color:
    """ANSI color codes for terminal output."""
    RESET = "\033[0m" if USE_COLOR else ""
    BOLD = "\033[1m" if USE_COLOR else ""
    DIM = "\033[2m" if USE_COLOR else ""
    RED = "\033[31m" if USE_COLOR else ""
    YELLOW = "\033[33m" if USE_COLOR else ""
    GREEN = "\033[32m" if USE_COLOR else ""
    CYAN = "\033[36m" if USE_COLOR else ""
    BLUE = "\033[34m" if USE_COLOR else ""
    MAGENTA = "\033[35m" if USE_COLOR else ""
    WHITE = "\033[37m" if USE_COLOR else ""


LEVEL_COLORS = {
    "DEBUG":    Color.DIM,
    "INFO":     Color.GREEN,
    "WARNING":  Color.YELLOW,
    "ERROR":    Color.RED,
    "CRITICAL": Color.RED + Color.BOLD,
}

PHASE_COLORS = {
    "moderation":         Color.CYAN,
    "factual_pass":       Color.BLUE,
    "fact_critique":      Color.MAGENTA,
    "voice_pass":         Color.GREEN,
    "character_critique": Color.YELLOW,
    "editorial_pass":     Color.CYAN,
    "editorial_critique": Color.MAGENTA,
    "orchestrator":       Color.WHITE + Color.BOLD,
    "logging_config":     Color.DIM,
}


# ---------------------------------------------------------------------------
# Log line parser
# ---------------------------------------------------------------------------

# Format: 2026-06-30 13:00:00 DEBUG    moderation | message text
_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
    r"\s+(?P<level>[A-Z]+)\s+"
    r"(?P<phase>\S+)\s+\|\s+"
    r"(?P<message>.+)$"
)

# Field pattern: key='value' or key=value (no quotes for bare values)
_FIELD_RE = re.compile(r"(\w+)=(?:'([^']*)'|(\S+))")


@dataclass
class LogLine:
    """
    Parsed representation of one structured pipeline log line.

    Produced by parsing lines in the format emitted by
    logging_config.PipelineFormatter:
        2026-06-30 13:00:00 DEBUG    moderation | key='value' key2=value2
    """
    timestamp: str
    level: str
    phase: str
    message: str
    fields: dict[str, str]
    raw: str

    @classmethod
    def parse(cls, raw: str) -> "LogLine | None":
        """Parse a raw log line. Returns None if line does not match format."""
        m = _LINE_RE.match(raw.rstrip())
        if not m:
            return None

        message = m.group("message")
        fields = {
            k: (v1 or v2)
            for k, v1, v2 in _FIELD_RE.findall(message)
        }

        return cls(
            timestamp=m.group("timestamp"),
            level=m.group("level"),
            phase=m.group("phase"),
            message=message,
            fields=fields,
            raw=raw.rstrip(),
        )

    def matches(
        self,
        phase: str | None = None,
        level: str | None = None,
        question: str | None = None,
        grep: str | None = None,
    ) -> bool:
        """Return True if this line passes all active filters."""
        if phase and self.phase != phase:
            return False
        if level and self.level != level:
            return False
        if question and question not in self.message:
            return False
        if grep and grep not in self.message:
            return False
        return True


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_line(line: LogLine, compact: bool = False) -> str:
    """Render a parsed log line for terminal display."""
    level_color = LEVEL_COLORS.get(line.level, "")
    phase_color = PHASE_COLORS.get(line.phase, Color.WHITE)

    ts = f"{Color.DIM}{line.timestamp}{Color.RESET}"
    level = f"{level_color}{line.level:<8}{Color.RESET}"
    phase = f"{phase_color}{line.phase}{Color.RESET}"

    if compact:
        # Compact: timestamp + level + phase only, truncate message
        msg = line.message[:80] + \
            "…" if len(line.message) > 80 else line.message
        return f"{ts} {level} {phase} | {msg}"

    # Full: highlight field keys in the message
    message = _FIELD_RE.sub(
        lambda m: (
            f"{Color.CYAN}{m.group(1)}{Color.RESET}"
            f"={Color.WHITE}{m.group(0)[len(m.group(1))+1:]}{Color.RESET}"
        ),
        line.message,
    )

    return f"{ts} {level} {phase} | {message}"


def _render_summary(lines: list[LogLine]) -> str:
    """Render a count summary grouped by phase and level."""
    phase_counts: dict[str, Counter] = {}
    for line in lines:
        phase_counts.setdefault(line.phase, Counter())
        phase_counts[line.phase][line.level] += 1

    rows = []
    rows.append(
        f"\n{Color.BOLD}Pipeline Log Summary — "
        f"{len(lines)} lines{Color.RESET}\n"
        + "─" * 60
    )

    for phase in sorted(phase_counts):
        counts = phase_counts[phase]
        total = sum(counts.values())
        phase_color = PHASE_COLORS.get(phase, Color.WHITE)
        count_parts = []
        for lvl in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            if lvl in counts:
                color = LEVEL_COLORS.get(lvl, "")
                count_parts.append(f"{color}{lvl}:{counts[lvl]}{Color.RESET}")
        rows.append(
            f"  {phase_color}{phase:<25}{Color.RESET} "
            f"{total:>4} lines  {' '.join(count_parts)}"
        )

    rows.append("─" * 60)
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def _find_latest_log(log_dir: str = "logs") -> Path | None:
    """Return the most recently modified log file in log_dir."""
    log_path = Path(log_dir)
    if not log_path.exists():
        return None
    files = sorted(log_path.glob("pipeline_*.log"),
                   key=lambda f: f.stat().st_mtime)
    return files[-1] if files else None


# ---------------------------------------------------------------------------
# Main reader
# ---------------------------------------------------------------------------

def read_log(
    file: Path,
    phase: str | None = None,
    level: str | None = None,
    question: str | None = None,
    grep: str | None = None,
    summary: bool = False,
    compact: bool = False,
) -> None:
    """Parse and display a log file with optional filters."""
    lines: list[LogLine] = []
    unparsed: list[str] = []

    with file.open(encoding="utf-8") as f:
        for raw in f:
            parsed = LogLine.parse(raw)
            if parsed:
                if parsed.matches(phase, level, question, grep):
                    lines.append(parsed)
            else:
                # Continuation lines (e.g. exception tracebacks)
                if raw.strip():
                    unparsed.append(raw.rstrip())

    if summary:
        print(_render_summary(lines))
        return

    if not lines:
        print(
            f"{Color.YELLOW}No log lines matched the active filters.{Color.RESET}",
            file=sys.stderr,
        )
        return

    for line in lines:
        print(_render_line(line, compact=compact))

    # Print any unparsed continuation lines (tracebacks etc.) at the end
    if unparsed and not summary:
        print(
            f"\n{Color.DIM}--- Unparsed lines (tracebacks / continuations) ---{Color.RESET}")
        for raw in unparsed:
            print(f"{Color.DIM}{raw}{Color.RESET}")


def tail_log(
    file: Path,
    phase: str | None = None,
    level: str | None = None,
    question: str | None = None,
    grep: str | None = None,
    poll_interval: float = 0.5,
) -> None:
    """
    Follow a log file in real time (tail -f style).

    Reads new lines as they are appended. Press Ctrl+C to stop.
    """
    print(
        f"{Color.DIM}Tailing {file} — Ctrl+C to stop{Color.RESET}",
        file=sys.stderr,
    )

    with file.open(encoding="utf-8") as f:
        # Seek to end so we only see new lines
        f.seek(0, 2)
        try:
            while True:
                raw = f.readline()
                if raw:
                    parsed = LogLine.parse(raw)
                    if parsed and parsed.matches(phase, level, question, grep):
                        print(_render_line(parsed))
                        sys.stdout.flush()
                else:
                    time.sleep(poll_interval)
        except KeyboardInterrupt:
            print(f"\n{Color.DIM}Tail stopped.{Color.RESET}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point for the log reader."""
    parser = argparse.ArgumentParser(
        description="Ask TechBear pipeline log reader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--file", "-f",
        type=Path,
        default=None,
        help="Log file to read. Defaults to most recent file in logs/.",
    )
    parser.add_argument(
        "--log-dir",
        default="logs",
        help="Directory to search for log files (default: logs/).",
    )
    parser.add_argument(
        "--phase", "-p",
        default=None,
        help="Filter by pipeline phase (e.g. moderation, fact_critique).",
    )
    parser.add_argument(
        "--level", "-l",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Filter by log level.",
    )
    parser.add_argument(
        "--question", "-q",
        default=None,
        help="Filter lines containing this question ID.",
    )
    parser.add_argument(
        "--grep", "-g",
        default=None,
        help="Filter lines containing this string (field=value style).",
    )
    parser.add_argument(
        "--summary", "-s",
        action="store_true",
        help="Show count summary by phase and level only.",
    )
    parser.add_argument(
        "--compact", "-c",
        action="store_true",
        help="Compact output — truncate long messages to 80 chars.",
    )
    parser.add_argument(
        "--tail", "-t",
        action="store_true",
        help="Follow the log file in real time (tail -f style).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available log files in log-dir and exit.",
    )

    args = parser.parse_args()
    log_file: Path | None = None

    # List mode
    if args.list:
        log_path = Path(args.log_dir)
        if not log_path.exists():
            print(f"Log directory '{args.log_dir}' does not exist.")
            return
        files = sorted(log_path.glob("pipeline_*.log"),
                       key=lambda f: f.stat().st_mtime)
        if not files:
            print(f"No pipeline log files found in '{args.log_dir}'.")
            return
        print(f"\nLog files in {args.log_dir}/:\n")
        for f in reversed(files):
            size_kb = f.stat().st_size / 1024
            print(f"  {f.name}  ({size_kb:.1f} KB)")
        return

    # Resolve file
    log_file = args.file
    if log_file is None:
        log_file = _find_latest_log(args.log_dir)
        if log_file is None:
            print(
                f"{Color.YELLOW}No pipeline log files found in '{args.log_dir}'. "
                f"Run with --file to specify a path.{Color.RESET}",
                file=sys.stderr,
            )
            sys.exit(1)
        print(
            f"{Color.DIM}Reading: {log_file}{Color.RESET}",
            file=sys.stderr,
        )

    if not log_file.exists():
        print(f"{Color.RED}File not found: {log_file}{Color.RESET}",
              file=sys.stderr)
        sys.exit(1)

    if args.tail:
        tail_log(
            log_file,
            phase=args.phase,
            level=args.level,
            question=args.question,
            grep=args.grep,
        )
    else:
        read_log(
            log_file,
            phase=args.phase,
            level=args.level,
            question=args.question,
            grep=args.grep,
            summary=args.summary,
            compact=args.compact,
        )


if __name__ == "__main__":
    main()
