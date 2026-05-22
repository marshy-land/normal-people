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


async def has_active_captcha(user_id: int) -> bool:
    """True if the user has a non-expired captcha session in the DB.

    Used to recover from per-process state loss (Railway redeploys wipe
    ctx.user_data). The DB is the authoritative state.
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "select expires_at from np_captcha_sessions where user_id = $1",
            user_id,
        )
    if not row:
        return False
    return row["expires_at"] > datetime.now(timezone.utc)


# -- REFERRALS ---------------------------------------------------------------

async def attribute_referral(referred_user_id: int, ref_code: str) -> Optional[dict]:
    """Call the np_attribute_referral RPC. The function sets np_users.referred_by,
    writes an np_referrals row, and applies any HIGH-watchlist inheritance.

    Returns the RPC result row, or None if no rows were returned.
    Caller treats failures as best-effort — a missing/expired code must NOT
    block onboarding.
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "select success, referrer_chat_id, message from np_attribute_referral($1, $2)",
            referred_user_id, ref_code,
        )
    return dict(row) if row else None


async def inherit_high_from_referrer(user_id: int) -> Optional[dict]:
    """Walk up to 3 hops up the referral chain; if any ancestor is on
    np_watchlist with tier='HIGH', add this user too. Idempotent.

    Returns {inherited, source_chat_id, hop, reason} or None if no row.
    Caller treats failures as best-effort — must never block onboarding.
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "select inherited, source_chat_id, hop, reason from np_inherit_high_from_referrer($1)",
            user_id,
        )
    return dict(row) if row else None


async def get_or_create_ref_code(chat_id: int) -> str:
    """Return the user's ref_code, creating one (8-char hex) if missing.

    Idempotent via ON CONFLICT — races between two parallel /ref invocations
    by the same user resolve to the same row.
    """
    import secrets as _secrets
    import asyncpg as _asyncpg
    async with get_pool().acquire() as conn:
        existing = await conn.fetchval(
            "select ref_code from np_referral_codes where chat_id = $1",
            chat_id,
        )
        if existing:
            return existing
        # Generate a fresh 8-char hex code. Collision odds against ref_code
        # uniqueness are ~1 in 4.3B per attempt; retry up to 5 times. The
        # chat_id PK side is handled by the `on conflict (chat_id)` clause.
        for _ in range(5):
            code = _secrets.token_hex(4)  # 8 hex chars
            try:
                row = await conn.fetchrow(
                    """
                    insert into np_referral_codes (chat_id, ref_code)
                    values ($1, $2)
                    on conflict (chat_id) do nothing
                    returning ref_code
                    """,
                    chat_id, code,
                )
            except _asyncpg.UniqueViolationError:
                # ref_code collided with another user. Try again with new code.
                continue
            if row:
                return row["ref_code"]
            # chat_id row already existed (concurrent insert). Read winner.
            existing = await conn.fetchval(
                "select ref_code from np_referral_codes where chat_id = $1",
                chat_id,
            )
            if existing:
                return existing
        # Extremely unlikely path: 5 collisions in a row. Surface as error.
        raise RuntimeError(f"failed to allocate ref_code for chat_id={chat_id}")


async def issue_shop_token(chat_id: int, ttl_hours: int) -> tuple[str, datetime]:
    """Mint a one-time shop access token for a certified user.

    Inserts a row in np_shop_access_grants (status='pending') and returns
    (token, expires_at). The store backend consumes the token on first
    valid arrival and bumps status='consumed'.
    """
    import secrets as _secrets
    token = _secrets.token_urlsafe(24)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            insert into np_shop_access_grants (token, chat_id, expires_at)
            values ($1, $2, $3)
            """,
            token, chat_id, expires_at,
        )
    return token, expires_at


async def get_secret(name: str) -> Optional[str]:
    """Read a runtime config value from np_secrets. Caller treats missing as None."""
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "select value from np_secrets where name = $1",
            name,
        )
    return row["value"] if row else None


async def log_watch_action(user_id: int, action: str, reason: str, actor: str) -> None:
    """Append a row to np_watch_actions. Used by automated jobs so every
    bot-initiated state change (kick, ban, demote, mute) is auditable from
    SQL without needing Railway logs. Best-effort; failure is logged by
    the caller and must never block the action that triggered it.
    """
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            insert into np_watch_actions (chat_id, action, reason, actor)
            values ($1, $2, $3, $4)
            """,
            user_id, action, reason, actor,
        )


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


# -- AUTO-MOD ----------------------------------------------------------------

async def log_mod_action(
    user_id: int,
    chat_id: int,
    message_id: Optional[int],
    rule_code: str,
    severity: str,
    action_taken: str,
    message_excerpt: Optional[str],
) -> int:
    async with get_pool().acquire() as conn:
        return int(await conn.fetchval(
            """
            insert into np_mod_actions
                (user_id, chat_id, message_id, rule_code, severity, action_taken, message_excerpt)
            values ($1, $2, $3, $4, $5, $6, $7)
            returning id
            """,
            user_id, chat_id, message_id, rule_code, severity, action_taken, message_excerpt,
        ))


async def mark_mod_reviewed(action_id: int, reviewer_id: int, decision: str) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            update np_mod_actions
               set reviewed_at = now(),
                   reviewer_id = $1,
                   review_decision = $2
             where id = $3
            """,
            reviewer_id, decision, action_id,
        )


# -- ANTI-LURK lifecycle ----------------------------------------------------

async def mark_joined_floor(user_id: int) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            update np_users
               set joined_floor_at = coalesce(joined_floor_at, now()),
                   demoted_at = null,
                   activity_pinged_at = null
             where user_id = $1
            """,
            user_id,
        )


async def mark_intro_completed(user_id: int) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            "update np_users set intro_completed_at = now() where user_id = $1 and intro_completed_at is null",
            user_id,
        )


async def touch_last_floor_msg(user_id: int) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            "update np_users set last_floor_msg_at = now() where user_id = $1",
            user_id,
        )


async def get_intro_pending(grace_hours: int) -> list[dict]:
    """Users who joined the Floor more than `grace_hours` ago and haven't
    introduced AND haven't certified.

    The certified_at gate is critical: a certified user has passed the full
    /certify gauntlet, which IS the intro. Without this check the kicker
    removes legitimate certified members who never happened to set
    intro_completed_at — e.g. anyone certified before that column was used,
    or anyone whose intro tracking failed silently for any reason.
    """
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            select user_id, username, first_name, joined_floor_at
              from np_users
             where joined_floor_at is not null
               and intro_completed_at is null
               and certified_at is null
               and is_banned = false
               and joined_floor_at < now() - interval '{int(grace_hours)} hours'
            """,
        )
        return [dict(r) for r in rows]


async def get_silent_users_for_ping(silent_days: int) -> list[dict]:
    """Active members whose last message was >= silent_days ago, not yet pinged."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            select user_id, username, first_name, last_floor_msg_at
              from np_users
             where current_tier >= 2
               and is_banned = false
               and demoted_at is null
               and intro_completed_at is not null
               and activity_pinged_at is null
               and (
                 (last_floor_msg_at is not null and last_floor_msg_at < now() - interval '{int(silent_days)} days')
                 or (last_floor_msg_at is null and joined_floor_at < now() - interval '{int(silent_days)} days')
               )
            """,
        )
        return [dict(r) for r in rows]


async def mark_activity_pinged(user_id: int) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            "update np_users set activity_pinged_at = now() where user_id = $1",
            user_id,
        )


async def get_users_for_demotion(silent_days: int, ping_grace_days: int) -> list[dict]:
    """Pinged users who've still been silent. Demote them."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            select user_id, username, first_name
              from np_users
             where current_tier >= 2
               and is_banned = false
               and demoted_at is null
               and activity_pinged_at is not null
               and activity_pinged_at < now() - interval '{int(ping_grace_days)} days'
               and (
                 last_floor_msg_at is null
                 or last_floor_msg_at < activity_pinged_at
               )
            """,
        )
        return [dict(r) for r in rows]


async def mark_demoted(user_id: int) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            update np_users
               set demoted_at = now(),
                   current_tier = 1
             where user_id = $1
            """,
            user_id,
        )


async def get_users_for_inactivity_sweep(demoted_days: int) -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            select user_id, username, first_name
              from np_users
             where demoted_at is not null
               and demoted_at < now() - interval '{int(demoted_days)} days'
               and is_banned = false
            """,
        )
        return [dict(r) for r in rows]


# -- ANTI-LURK testing helpers ---------------------------------------------

async def get_lifecycle_snapshot(user_id: int) -> Optional[dict]:
    """Full lifecycle dump for diagnostics."""
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            select user_id, username, first_name, current_tier, is_banned,
                   joined_floor_at, intro_completed_at, last_floor_msg_at,
                   activity_pinged_at, demoted_at,
                   strike_count, last_strike_at,
                   accepted_protocols_at, certified_at,
                   created_at
              from np_users
             where user_id = $1
            """,
            user_id,
        )
        return dict(row) if row else None


async def force_backdate(
    user_id: int,
    *,
    joined_floor_minus_hours: Optional[int] = None,
    last_floor_msg_minus_days: Optional[int] = None,
    activity_pinged_minus_days: Optional[int] = None,
    demoted_minus_days: Optional[int] = None,
    clear_intro: bool = False,
) -> None:
    """Push timestamps backwards for testing the lifecycle jobs."""
    updates = []
    params: list = [user_id]
    if joined_floor_minus_hours is not None:
        updates.append(f"joined_floor_at = now() - interval '{int(joined_floor_minus_hours)} hours'")
    if last_floor_msg_minus_days is not None:
        updates.append(f"last_floor_msg_at = now() - interval '{int(last_floor_msg_minus_days)} days'")
    if activity_pinged_minus_days is not None:
        updates.append(f"activity_pinged_at = now() - interval '{int(activity_pinged_minus_days)} days'")
    if demoted_minus_days is not None:
        updates.append(f"demoted_at = now() - interval '{int(demoted_minus_days)} days'")
    if clear_intro:
        updates.append("intro_completed_at = null")
    if not updates:
        return
    sql = "update np_users set " + ", ".join(updates) + " where user_id = $1"
    async with get_pool().acquire() as conn:
        await conn.execute(sql, *params)
