"""Anti-lurk handlers and lifecycle jobs.

Phase 5 of the moderation system. Enforces:
  - new joiners must post in #home within INTRO_GRACE_HOURS or get removed
  - active members who go silent for SILENT_PING_DAYS get DM'd
  - if they remain silent SILENT_DEMOTE_DAYS more, they're demoted to library-only
  - demoted members who stay away INACTIVITY_REMOVE_DAYS get fully removed
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from telegram import Update, ChatPermissions, ChatMemberUpdated
from telegram.ext import (
    ContextTypes, ChatMemberHandler, CommandHandler, MessageHandler, filters,
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
            try:
                await repo.log_watch_action(
                    u["user_id"], "intro_kick",
                    f"Joined Floor at {u.get('joined_floor_at')} and never posted intro within {cfg.intro_grace_hours}h grace. Ban+unban (re-invitable).",
                    "py_bot",
                )
            except Exception as e:
                log.warning("log_watch_action failed for %s: %s", u["user_id"], e)
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
            try:
                await repo.log_watch_action(
                    u["user_id"], "long_inactivity_kick",
                    f"Demoted >= {cfg.inactivity_remove_days} days. Ban+unban (re-invitable).",
                    "py_bot",
                )
            except Exception as e:
                log.warning("log_watch_action failed for %s: %s", u["user_id"], e)
        except Exception as e:
            log.warning("inactivity removal failed for %s: %s", u["user_id"], e)


# --- admin commands -------------------------------------------------------

JOB_MAP = {
    "intro_kick":      _job_kick_intro_pending,
    "ping_silent":     _job_ping_silent,
    "demote_silent":   _job_demote_silent,
    "remove_inactive": _job_remove_long_inactive,
}


def _is_admin(user_id: int, cfg: Config) -> bool:
    return user_id in cfg.bootstrap_admin_ids


def _fmt_ts(ts) -> str:
    if not ts:
        return "—"
    if isinstance(ts, datetime):
        now = datetime.now(timezone.utc)
        delta = now - ts
        secs = int(delta.total_seconds())
        if secs < 0:
            return ts.strftime("%Y-%m-%d %H:%M") + " (future?)"
        if secs < 3600:
            ago = f"{secs // 60}m ago"
        elif secs < 86400:
            ago = f"{secs // 3600}h ago"
        else:
            ago = f"{secs // 86400}d ago"
        return ts.strftime("%Y-%m-%d %H:%M") + f" ({ago})"
    return str(ts)


async def cmd_lurk_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only. /lurk_status [user_id]
    Shows lifecycle state for the user (default: yourself).
    Can also be used as a reply: /lurk_status (replying to a message).
    """
    cfg: Config = ctx.bot_data["config"]
    if not _is_admin(update.effective_user.id, cfg):
        return

    # Resolve target user_id
    target_id = update.effective_user.id
    if update.message and update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
    else:
        parts = (update.message.text or "").split()
        if len(parts) > 1 and parts[1].lstrip("-").isdigit():
            target_id = int(parts[1])

    snap = await repo.get_lifecycle_snapshot(target_id)
    if not snap:
        await update.message.reply_text(f"no record for user_id {target_id}")
        return

    handle = ("@" + snap["username"]) if snap["username"] else (snap.get("first_name") or "—")
    text = (
        f"lifecycle :: {handle} ({snap['user_id']})\n"
        f"\n"
        f"current_tier:        {snap['current_tier']}\n"
        f"is_banned:           {snap['is_banned']}\n"
        f"strike_count:        {snap['strike_count']}\n"
        f"last_strike_at:      {_fmt_ts(snap['last_strike_at'])}\n"
        f"\n"
        f"accepted_protocols:  {_fmt_ts(snap['accepted_protocols_at'])}\n"
        f"certified:           {_fmt_ts(snap['certified_at'])}\n"
        f"\n"
        f"joined_floor:        {_fmt_ts(snap['joined_floor_at'])}\n"
        f"intro_completed:     {_fmt_ts(snap['intro_completed_at'])}\n"
        f"last_floor_msg:      {_fmt_ts(snap['last_floor_msg_at'])}\n"
        f"activity_pinged:     {_fmt_ts(snap['activity_pinged_at'])}\n"
        f"demoted_at:          {_fmt_ts(snap['demoted_at'])}\n"
    )
    await update.message.reply_text(text)


async def cmd_lurk_run(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only. /lurk_run <job_name>
    Force-executes one of the lifecycle jobs now.
      job_name ∈ {intro_kick, ping_silent, demote_silent, remove_inactive}
    """
    cfg: Config = ctx.bot_data["config"]
    if not _is_admin(update.effective_user.id, cfg):
        return

    parts = (update.message.text or "").split()
    if len(parts) < 2 or parts[1] not in JOB_MAP:
        await update.message.reply_text(
            "usage: /lurk_run <job>\n"
            "jobs: " + ", ".join(JOB_MAP.keys())
        )
        return

    job_name = parts[1]
    await update.message.reply_text(f"running {job_name}…")
    try:
        await JOB_MAP[job_name](ctx)
        await update.message.reply_text(f"✓ {job_name} done. check logs for details.")
    except Exception as e:
        log.exception("manual job run failed")
        await update.message.reply_text(f"✗ {job_name} failed: {e}")


async def cmd_lurk_backdate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only. /lurk_backdate <user_id> <field>=<value> [<field>=<value> ...]

    Push timestamps backwards on a user so the lifecycle jobs catch them.

    Fields:
      join_h=<hours>          how many hours ago they joined
      msg_d=<days>            how many days ago their last floor message was
      ping_d=<days>            how many days ago they were activity-pinged
      demote_d=<days>         how many days ago they were demoted
      clear_intro             erase intro_completed_at

    Example: /lurk_backdate 7721296153 join_h=48 clear_intro
    """
    cfg: Config = ctx.bot_data["config"]
    if not _is_admin(update.effective_user.id, cfg):
        return

    parts = (update.message.text or "").split()
    if len(parts) < 3:
        await update.message.reply_text(
            "usage: /lurk_backdate <user_id> <field>=<value> ...\n"
            "fields: join_h=<h>, msg_d=<d>, ping_d=<d>, demote_d=<d>, clear_intro"
        )
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await update.message.reply_text("user_id must be a number")
        return

    kwargs = {}
    for arg in parts[2:]:
        if arg == "clear_intro":
            kwargs["clear_intro"] = True
            continue
        if "=" not in arg:
            continue
        k, v = arg.split("=", 1)
        try:
            iv = int(v)
        except ValueError:
            continue
        if k == "join_h":
            kwargs["joined_floor_minus_hours"] = iv
        elif k == "msg_d":
            kwargs["last_floor_msg_minus_days"] = iv
        elif k == "ping_d":
            kwargs["activity_pinged_minus_days"] = iv
        elif k == "demote_d":
            kwargs["demoted_minus_days"] = iv

    if not kwargs:
        await update.message.reply_text("no recognized fields. see /lurk_backdate help.")
        return

    await repo.force_backdate(target_id, **kwargs)
    await update.message.reply_text(
        f"backdated user {target_id} with: {kwargs}\n"
        f"now run /lurk_status {target_id} to verify, or /lurk_run <job> to trigger action."
    )


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

    # Admin testing commands
    application.add_handler(CommandHandler("lurk_status",   cmd_lurk_status))
    application.add_handler(CommandHandler("lurk_run",      cmd_lurk_run))
    application.add_handler(CommandHandler("lurk_backdate", cmd_lurk_backdate))

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
