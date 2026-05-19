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
            """
            update np_users
               set accepted_protocols_at = now(),
                   current_tier = greatest(current_tier, 1),
                   must_reverify = false
             where user_id = $1
            """,
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


async def set_banned(user_id: int) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            "update np_users set is_banned = true where user_id = $1",
            user_id,
        )


async def set_mute(user_id: int, until: datetime) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            "update np_users set muted_until = $1, must_reverify = true where user_id = $2",
            until, user_id,
        )


async def clear_mute(user_id: int) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            "update np_users set muted_until = null where user_id = $1",
            user_id,
        )


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


# -- STRIKES & MESSAGES ------------------------------------------------------

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


async def active_strike_count(user_id: int) -> int:
    async with get_pool().acquire() as conn:
        return int(await conn.fetchval(
            "select count(*) from np_strikes where user_id = $1 and decayed_at is null",
            user_id,
        ))


async def log_message(chat_id: int, message_id: int, user_id: int) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            insert into np_messages (chat_id, message_id, user_id)
            values ($1, $2, $3)
            on conflict do nothing
            """,
            chat_id, message_id, user_id,
        )


async def get_user_recent_messages(user_id: int, chat_id: int, hours: int = 48) -> list[int]:
    """Returns list of message_ids for scrubbing."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            select message_id from np_messages
             where user_id = $1
               and chat_id = $2
               and posted_at > now() - ($3 || ' hours')::interval
            """,
            user_id, chat_id, str(hours),
        )
        return [r["message_id"] for r in rows]


async def decay_old_strikes(days: int) -> int:
    """Mark strikes as decayed after N days of clean record. Returns count decayed."""
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            count = await conn.fetchval(
                f"""
                update np_strikes
                   set decayed_at = now()
                 where decayed_at is null
                   and issued_at < now() - interval '{int(days)} days'
                returning (select count(*) from np_strikes where decayed_at is null)
                """,
            )
            # Recalc strike_count per user
            await conn.execute(
                """
                update np_users u
                   set strike_count = (
                     select count(*) from np_strikes s
                      where s.user_id = u.user_id and s.decayed_at is null
                   )
                """
            )
            return int(count or 0)


async def prune_message_log(days: int = 14) -> int:
    """Delete np_messages rows older than N days. Returns rows deleted."""
    async with get_pool().acquire() as conn:
        return int(await conn.fetchval(
            f"with d as (delete from np_messages where posted_at < now() - interval '{int(days)} days' returning 1) select count(*) from d"
        ))
