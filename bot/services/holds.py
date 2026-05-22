"""Post-certify hold queue + identity-change soft-boot.

Design (Python-bot only, no fulfillment-hq dependency):

* Every certified user passes through a hold queue (np_hold_queue) before the
  Tier-2 invite is DMed. Default hold = 6h (HOLD_HOURS_DEFAULT).
* If a user is currently soft-booted (open np_soft_boots row), the hold is
  bumped to 24h (HOLD_HOURS_SOFT_BOOT). If their identity (username OR
  first_name) has changed since the boot, the hold escalates to 72h
  (HOLD_HOURS_SOFT_BOOT_ID_CHANGE).
* A soft-boot is triggered when a Floor member's username AND first_name BOTH
  differ from the np_identity_snapshots row captured at first /start.
* Soft-boots auto-clear when the user completes their hold and is delivered
  the Tier-2 invite (rehabilitation on completed hold).

The SQL layer (np_enqueue_hold, np_handle_identity_change, np_claim_due_holds,
np_mark_hold_delivered, np_record_identity_snapshot, np_hold_status) owns all
state transitions; this module is glue between the bot and those functions.
"""
from __future__ import annotations

import logging
from typing import Optional

from telegram import Bot
from telegram.ext import ContextTypes

from ..config import Config
from ..db import repo
from .invites import issue_single_use_invite

log = logging.getLogger(__name__)


# --- Public helpers used by handlers --------------------------------------

async def record_identity_snapshot(chat_id: int, username: Optional[str], first_name: Optional[str]) -> None:
    """Insert-only snapshot at first /start. No-op on duplicate."""
    await repo.np_record_identity_snapshot(chat_id, username, first_name)


async def enqueue_post_certify_hold(
    chat_id: int,
    username: Optional[str],
    first_name: Optional[str],
) -> dict:
    """Called when certify completes. Returns dict with deliver_at, hold_hours,
    hold_reason, was_existing. Caller DMs the user the hold message; cron-like
    poller delivers the actual invite link at deliver_at."""
    return await repo.np_enqueue_hold(chat_id, username, first_name)


async def check_and_handle_identity_change(
    bot: Bot,
    cfg: Config,
    chat_id: int,
    username: Optional[str],
    first_name: Optional[str],
) -> bool:
    """Called from Floor message handler. Returns True if a soft-boot was
    triggered (caller may also kick the user from The Floor)."""
    res = await repo.np_handle_identity_change(chat_id, username, first_name)
    if not res or not res.get("triggered"):
        return False

    # Trigger: open a soft_boot row was just created. Kick from Floor (ban+unban
    # so they can re-invite themselves via /start).
    try:
        await bot.ban_chat_member(cfg.tier2_group_id, chat_id)
        await bot.unban_chat_member(cfg.tier2_group_id, chat_id)
    except Exception as e:
        log.warning("soft-boot kick failed for chat_id=%s: %s", chat_id, e)

    # DM the user (best-effort)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "your username and display name both changed inside the floor\n\n"
                "we hold identity changes for 24h before you can re-enter\n"
                "send /start when you're ready to walk back in\n\n"
                "if you change your name again before re-entering, the hold becomes 72h"
            ),
        )
    except Exception as e:
        log.info("soft-boot DM failed for chat_id=%s: %s", chat_id, e)

    # Admin alert (best-effort)
    snap_user = res.get("username_snap")
    snap_name = res.get("first_name_snap")
    for admin_id in cfg.bootstrap_admin_ids:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=(
                    f"🪞 soft-boot: identity change\n"
                    f"chat_id: {chat_id}\n"
                    f"snapshot: @{snap_user or '-'} ({snap_name or '-'})\n"
                    f"now:      @{username or '-'} ({first_name or '-'})\n"
                    f"kicked from floor; 24h hold on re-entry"
                ),
            )
        except Exception:
            pass
    return True


async def hold_status(chat_id: int) -> Optional[dict]:
    """Return dict with in_hold, hold_reason, hold_hours, deliver_at,
    seconds_remaining, soft_booted. None if user has no row at all."""
    return await repo.np_hold_status(chat_id)


# --- Background poller ----------------------------------------------------

async def deliver_due_holds_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue tick: claim any rows whose deliver_at <= NOW(), issue a single-
    use Tier-2 invite, DM the user, mark delivered (which auto-clears any open
    soft_boot row). Best-effort per row — one failure does not block others."""
    cfg: Config = ctx.bot_data["config"]
    try:
        due = await repo.np_claim_due_holds(limit=20)
    except Exception as e:
        log.exception("np_claim_due_holds failed: %s", e)
        return
    if not due:
        return

    for row in due:
        hold_id = row["id"]
        chat_id = row["chat_id"]
        try:
            invite_url = await issue_single_use_invite(
                bot=ctx.bot,
                chat_id=cfg.tier2_group_id,
                user_id=chat_id,
                target_tier=2,
                ttl_seconds=cfg.invite_ttl_seconds,
            )
        except Exception as e:
            log.exception("invite generation failed for chat_id=%s: %s", chat_id, e)
            continue

        try:
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=(
                    "your hold window is up\n\n"
                    f"this link works once and only for the next {cfg.invite_ttl_seconds // 60} minutes\n"
                    f"{invite_url}\n\n"
                    "help others. don't lie. treat everyone as your equal"
                ),
                disable_web_page_preview=True,
            )
        except Exception as e:
            # User blocked the bot or deleted the chat. Mark delivered anyway
            # so we don't keep retrying forever; admins can re-issue manually.
            log.warning("hold-delivery DM failed for chat_id=%s: %s", chat_id, e)

        try:
            await repo.np_mark_hold_delivered(hold_id, invite_url)
        except Exception as e:
            log.exception("np_mark_hold_delivered failed for hold_id=%s: %s", hold_id, e)
