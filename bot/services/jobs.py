"""Background jobs: strike decay, message log retention."""
from __future__ import annotations

import logging
from telegram.ext import Application, ContextTypes

from ..config import Config
from ..db import repo
from . import holds

log = logging.getLogger(__name__)


async def _job_decay_strikes(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = ctx.bot_data["config"]
    try:
        decayed = await repo.decay_old_strikes(cfg.strike_decay_days)
        if decayed:
            log.info("Decayed %d strikes (>%dd old)", decayed, cfg.strike_decay_days)
    except Exception as e:
        log.exception("strike decay failed: %s", e)


async def _job_prune_message_log(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        removed = await repo.prune_message_log(days=14)
        if removed:
            log.info("Pruned %d old message log rows", removed)
    except Exception as e:
        log.exception("prune failed: %s", e)


async def _job_reconcile_floor_membership(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Backstop for the chat_member event handler. Walks every np_users row
    marked current_tier=2 and asks Telegram whether they're still in The Floor.
    Anyone who's left/kicked/invalid is soft-downgraded via mark_left_floor.

    This is a SAFETY NET, not the primary path. The primary path is the
    chat_member handler in handlers/antilurk.py which decrements in real-time.
    This job catches drift caused by bot downtime, missed updates, or manual
    Telegram-side changes that didn't fire an event for whatever reason."""
    cfg: Config = ctx.bot_data["config"]
    try:
        ids = await repo.get_tier2_user_ids()
    except Exception:
        log.exception("reconcile: failed to load tier-2 ids")
        return
    if not ids:
        return
    log.info("reconcile: checking %d tier-2 users against Telegram", len(ids))

    present_statuses = {"creator", "administrator", "member"}
    downgraded = 0; errored = 0
    import asyncio
    for uid in ids:
        try:
            m = await ctx.bot.get_chat_member(cfg.tier2_group_id, uid)
            status = m.status
            # 'restricted' may still be in chat — trust ChatMemberRestricted.is_member
            is_present = status in present_statuses or (
                status == "restricted" and getattr(m, "is_member", True)
            )
            if not is_present:
                await repo.mark_left_floor(uid)
                downgraded += 1
        except Exception as e:
            # PARTICIPANT_ID_INVALID / user-deleted etc. = treat as absent.
            msg = str(e).lower()
            if "participant_id_invalid" in msg or "user not found" in msg or "user_id_invalid" in msg:
                try:
                    await repo.mark_left_floor(uid)
                    downgraded += 1
                except Exception:
                    log.exception("reconcile: mark_left_floor failed for %s", uid)
            else:
                errored += 1
                log.warning("reconcile: getChatMember failed for %s: %s", uid, e)
        # Pace under Telegram's 30/sec global. 80 calls @ 5/sec = 16s.
        await asyncio.sleep(0.2)

    if downgraded or errored:
        log.info("reconcile: downgraded=%d errored=%d (checked=%d)", downgraded, errored, len(ids))


def register(application: Application) -> None:
    jq = application.job_queue
    if jq is None:
        log.warning("JobQueue not available; install python-telegram-bot[job-queue]")
        return
    # Run daily; first run 60s after boot for a quick smoke check.
    jq.run_repeating(_job_decay_strikes,    interval=86400, first=60, name="decay_strikes")
    jq.run_repeating(_job_prune_message_log, interval=86400, first=120, name="prune_messages")
    # Hold-queue poller: deliver Tier-2 invites for users whose hold window
    # expired. Runs every 60s; first tick 15s after boot.
    jq.run_repeating(holds.deliver_due_holds_job, interval=60, first=15, name="deliver_due_holds")
    # Weekly Floor-membership reconcile. Backstop for the chat_member handler
    # in case the bot was offline during a leave/kick event.
    jq.run_repeating(_job_reconcile_floor_membership, interval=7 * 86400, first=600, name="reconcile_floor_membership")
