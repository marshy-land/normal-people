"""asyncpg connection pool singleton."""
from __future__ import annotations

import asyncpg
from typing import Optional

_pool: Optional[asyncpg.Pool] = None


async def init_pool(dsn: str) -> asyncpg.Pool:
    """Initialize the asyncpg pool.

    Disables prepared-statement caching so the pool works with Supabase's
    Transaction-mode pooler (PgBouncer, port 6543), which rotates underlying
    Postgres connections between transactions and can't preserve prepared
    statements. Safe with Session-mode pooler too — just gives up a small
    perf optimization.
    """
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=1,
            max_size=10,
            command_timeout=30,
            statement_cache_size=0,
        )
    return _pool


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized; call init_pool() first.")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
