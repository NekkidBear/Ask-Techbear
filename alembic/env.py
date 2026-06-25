"""
alembic/env.py — Alembic migration environment
Ask TechBear — Gymnarctos Studios LLC

Configured for async SQLAlchemy (asyncpg) with a sync psycopg2
connection for migration execution. Alembic requires a synchronous
connection; we derive it from the same DATABASE_URL used by the app
by swapping the driver prefix.

Autogenerate support is enabled — Alembic compares the current DB
schema against the SQLAlchemy metadata and generates diffs.
"""
# pylint: disable=wrong-import-position,wrong-import-order,no-member,unused-import
# E1101 (no-member): alembic.context members are injected at runtime,
#   not statically defined — Pylint cannot introspect them.
# C0413 (wrong-import-position): sys.path.insert must precede backend
#   imports; this is intentional Alembic env.py boilerplate.
# W0611 (unused-import): model imports are intentional — they register
#   models with SQLAlchemy metadata for Alembic autogenerate diffing.

import os
import sys
from logging.config import fileConfig
from pathlib import Path

# Ensure the repo root is on sys.path so `backend` is importable
# when Alembic runs env.py outside of the normal application context.
# This must come before any backend.* imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import engine_from_config, pool  # noqa: E402

from alembic import context  # noqa: E402

# Load .env so DATABASE_URL is available
load_dotenv()

# Alembic Config object — provides access to alembic.ini values
config = context.config

# Configure Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import all models so autogenerate can detect schema changes.
# These imports are intentionally "unused" — they register the models
# with SQLAlchemy's metadata so Alembic can diff against them.
# Any new model file must be imported here to be tracked.
from backend.models import Base  # noqa: E402, F401
from backend.models_v26 import (  # noqa: E402, F401
    HumanReview,
    LLMScore,
    PipelineArtifact,
    PipelineRun,
    ReviewNote,
)

target_metadata = Base.metadata


def get_sync_url() -> str:
    """
    Derive a synchronous psycopg2 connection URL from the app's
    async asyncpg DATABASE_URL.

    The app uses:  postgresql+asyncpg://user:pass@host/db
    Alembic needs: postgresql+psycopg2://user:pass@host/db
    """
    url = os.getenv(
        "DATABASE_URL",
        "postgresql://localhost/ask_techbear",
    )
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode — generates SQL without a live
    DB connection. Useful for reviewing migrations before applying.

    Usage: alembic upgrade head --sql
    """
    url = get_sync_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode against a live DB connection.
    Uses psycopg2 (sync) since Alembic doesn't support async engines.
    """
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_sync_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
