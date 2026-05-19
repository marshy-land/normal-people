"""Two-strike moderation + auto-mod layer.

Admin commands (reply to the offending message):
    /warn1   For causing harm
    /warn2   For dishonesty or careless information
    /warn3   For treating someone as less than equal

First strike: message deleted, 24h mute, public note, must re-accept the three
agreements to get voice back.
Second strike: removed from everywhere, recent messages cleaned up, no notice.

Auto-mod runs on every floor message before logging. Block-severity hits trigger
an immediate strike automatically. Flag-severity hits are sent to the mod review
channel (or admin DMs) for human decision.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters

from ..config import Config
from ..db import repo
from ..services import automod

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


# --- Message logger + auto-mod gate ---------------------------------------

async def on_floor_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Run auto-mod on every Floor message, then log it for scrub support."""
    cfg: Config = ctx.bot_data["config"]
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat or chat.id != cfg.tier2_group_id:
        return
    user = msg.from_user
    if not user or user.is_bot:
        return

    # Skip admins entirely — they can self-regulate.
    try:
        member = await ctx.bot.get_chat_member(chat.id, user.id)
        if member.status in ("creator", "administrator"):
            # still log for scrub support; just skip automod
            try:
                await repo.log_message(chat.id, msg.message_id, user.id)
            except Exception:
                pass
            return
    except Exception:
        pass

    text = msg.text or msg.caption or ""

    user_meta = automod.UserMeta(
        user_id=user.id,
        joined_chat_seconds_ago=None,  # could be populated from np_messages first-seen
        is_premium=getattr(user, "is_premium", False) or False,
        has_username=bool(user.username),
    )

    # First-seen approximation: if we've never logged a message from this user, treat as new.
    # Cheap query; we can optimize later.
    if text:
        prior_msgs = await repo.get_user_recent_messages(user.id, chat.id, hours=168)
        if not prior_msgs:
            # mark as ~30 minutes old so the new-account heuristic can fire
            user_meta = automod.UserMeta(
                user_id=user.id,
                joined_chat_seconds_ago=1800,
                is_premium=user_meta.is_premium,
                has_username=user_meta.has_username,
            )

    detection = automod.scan(text, user_meta)

    if detection:
        await _handle_detection(ctx, chat, msg, user, detection, text)
    else:
        # log only if we didn't already block-action it
        try:
            await repo.log_message(chat.id, msg.message_id, user.id)
        except Exception as e:
            log.debug("log_message skipped: %s", e)


async def _handle_detection(ctx, chat, msg, offender, detection, text: str) -> None:
    """Route a detection: block → auto-strike, flag → mod review."""
    cfg: Config = ctx.bot_data["config"]
    excerpt = automod.excerpt(text)

    if detection.severity == "block":
        # delete the offending message immediately
        try:
            await ctx.bot.delete_message(chat.id, msg.message_id)
        except Exception as e:
            log.warning("automod delete failed: %s", e)

        # ensure user exists, then apply a strike under "protocol 2 (harm/lies)"
        await repo.upsert_user(offender.id, offender.username, offender.first_name)
        active_count = await repo.add_strike(
            user_id=offender.id,
            issued_by=ctx.bot.id,                # bot is the issuer
            protocol=2,                          # default: "harm" bucket for automod blocks
            message_excerpt=excerpt,
            chat_id=chat.id,
        )
        await repo.log_mod_action(
            user_id=offender.id,
            chat_id=chat.id,
            message_id=msg.message_id,
            rule_code=detection.rule_code,
            severity="block",
            action_taken="deleted+strike",
            message_excerpt=excerpt,
        )

        if active_count == 1:
            await _strike_one(ctx, chat.id, msg.message_thread_id, offender, 2, cfg)
        else:
            await _strike_two(ctx, chat.id, offender, cfg)
        return

    # flag-severity → mod review only
    action_id = await repo.log_mod_action(
        user_id=offender.id,
        chat_id=chat.id,
        message_id=msg.message_id,
        rule_code=detection.rule_code,
        severity="flag",
        action_taken="flagged",
        message_excerpt=excerpt,
    )

    # still log the message so admins can see it / scrub it later
    try:
        await repo.log_message(chat.id, msg.message_id, offender.id)
    except Exception:
        pass

    # Build review notification
    handle = f"@{offender.username}" if offender.username else (offender.first_name or str(offender.id))
    review_text = (
        "automod flag\n\n"
        f"user: {handle}\n"
        f"rule: {detection.rule_code}\n"
        f"reason: {detection.reason}\n\n"
        f"message:\n{excerpt}"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("strike (delete + mute)", callback_data=f"modrev_strike_{action_id}_{chat.id}_{msg.message_id}_{offender.id}"),
        InlineKeyboardButton("dismiss",                 callback_data=f"modrev_dismiss_{action_id}"),
    ]])

    await _send_mod_review(ctx, review_text, kb)


async def _send_mod_review(ctx, text: str, keyboard) -> None:
    cfg: Config = ctx.bot_data["config"]
    if cfg.mod_review_chat_id:
        try:
            await ctx.bot.send_message(
                chat_id=cfg.mod_review_chat_id,
                text=text,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            return
        except Exception as e:
            log.warning("could not post to mod review chat: %s", e)

    # fallback: DM each bootstrap admin
    for admin_id in cfg.bootstrap_admin_ids:
        try:
            await ctx.bot.send_message(
                chat_id=admin_id,
                text=text,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except Exception as e:
            log.debug("could not DM admin %s: %s", admin_id, e)


# --- Mod review action buttons --------------------------------------------

async def on_mod_review_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the strike / dismiss buttons on automod flag notifications."""
    q = update.callback_query
    await q.answer()
    cfg: Config = ctx.bot_data["config"]
    reviewer_id = q.from_user.id

    if reviewer_id not in cfg.bootstrap_admin_ids:
        # also allow current chat admins to act
        try:
            member = await ctx.bot.get_chat_member(cfg.tier2_group_id, reviewer_id)
            if member.status not in ("creator", "administrator"):
                await q.answer("not authorized", show_alert=True)
                return
        except Exception:
            await q.answer("not authorized", show_alert=True)
            return

    parts = q.data.split("_")
    action = parts[1]

    if action == "dismiss":
        action_id = int(parts[2])
        await repo.mark_mod_reviewed(action_id, reviewer_id, "no_action")
        await q.edit_message_text(q.message.text + "\n\n— dismissed by admin")
        return

    if action == "strike":
        # modrev_strike_{action_id}_{chat_id}_{message_id}_{user_id}
        action_id  = int(parts[2])
        chat_id    = int(parts[3])
        message_id = int(parts[4])
        user_id    = int(parts[5])

        # delete the offending message
        try:
            await ctx.bot.delete_message(chat_id, message_id)
        except Exception as e:
            log.warning("delete on review-strike failed: %s", e)

        # apply strike
        active_count = await repo.add_strike(
            user_id=user_id, issued_by=reviewer_id,
            protocol=2, message_excerpt=None, chat_id=chat_id,
        )
        await repo.mark_mod_reviewed(action_id, reviewer_id, "uphold")

        # apply mute or escalate to ban
        # we need the offender object for the strike-1 helper. Fake-shape it.
        class _U:
            id = user_id
            username = None
            first_name = None
        if active_count == 1:
            await _strike_one(ctx, chat_id, None, _U, 2, cfg)
        else:
            await _strike_two(ctx, chat_id, _U, cfg)

        await q.edit_message_text(q.message.text + "\n\n— strike applied")


# --- /modtest -------------------------------------------------------------

async def cmd_modtest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only. Run automod against arbitrary text without taking action.

    Usage: /modtest <text to scan>
    Replies with the detection result (or 'clean').
    """
    cfg: Config = ctx.bot_data["config"]
    if update.effective_user.id not in cfg.bootstrap_admin_ids:
        return

    text = update.message.text or ""
    payload = text.split(maxsplit=1)
    if len(payload) < 2:
        await update.message.reply_text(
            "usage: /modtest <text>\nruns automod detectors against the text and reports what trips, without taking action."
        )
        return

    sample = payload[1]
    user_meta = automod.UserMeta(user_id=update.effective_user.id)
    detection = automod.scan(sample, user_meta)

    if not detection:
        await update.message.reply_text("clean. no detectors tripped.")
        return

    await update.message.reply_text(
        f"tripped: {detection.rule_code}\n"
        f"severity: {detection.severity}\n"
        f"reason: {detection.reason}"
    )


# --- Registration ----------------------------------------------------------

def register(application) -> None:
    application.add_handler(CommandHandler("warn1", _make_warn_handler(1)))
    application.add_handler(CommandHandler("warn2", _make_warn_handler(2)))
    application.add_handler(CommandHandler("warn3", _make_warn_handler(3)))
    application.add_handler(CommandHandler("modtest", cmd_modtest))

    # Mod review buttons
    application.add_handler(CallbackQueryHandler(
        on_mod_review_button,
        pattern=r"^modrev_(strike|dismiss)_\d+",
    ))

    # Log all non-command messages in groups so we can scrub later.
    application.add_handler(MessageHandler(
        (filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP)
        & ~filters.COMMAND
        & ~filters.StatusUpdate.ALL,
        on_floor_message,
    ))
