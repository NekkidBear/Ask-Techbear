"""
scripts/seed_blocklist.py — One-time blocklist seeding script
Gymnarctos Studios LLC

Run with: python -m backend.scripts.seed_blocklist
"""

import asyncio

from backend.database import get_db_context
from backend.services.moderation import seed_default_blocklist


async def main():
    """Populate the database blocklist with the default moderation terms."""
    async with get_db_context() as db:
        await seed_default_blocklist(db)
    print("✅ Blocklist seeded successfully")


if __name__ == "__main__":
    asyncio.run(main())
