"""Anti-lurk handlers and lifecycle jobs.

Phase 5 of the moderation system. Enforces:
  - new joiners must post in #home within INTRO_GRACE_HOURS or get removed
  - active members who go silent for SILENT_PING_DAYS get DM'd
  - if they remain silent SILENT_DEMOTE_DAYS more, they're demoted to library-only
  - demoted members who stay away INACTIVITY_REMOVE_DAYS get fully removed
"""
from __future__ import annotations

import logging
from telegram import Update, ChatPermissions, ChatMemberUpdated
from telegram.ext import (
    ContextTypes, ChatMemberHandler, MessageHandler, filters,
    Application,
)

from ..config import Config
from ..db import repo

log = logging.getLogger(__name__)


# --- copy ------------------------------------------------------------------

INTRO_DM = (
    "welcome\n\n"
    "you have {hours} hours to introduce yourself in the #home topic\n\n"
    "tell us who you are\n"
    "what brought you here\n"
    "what you want to give\n\n"
    "if you don't post in #home within the window you will be removed\n"
    "you can always come back through /start"
)

ACTIVITY_PING_DM = (
    "are you still with us\n\n"
    "you haven't said anything in the floor for {days} days\n"
    "if you want to stay just post something within the next week\n"
    "no pressure on what — just a sign that you are here\n\n"
    "if we don't hear from you your access will be reduced to read-only\n"
    "you can always come back by sending /certify"
)


# --- handlers --------------------------------------------------------------

async def on_chat_member_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Fires whenever a user's chat membership status changes in the floor.

    Detect joins specifically: status transitions from 'left'/'kicked'/'restricted'
    to 'member'.
    """
    cfg: Config = ctx.bot_data["config"]
    update_obj: ChatMemberUpdated = update.chat_member or update.my_chat_member
    if not update_obj:
        return

    chat = update_obj.chat
    if chat.id != cfg.tier2_group_id:
        return

    old = update_obj.old_chat_member
    new = update_obj.new_chat_member
    user = new.user
    if user.is_bot:
        return

    old_status = old.status if old else None
    new_status = new.status if new else None

    became_member = (
        old_status in (None, "left", "kicked", "restricted")
        and new_status == "member"
    )

    if not became_member:
        return

    log.info("New floor join: user_id=%s (@%s)", user.id, user.username)

    # Ensure user exists in DB; record the floor join
    await repo.upsert_user(user.id, user.username, user.first_name)
    await repo.mark_joined_floor(user.id)

    # DM them with the intro instructions
    try:
        await ctx.bot.send_message(
            chat_id=user.id,
            text=INTRO_DM.format(hours=cfg.intro_grace_hours),
        )
    except Exception as e:
        log.warning("could not DM new joiner %s: %s", user.id, e)


async def on_home_topic_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Mark intro completed when a user posts in the #home topic for the first time."""
    cfg: Config = ctx.bot_data["config"]
    msg = update.effective_message
    if not msg:
        return
    chat = update.effective_chat
    user = msg.from_user
    if not chat or chat.id != cfg.tier2_group_id:
        return
    if not user or user.is_bot:
        return
    # Only count #home topic posts
    if msg.message_thread_id != cfg.home_topic_id:
        return

    await repo.mark_intro_completed(user.id)


# --- jobs ------------------------------------------------------------------

async def _job_kick_intro_pending(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = ctx.bot_data["config"]
    pending = await repo.get_intro_pending(cfg.intro_grace_hours)
    for u in pending:
        try:
            # ban + immediate unban = silent kick (user can re-join via fresh invite)
            await ctx.bot.ban_chat_member(cfg.tier2_group_id, u["user_id"])
            await ctx.bot.unban_chat_member(cfg.tier2_group_id, u["user_id"])
            log.info("kicked for no intro: user_id=%s (@%s)", u["user_id"], u.get("username"))
        except Exception as e:
            log.warning("kick failed for %s: %s", u["user_id"], e)


async def _job_ping_silent(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = ctx.bot_data["config"]
    candidates = await repo.get_silent_users_for_ping(cfg.silent_ping_days)
    for u in candidates:
        try:
            await ctx.bot.send_message(
                chat_id=u["user_id"],
                text=ACTIVITY_PING_DM.format(days=cfg.silent_ping_days),
            )
            await repo.mark_activity_pinged(u["user_id"])
            log.info("activity ping sent: user_id=%s (@%s)", u["user_id"], u.get("username"))
        except Exception as e:
            log.warning("ping failed for %s: %s", u["user_id"], e)
            # if we couldn't DM them (they blocked the bot or never started a convo),
            # still mark them so we don't keep retrying daily; demotion logic will catch them
            await repo.mark_activity_pinged(u["user_id"])


async def _job_demote_silent(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = ctx.bot_data["config"]
    targets = await repo.get_users_for_demotion(cfg.silent_ping_days, cfg.silent_demote_days)
    for u in targets:
        try:
            await ctx.bot.restrict_chat_member(
                chat_id=cfg.tier2_group_id,
                user_id=u["user_id"],
                permissions=ChatPermissions(can_send_messages=False),
            )
            await repo.mark_demoted(u["user_id"])
            log.info("demoted for inactivity: user_id=%s (@%s)", u["user_id"], u.get("username"))
        except Exception as e:
            log.warning("demote failed for %s: %s", u["user_id"], e)


async def _job_remove_long_inactive(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = ctx.bot_data["config"]
    targets = await repo.get_users_for_inactivity_sweep(cfg.inactivity_remove_days)
    for u in targets:
        try:
            await ctx.bot.ban_chat_member(cfg.tier2_group_id, u["user_id"])
            await ctx.bot.unban_chat_member(cfg.tier2_group_id, u["user_id"])
            log.info("removed for long inactivity: user_id=%s (@%s)", u["user_id"], u.get("username"))
        except Exception as e:
            log.warning("inactivity removal failed for %s: %s", u["user_id"], e)


# --- registration ----------------------------------------------------------

def register(application: Application) -> None:
    # Watch for membership changes (joins, leaves, demotions)
    application.add_handler(ChatMemberHandler(
        on_chat_member_update,
        ChatMemberHandler.CHAT_MEMBER,
    ))

    # Watch floor #home messages to mark intros completed
    application.add_handler(MessageHandler(
        (filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP)
        & ~filters.COMMAND
        & ~filters.StatusUpdate.ALL,
        on_home_topic_message,
    ))

    # Schedule lifecycle jobs
    jq = application.job_queue
    if jq is None:
        log.warning("JobQueue unavailable; anti-lurk jobs won't run")
        return

    # Hourly: check who missed their intro deadline
    jq.run_repeating(_job_kick_intro_pending, interval=3600, first=180, name="antilurk_intro_kick")
    # Daily: ping silent members
    jq.run_repeating(_job_ping_silent,        interval=86400, first=240, name="antilurk_ping_silent")
    # Daily: demote those who didn't respond
    jq.run_repeating(_job_demote_silent,      interval=86400, first=300, name="antilurk_demote_silent")
    # Weekly: full removal of long-demoted accounts
    jq.run_repeating(_job_remove_long_inactive, interval=604800, first=360, name="antilurk_remove_inactive")
