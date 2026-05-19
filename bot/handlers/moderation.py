"""Two-strike moderation.

Admin commands (reply to the offending message):
    /warn1   For causing harm
    /warn2   For dishonesty or careless information
    /warn3   For treating someone as less than equal

First strike: message deleted, 24h mute, public note, must re-accept the three
agreements to get voice back.
Second strike: removed from everywhere, recent messages cleaned up, no notice.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from telegram import Update, ChatPermissions
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from ..config import Config
from ..db import repo

log = logging.getLogger(__name__)

# Plain-language reasons aligned to the three agreements.
AGREEMENT_LABELS = {
    1: "causing harm",
    2: "being dishonest or careless with information",
    3: "treating someone as less than equal",
}


# --- helpers ---------------------------------------------------------------

async def _is_authorized_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    """Bootstrap admins always; otherwise must be a Telegram chat admin."""
    cfg: Config = ctx.bot_data["config"]
    u = update.effective_user
    if u.id in cfg.bootstrap_admin_ids:
        return True
    try:
        member = await ctx.bot.get_chat_member(update.effective_chat.id, u.id)
        return member.status in ("creator", "administrator")
    except Exception:
        return False


def _excerpt(text: str | None, limit: int = 200) -> str:
    if not text:
        return ""
    return (text[:limit] + "…") if len(text) > limit else text


# --- /warn handler factory -------------------------------------------------

def _make_warn_handler(protocol: int):
    async def handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        cfg: Config = ctx.bot_data["config"]
        msg = update.effective_message
        chat = update.effective_chat

        # Only operate in Tier 2 supergroup
        if chat.id != cfg.tier2_group_id:
            return

        if not msg.reply_to_message:
            try:
                await msg.reply_text("reply to the message you're warning about with /warn1, /warn2, or /warn3.")
            except Exception:
                pass
            return

        if not await _is_authorized_admin(update, ctx):
            log.info("non-admin %s tried /warn%d", update.effective_user.id, protocol)
            return

        offender = msg.reply_to_message.from_user
        if not offender or offender.is_bot:
            return

        offending = msg.reply_to_message
        excerpt = _excerpt(offending.text or offending.caption)

        # 1. Delete the offending message + the admin's /warn command
        try:
            await ctx.bot.delete_message(chat.id, offending.message_id)
        except Exception as e:
            log.warning("could not delete offending message: %s", e)
        try:
            await msg.delete()
        except Exception:
            pass

        # 2. Ensure offender exists in DB, then record strike
        await repo.upsert_user(offender.id, offender.username, offender.first_name)
        active_count = await repo.add_strike(
            user_id=offender.id,
            issued_by=update.effective_user.id,
            protocol=protocol,
            message_excerpt=excerpt,
            chat_id=chat.id,
        )

        if active_count == 1:
            await _strike_one(ctx, chat.id, offending.message_thread_id, offender, protocol, cfg)
        else:
            await _strike_two(ctx, chat.id, offender, cfg)

    return handler


# --- Strike 1: mute + cold log --------------------------------------------

async def _strike_one(ctx, chat_id: int, thread_id: int | None,
                      offender, protocol: int, cfg: Config) -> None:
    until = datetime.now(timezone.utc) + timedelta(seconds=cfg.strike_mute_seconds)
    try:
        await ctx.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=offender.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )
    except Exception as e:
        log.warning("restrict failed for %s: %s", offender.id, e)

    await repo.set_mute(offender.id, until)

    handle = f"@{offender.username}" if offender.username else (offender.first_name or "someone")
    log_text = (
        f"{handle}'s message was removed for {AGREEMENT_LABELS[protocol]}. "
        "they can read but not speak here for 24 hours. "
        "after that, they can come back if they agree to the three rules again."
    )
    try:
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=log_text,
            message_thread_id=thread_id,
        )
    except Exception as e:
        log.warning("could not post strike log: %s", e)


# --- Strike 2: global ban + scrub + sync ----------------------------------

async def _strike_two(ctx, chat_id: int, offender, cfg: Config) -> None:
    # 1. Ban from Tier 2
    try:
        await ctx.bot.ban_chat_member(chat_id=chat_id, user_id=offender.id)
    except Exception as e:
        log.warning("ban from Floor failed: %s", e)

    # 2. Scrub recent messages
    message_ids = await repo.get_user_recent_messages(offender.id, chat_id, hours=48)
    for mid in message_ids:
        try:
            await ctx.bot.delete_message(chat_id, mid)
        except Exception:
            continue

    # 3. Sync ban to Tier 1 Library
    try:
        await ctx.bot.ban_chat_member(chat_id=cfg.tier1_channel_id, user_id=offender.id)
    except Exception as e:
        log.warning("ban from Library failed: %s", e)

    # 4. Persist ban flag
    await repo.set_banned(offender.id)

    log.info("Strike 2: %s banned globally; scrubbed %d messages", offender.id, len(message_ids))
    # No public notification per spec.


# --- Message logger (for scrub) -------------------------------------------

async def on_floor_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Log every non-bot, non-service message posted in the Floor."""
    cfg: Config = ctx.bot_data["config"]
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat or chat.id != cfg.tier2_group_id:
        return
    user = msg.from_user
    if not user or user.is_bot:
        return
    try:
        await repo.log_message(chat.id, msg.message_id, user.id)
    except Exception as e:
        log.debug("log_message skipped: %s", e)


# --- Registration ----------------------------------------------------------

def register(application) -> None:
    application.add_handler(CommandHandler("warn1", _make_warn_handler(1)))
    application.add_handler(CommandHandler("warn2", _make_warn_handler(2)))
    application.add_handler(CommandHandler("warn3", _make_warn_handler(3)))

    # Log all non-command messages in groups so we can scrub later.
    application.add_handler(MessageHandler(
        (filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP)
        & ~filters.COMMAND
        & ~filters.StatusUpdate.ALL,
        on_floor_message,
    ))
