"""Data access layer. All SQL lives here."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from .pool import get_pool


# -- USERS -------------------------------------------------------------------

async def upsert_user(user_id: int, username: Optional[str], first_name: Optional[str]) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            insert into np_users (user_id, username, first_name)
            values ($1, $2, $3)
            on conflict (user_id) do update
              set username = excluded.username,
                  first_name = excluded.first_name
            """,
            user_id, username, first_name,
        )


async def get_user(user_id: int) -> Optional[dict]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("select * from np_users where user_id = $1", user_id)
        return dict(row) if row else None


async def mark_protocols_accepted(user_id: int) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            "update np_users set accepted_protocols_at = now(), current_tier = greatest(current_tier, 1) where user_id = $1",
            user_id,
        )


async def mark_certified(user_id: int) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            "update np_users set certified_at = now(), current_tier = greatest(current_tier, 2) where user_id = $1",
            user_id,
        )


async def is_banned(user_id: int) -> bool:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("select is_banned from np_users where user_id = $1", user_id)
        return bool(row and row["is_banned"])


# -- INVITE LINKS ------------------------------------------------------------

async def record_invite_link(link_id: str, user_id: int, target_tier: int, ttl_seconds: int) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            insert into np_invite_links (link_id, associated_user_id, target_tier, expires_at)
            values ($1, $2, $3, $4)
            """,
            link_id, user_id, target_tier, expires_at,
        )


async def mark_invite_used(link_id: str) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            "update np_invite_links set is_used = true, used_at = now() where link_id = $1",
            link_id,
        )


# -- CAPTCHA -----------------------------------------------------------------

async def set_captcha(user_id: int, answer: str, ttl_seconds: int) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            insert into np_captcha_sessions (user_id, answer, attempts, expires_at)
            values ($1, $2, 0, $3)
            on conflict (user_id) do update
              set answer = excluded.answer,
                  attempts = 0,
                  issued_at = now(),
                  expires_at = excluded.expires_at
            """,
            user_id, answer, expires_at,
        )


async def check_captcha(user_id: int, submitted: str) -> tuple[bool, int]:
    """Returns (is_correct, attempts_remaining). Deletes session on success."""
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "select answer, attempts, expires_at from np_captcha_sessions where user_id = $1",
            user_id,
        )
        if not row:
            return False, 0
        if row["expires_at"] < datetime.now(timezone.utc):
            await conn.execute("delete from np_captcha_sessions where user_id = $1", user_id)
            return False, 0
        if submitted.strip().lower() == row["answer"].strip().lower():
            await conn.execute("delete from np_captcha_sessions where user_id = $1", user_id)
            return True, 0
        new_attempts = row["attempts"] + 1
        remaining = max(0, 3 - new_attempts)
        if remaining == 0:
            await conn.execute("delete from np_captcha_sessions where user_id = $1", user_id)
        else:
            await conn.execute(
                "update np_captcha_sessions set attempts = $1 where user_id = $2",
                new_attempts, user_id,
            )
        return False, remaining


# -- STRIKES (Phase 3 hook — defined now, used later) -----------------------

async def add_strike(user_id: int, issued_by: int, protocol: int,
                     message_excerpt: Optional[str], chat_id: Optional[int]) -> int:
    """Returns total active strikes after insert."""
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                insert into np_strikes (user_id, issued_by, protocol, message_excerpt, chat_id)
                values ($1, $2, $3, $4, $5)
                """,
                user_id, issued_by, protocol, message_excerpt, chat_id,
            )
            count = await conn.fetchval(
                "select count(*) from np_strikes where user_id = $1 and decayed_at is null",
                user_id,
            )
            await conn.execute(
                "update np_users set strike_count = $1, last_strike_at = now() where user_id = $2",
                count, user_id,
            )
            return int(count)
