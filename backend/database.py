"""
database.py — Async PostgreSQL connection for Ask TechBear
Gymnarctos Studios LLC

Uses SQLAlchemy's async engine with asyncpg.
Connection string is loaded from .env via python-dotenv.

Future:
    Session/chat memory will likely use UUID conversation IDs
    when live TechBear chat is implemented.
"""

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.models import Base

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://localhost/ask_techbear"
)

# Create the async engine
# echo=True logs all SQL statements — useful for debugging,
# set to False in production
engine = create_async_engine(
    DATABASE_URL,
    echo=True,
)

# Session factory — use this to create database sessions
# expire_on_commit=False prevents attributes from expiring
# after a commit, which matters in async context
# pylint: disable=invalid-name
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
# pylint: enable=invalid-name


async def init_db():
    """
    Create all tables on startup if they don't exist.
    Called once when the FastAPI app starts.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """
    Dependency injector for FastAPI route handlers.
    Yields an async database session and ensures it's
    closed after the request completes.

    Usage in a route:
        async def my_route(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_context():
    """
    Context manager version for use outside of FastAPI routes
    (e.g. startup scripts, corpus ingestion, etc.)

    Usage:
        async with get_db_context() as db:
            result = await db.execute(...)
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
