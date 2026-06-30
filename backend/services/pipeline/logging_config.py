"""
TechBear Async Pipeline — logging_config.py

Centralized logging configuration for all pipeline phases.

Usage (orchestrator and test harness call this once at startup):

    from backend.services.pipeline.logging_config import configure_logging
    configure_logging(verbosity="verbose")

Verbosity levels map to the modes defined in the v2.8 roadmap:

    standard  — stage progress only; minimal output; default for production
    summary   — one-line result per question + aggregate stats; no SQLAlchemy
    verbose   — full pipeline debug: retries, routing, retrieval diagnostics,
                raw moderation output, critique flags; SQLAlchemy suppressed
                unless explicitly enabled
    debug     — everything: full prompts, raw model responses, stack traces,
                SQLAlchemy engine chatter

Log format (Option B — structured text, human-readable, grep-parsable):

    2026-06-30 13:00:00 DEBUG    moderation | question='lore_004' retrieval_mode='lore' decision='pass'
    2026-06-30 13:00:01 INFO     orchestrator | phase='fact_critique' status='complete' score=8
    2026-06-30 13:00:02 WARNING  fact_critique | retry=1/2 accuracy=0→9 trigger='wrong_episode'

Log output:
    Console (stderr) — always
    File (logs/pipeline_{timestamp}.log) — when file_logging=True

The log file location is gitignored via logs/ directory pattern.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Verbosity level mapping
# ---------------------------------------------------------------------------

VERBOSITY_LEVELS = {
    "standard": logging.INFO,
    "summary":  logging.INFO,
    "verbose":  logging.DEBUG,
    "debug":    logging.DEBUG,
}

# SQLAlchemy engine loggers — suppressed in all modes except debug
_SQLALCHEMY_LOGGERS = (
    "sqlalchemy.engine",
    "sqlalchemy.engine.Engine",
    "sqlalchemy.pool",
    "sqlalchemy.dialects",
    "sqlalchemy.orm",
)

# Third-party loggers that tend to be chatty — suppressed below WARNING
# in all modes except debug
_SUPPRESS_BELOW_WARNING = (
    "httpx",
    "httpcore",
    "urllib3",
    "requests",
    "chromadb",
    "chromadb.segment",
    "chromadb.api",
)


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------

class PipelineFormatter(logging.Formatter):
    """
    Structured text formatter for pipeline log lines.

    Produces human-readable, grep-parsable output:
        2026-06-30 13:00:00 DEBUG    moderation | question='lore_004' decision='pass'

    The pipe separator ( | ) makes it easy to split on phase name:
        grep "moderation |" pipeline.log
        grep "retrieval_mode='lore'" pipeline.log
        grep "retry=" pipeline.log
    """

    LEVEL_WIDTH = 8  # pad level name to consistent width

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        level = record.levelname.ljust(self.LEVEL_WIDTH)

        # Module name: strip backend.services.pipeline prefix for brevity
        module = record.name.replace("backend.services.pipeline.", "")
        module = module.replace("backend.scripts.", "")

        message = record.getMessage()

        # Exception info appended on its own line if present
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            message = f"{message}\n{exc_text}"

        return f"{timestamp} {level} {module} | {message}"


# ---------------------------------------------------------------------------
# Public configuration entry point
# ---------------------------------------------------------------------------

def configure_logging(
    verbosity: str = "standard",
    file_logging: bool = False,
    log_dir: str = "logs",
) -> None:
    """
    Configure pipeline logging at startup.

    Call once from the orchestrator or test harness before any pipeline
    phases run. Subsequent calls are safe but will reconfigure handlers.

    Args:
        verbosity: One of "standard", "summary", "verbose", "debug".
                   Unknown values default to "standard" with a warning.
        file_logging: If True, write log lines to a timestamped file in
                      log_dir in addition to console output.
        log_dir: Directory for log files. Created if it does not exist.
                 Should be gitignored (logs/ is already in .gitignore pattern).
    """
    level = VERBOSITY_LEVELS.get(verbosity)
    if level is None:
        level = logging.INFO
        # Can't use logger here yet — print is intentional
        print(
            f"[logging_config] Unknown verbosity '{verbosity}' — "
            "defaulting to 'standard' (INFO)",
            file=sys.stderr,
        )

    formatter = PipelineFormatter()

    # --- Root logger ---
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    # Console handler (stderr — keeps stdout clean for pipeline output)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(formatter)
    root.addHandler(console)

    # File handler (optional)
    log_file: Path | None = None
    if file_logging:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = log_path / f"pipeline_{timestamp}.log"

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # --- SQLAlchemy suppression ---
    # Suppress in all modes except debug; SQLAlchemy engine logs are
    # extremely verbose and obscure pipeline output in normal use.
    sa_level = logging.DEBUG if verbosity == "debug" else logging.WARNING
    for sa_logger in _SQLALCHEMY_LOGGERS:
        logging.getLogger(sa_logger).setLevel(sa_level)

    # --- Third-party suppression ---
    # HTTP client and ChromaDB loggers suppressed below WARNING in all modes.
    # These fire on every Ollama call and every vector store query.
    tp_level = logging.DEBUG if verbosity == "debug" else logging.WARNING
    for tp_logger in _SUPPRESS_BELOW_WARNING:
        logging.getLogger(tp_logger).setLevel(tp_level)

    # Confirm configuration in verbose/debug modes
    if verbosity in ("verbose", "debug"):
        logging.getLogger(__name__).debug(
            "logging configured | verbosity=%r level=%s file_logging=%s",
            verbosity,
            logging.getLevelName(level),
            log_file if file_logging else "disabled",
        )


# ---------------------------------------------------------------------------
# Phase-level logger helper
# ---------------------------------------------------------------------------

def get_phase_logger(phase_name: str) -> logging.Logger:
    """
    Return a logger namespaced to a pipeline phase.

    Convenience wrapper so phases don't need to know the full module path.
    Typically called at module level in each phase file:

        from backend.services.pipeline.logging_config import get_phase_logger
        logger = get_phase_logger("moderation")

    Args:
        phase_name: Short phase identifier, e.g. "moderation", "fact_critique".

    Returns:
        Logger instance namespaced as backend.services.pipeline.{phase_name}.
    """
    return logging.getLogger(f"backend.services.pipeline.{phase_name}")
