"""asyncpg connection pool singleton."""
from __future__ import annotations

import asyncpg
from typing import Optional

_pool: Optional[asyncpg.Pool] = None


async def init_pool(dsn: str) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=1,
            max_size=10,
            command_timeout=30,
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
