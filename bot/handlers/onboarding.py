"""Onboarding handlers: /start (entry) and /certify (full membership)."""
from __future__ import annotations

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
)

from ..config import Config
from ..db import repo
from ..services.captcha import generate_math_challenge
from ..services.invites import issue_single_use_invite
from ..services import holds

log = logging.getLogger(__name__)

# --- copy (plain language, no markdown) -----------------------------------

WELCOME = (
    "we are normal people\n\n"
    "there is no master class of human here\n"
    "no expert who knows your body better than you can learn to\n"
    "no founder whose vision you exist to serve\n\n"
    "the door is open. it is just clear about what you walk in agreeing to."
)

THREE_AGREEMENTS = (
    "1. you will treat every person here as your equal\n"
    "2. you will not harm\n"
    "3. you are here to help others\n\n"
    "that is all\n"
    "if you can do that there is a place for you"
)

REVERIFY_INTRO = (
    "you broke one of the three agreements\n"
    "to get your voice back agree to them again"
)

# three agreement prompts, one per step
CERTIFY_PROMPTS = [
    (
        "everyone is equal\n\n"
        "no guru no founder no priest with a private line to anything real\n"
        "every idea here is open to peer review including yours\n\n"
        "do you accept this"
    ),
    (
        "you will not harm\n\n"
        "not with your words not with your actions not with what you share\n"
        "a wrong number kills someone a wrong story ruins them\n"
        "if you don't know something for sure say so\n\n"
        "do you accept this"
    ),
    (
        "you are here to help others\n\n"
        "information food medicine shelter time attention\n"
        "when one of us learns something we share it\n"
        "when one of us is in trouble we help\n"
        "this is the only thing that has ever worked\n\n"
        "do you accept this"
    ),
]


# --- /start ----------------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    cfg: Config = ctx.bot_data["config"]

    await repo.upsert_user(u.id, u.username, u.first_name)
    # Insert-only identity snapshot used by the soft-boot detector. Safe to
    # call on every /start; no-ops once a snapshot exists.
    try:
        await holds.record_identity_snapshot(u.id, u.username, u.first_name)
    except Exception:
        log.exception("identity snapshot failed for user=%s", u.id)

    if await repo.is_banned(u.id):
        await update.message.reply_text("you can't come back here")
        return

    # If they're currently in a hold window, short-circuit with status instead
    # of restarting the gauntlet. Active hold survives /start.
    try:
        st = await holds.hold_status(u.id)
    except Exception:
        st = None
        log.exception("hold_status failed for user=%s", u.id)
    if st and st.get("in_hold"):
        secs = int(st.get("seconds_remaining") or 0)
        hours = secs // 3600
        mins  = (secs % 3600) // 60
        await update.message.reply_text(
            "you're in a hold window\n\n"
            f"remaining: {hours}h {mins}m\n"
            "your invite link arrives here automatically when the timer expires\n"
            "no action needed. messaging the bot doesn't change the timer"
        )
        return

    # ── Referral attribution ────────────────────────────────────────────
    # `/start <ref_code>` arrives as ctx.args=[ref_code]. Telegram delivers
    # the deep-link payload as the only token after /start. Best-effort:
    # attribution failures must NEVER block onboarding.
    if ctx.args:
        ref_code = ctx.args[0].strip()
        if ref_code:
            try:
                await repo.attribute_referral(u.id, ref_code)
            except Exception:
                log.exception("referral attribution failed for user=%s code=%s", u.id, ref_code)

            # ── HIGH-watchlist inheritance ───────────────────────────────
            # If any ancestor (≤3 hops up the referral chain) is on np_watchlist
            # with tier='HIGH', add this user too. DM admins on hit so a human
            # can review immediately. Best-effort: never block onboarding.
            try:
                inh = await repo.inherit_high_from_referrer(u.id)
                if inh and inh.get("inherited"):
                    src = inh.get("source_chat_id")
                    hop = inh.get("hop")
                    reason = inh.get("reason") or "-"
                    log.info(
                        "HIGH inherited: user=%s source=%s hop=%s reason=%s",
                        u.id, src, hop, reason,
                    )
                    handle = f"@{u.username}" if u.username else f"id:{u.id}"
                    name = (u.first_name or "").strip() or "-"
                    body = (
                        "⛓ HIGH inherited via referral\n"
                        f"user: {handle} ({name}) id={u.id}\n"
                        f"inherited from: {src} (hop {hop})\n"
                        f"ref_code used: {ref_code}\n"
                        f"reason: {reason}"
                    )
                    for admin_id in cfg.bootstrap_admin_ids:
                        try:
                            await ctx.bot.send_message(chat_id=admin_id, text=body)
                        except Exception:
                            log.warning("admin DM failed for admin_id=%s", admin_id)
            except Exception:
                log.exception("HIGH inheritance failed for user=%s", u.id)

    # Re-verification path: user was muted and needs to re-affirm
    user_row = await repo.get_user(u.id)
    if user_row and user_row.get("must_reverify"):
        ctx.user_data["state"] = "awaiting_reverify"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("i accept again", callback_data="reverify_accept")
        ]])
        await update.message.reply_text(
            REVERIFY_INTRO + "\n\n" + THREE_AGREEMENTS,
            reply_markup=kb,
        )
        return

    challenge = generate_math_challenge()
    await repo.set_captcha(u.id, challenge.answer, cfg.captcha_ttl_seconds)
    ctx.user_data["state"] = "awaiting_captcha"

    await update.message.reply_text(
        f"{WELCOME}\n\n"
        "first a quick check that you are a person\n"
        "reply with the answer\n\n"
        f"  {challenge.question}\n\n"
        f"you have {cfg.captcha_ttl_seconds // 60} minutes"
    )


# --- CAPTCHA reply ---------------------------------------------------------

async def on_captcha_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Free-text in DM. ctx.user_data is per-process and is wiped on every
    Railway redeploy, so we MUST NOT gate on the in-memory state alone.
    The DB has authoritative captcha state via np_captcha_sessions; reconstruct
    from it. If the user is mid-manifesto/certify, nudge them onto the right
    button instead of silently swallowing the message.
    """
    u = update.effective_user
    in_memory_state = ctx.user_data.get("state")
    submitted = update.message.text or ""

    # 1. If there is an active captcha session (DB-backed), treat this as
    #    a captcha answer regardless of in-memory state.
    if in_memory_state == "awaiting_captcha" or await repo.has_active_captcha(u.id):
        ok, remaining = await repo.check_captcha(u.id, submitted)
        if not ok:
            if remaining > 0:
                await update.message.reply_text(
                    f"that's not right. {remaining} attempts left. or send /start for a new question"
                )
            else:
                ctx.user_data.pop("state", None)
                await update.message.reply_text(
                    "too many wrong answers. send /start to try again"
                )
            return

        ctx.user_data["state"] = "awaiting_manifesto"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("i accept", callback_data="accept_protocols")
        ]])
        await update.message.reply_text(
            "good\n\nthe three agreements\n\n" + THREE_AGREEMENTS,
            reply_markup=kb,
        )
        return

    # 2. No active captcha. Recover state from DB so the user isn't stuck
    #    after a redeploy or after typing text instead of pressing a button.
    user_row = await repo.get_user(u.id)
    if not user_row:
        await update.message.reply_text("send /start to begin")
        return

    if user_row.get("must_reverify"):
        ctx.user_data["state"] = "awaiting_reverify"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("i accept again", callback_data="reverify_accept")
        ]])
        await update.message.reply_text(
            REVERIFY_INTRO + "\n\n" + THREE_AGREEMENTS,
            reply_markup=kb,
        )
        return

    if not user_row.get("accepted_protocols_at"):
        # User passed captcha previously but never tapped "i accept".
        ctx.user_data["state"] = "awaiting_manifesto"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("i accept", callback_data="accept_protocols")
        ]])
        await update.message.reply_text(
            "tap the button to continue\n\nthe three agreements\n\n" + THREE_AGREEMENTS,
            reply_markup=kb,
        )
        return

    if not user_row.get("certified_at"):
        # Read the library, then send /certify.
        await update.message.reply_text(
            "you have read-access. when you are ready to speak, send /certify here"
        )
        return

    # Fully certified — text in DM is nothing to act on.
    await update.message.reply_text(
        "you already have full access. talk in the group, not here"
    )


# --- Manifesto acceptance --------------------------------------------------

async def on_accept_protocols(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    u = q.from_user
    cfg: Config = ctx.bot_data["config"]

    if ctx.user_data.get("state") != "awaiting_manifesto":
        await q.edit_message_text("that has expired. send /start to begin again")
        return

    await repo.mark_protocols_accepted(u.id)
    try:
        invite = await issue_single_use_invite(
            bot=ctx.bot,
            chat_id=cfg.tier1_channel_id,
            user_id=u.id,
            target_tier=1,
            ttl_seconds=cfg.invite_ttl_seconds,
        )
    except Exception as e:
        log.exception("invite generation failed")
        await q.edit_message_text(f"something went wrong issuing your invite: {e}")
        return

    ctx.user_data["state"] = "in_tier1"
    await q.edit_message_text(
        "thank you. here is the way in\n\n"
        f"this link works once and only for the next {cfg.invite_ttl_seconds // 60} minutes\n"
        f"{invite}\n\n"
        "it will take you to a quiet reading space\n"
        "sit with what is there\n"
        "when you are ready to speak come back here and send /certify",
        disable_web_page_preview=True,
    )


# --- /certify --------------------------------------------------------------

async def cmd_certify(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = await repo.get_user(u.id)
    if not user or not user.get("accepted_protocols_at"):
        await update.message.reply_text(
            "you need to send /start and accept the three agreements first"
        )
        return
    if user.get("certified_at"):
        await update.message.reply_text("you already have full access. you are good")
        return

    ctx.user_data["certify_step"] = 0
    await _send_certify_prompt(update, ctx, step=0)


async def _send_certify_prompt(update, ctx, step: int) -> None:
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("yes", callback_data=f"certify_agree_{step}"),
        InlineKeyboardButton("no",  callback_data=f"certify_deny_{step}"),
    ]])
    text = CERTIFY_PROMPTS[step]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)


async def on_certify_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    parts = q.data.split("_")
    action, step = parts[1], int(parts[2])

    if action == "deny":
        ctx.user_data.pop("certify_step", None)
        await q.edit_message_text(
            "that is okay\n"
            "you can still read everything in the quiet space\n"
            "when you are ready send /certify again"
        )
        return

    next_step = step + 1
    if next_step < len(CERTIFY_PROMPTS):
        ctx.user_data["certify_step"] = next_step
        await _send_certify_prompt(update, ctx, step=next_step)
        return

    # All 3 agreed → certified, but entry to The Floor goes through a hold
    # window (default 6h; longer for soft-booted users — see services/holds.py).
    u = q.from_user
    cfg: Config = ctx.bot_data["config"]
    await repo.mark_certified(u.id)
    try:
        hold = await holds.enqueue_post_certify_hold(u.id, u.username, u.first_name)
    except Exception as e:
        log.exception("enqueue_post_certify_hold failed")
        await q.edit_message_text(f"something went wrong starting your hold: {e}")
        return

    hold_hours  = int(hold.get("hold_hours") or 6)
    hold_reason = hold.get("hold_reason") or "default"
    deliver_at  = hold.get("deliver_at")
    deliver_when = (
        deliver_at.strftime("%Y-%m-%d %H:%M UTC") if deliver_at is not None else "soon"
    )
    reason_blurb = {
        "default":                  "standard new-member trust window",
        "soft_boot_reentry":        "you were soft-booted previously",
        "soft_boot_identity_change":"you were soft-booted and your name has changed since",
    }.get(hold_reason, "trust window")

    ctx.user_data.pop("certify_step", None)
    await q.edit_message_text(
        "agreements logged\n\n"
        f"hold: {hold_hours} hours ({reason_blurb})\n"
        f"your invite link arrives here: {deliver_when}\n\n"
        "no action required\n"
        "messaging the bot doesn't change the timer\n"
        "send /status anytime to see the countdown",
        disable_web_page_preview=True,
    )


# --- Re-verification after a mute -----------------------------------------

async def on_reverify_accept(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    u = q.from_user
    cfg: Config = ctx.bot_data["config"]

    if ctx.user_data.get("state") != "awaiting_reverify":
        await q.edit_message_text("that has expired. send /start to begin again")
        return

    await repo.mark_protocols_accepted(u.id)  # clears must_reverify

    try:
        await ctx.bot.restrict_chat_member(
            chat_id=cfg.tier2_group_id,
            user_id=u.id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
    except Exception as e:
        log.warning("could not restore permissions for %s: %s", u.id, e)

    await repo.clear_mute(u.id)
    ctx.user_data.pop("state", None)
    await q.edit_message_text(
        "thank you. you have your voice back\n"
        "go back to the group and keep helping"
    )


# --- /ref ------------------------------------------------------------------

async def cmd_ref(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """DM-only. Tier-2 certified users get their personal referral deeplink.

    The link form is `https://t.me/<bot_username>?start=<ref_code>`. When a
    new arrival clicks it, Telegram delivers `/start <ref_code>` to the bot
    and cmd_start attributes the referral via np_attribute_referral.
    """
    if update.effective_chat.type != "private":
        return  # silently ignore in groups; this is a DM-only feature

    u = update.effective_user
    user_row = await repo.get_user(u.id)
    if not user_row or (user_row.get("current_tier") or 0) < 2:
        await update.message.reply_text(
            "you need to be in the floor before you can invite anyone. "
            "send /start, get through the airlock, and /certify first"
        )
        return

    try:
        code = await repo.get_or_create_ref_code(u.id)
    except Exception:
        log.exception("get_or_create_ref_code failed for user=%s", u.id)
        await update.message.reply_text(
            "something went wrong generating your invite. try again in a minute"
        )
        return

    me = await ctx.bot.get_me()
    link = f"https://t.me/{me.username}?start={code}"
    await update.message.reply_text(
        "here is your invite link\n\n"
        f"{link}\n\n"
        "share it with people you vouch for. each person who joins through it "
        "is attributed to you. no clout, no leaderboard. just a quiet way to "
        "grow the room with people you trust",
        disable_web_page_preview=True,
    )


# --- /shop -----------------------------------------------------------------

async def cmd_shop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """DM-only. Mints a one-time shop access token, sends the store URL with
    the token attached. Token lifetime = SHOP_SESSION_HOURS (defaults to 24).
    """
    if update.effective_chat.type != "private":
        return

    u = update.effective_user
    user_row = await repo.get_user(u.id)
    if not user_row or (user_row.get("current_tier") or 0) < 2:
        await update.message.reply_text(
            "the store is for floor members. send /start to begin if you haven't yet"
        )
        return

    store_url = await repo.get_secret("STORE_URL")
    if not store_url:
        log.error("STORE_URL missing from np_secrets")
        await update.message.reply_text(
            "the store is offline right now. try again later"
        )
        return

    ttl_raw = await repo.get_secret("SHOP_SESSION_HOURS")
    try:
        ttl_hours = int(ttl_raw) if ttl_raw else 24
    except (TypeError, ValueError):
        ttl_hours = 24

    try:
        token, expires_at = await repo.issue_shop_token(u.id, ttl_hours)
    except Exception:
        log.exception("issue_shop_token failed for user=%s", u.id)
        await update.message.reply_text(
            "something went wrong opening your session. try again in a minute"
        )
        return

    # Build URL with token. Append cleanly whether store_url already has a query.
    sep = "&" if "?" in store_url else "?"
    link = f"{store_url}{sep}t={token}"

    await update.message.reply_text(
        "here is your store session\n\n"
        f"{link}\n\n"
        f"the link is yours alone and lasts {ttl_hours} hours. "
        "if you close it just send /shop again",
        disable_web_page_preview=True,
    )


# --- /status ---------------------------------------------------------------

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the user's current hold countdown (or that they have none)."""
    if update.effective_chat.type != "private":
        return
    u = update.effective_user
    try:
        st = await holds.hold_status(u.id)
    except Exception:
        log.exception("hold_status failed for user=%s", u.id)
        await update.message.reply_text("couldn't read your status right now. try again in a moment")
        return
    if not st or not st.get("in_hold"):
        await update.message.reply_text(
            "no active hold\n"
            "if you haven't started yet send /start"
        )
        return
    secs   = int(st.get("seconds_remaining") or 0)
    hours  = secs // 3600
    mins   = (secs % 3600) // 60
    reason = st.get("hold_reason") or "trust window"
    deliver_at = st.get("deliver_at")
    deliver_when = deliver_at.strftime("%Y-%m-%d %H:%M UTC") if deliver_at else "soon"
    await update.message.reply_text(
        f"hold: {st.get('hold_hours')}h ({reason})\n"
        f"remaining: {hours}h {mins}m\n"
        f"invite arrives: {deliver_when}"
    )


# --- Registration ----------------------------------------------------------

def register(application) -> None:
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("certify", cmd_certify))
    application.add_handler(CommandHandler("ref", cmd_ref))
    application.add_handler(CommandHandler("shop", cmd_shop))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CallbackQueryHandler(on_accept_protocols, pattern="^accept_protocols$"))
    application.add_handler(CallbackQueryHandler(on_reverify_accept, pattern="^reverify_accept$"))
    application.add_handler(CallbackQueryHandler(on_certify_button, pattern="^certify_(agree|deny)_\\d+$"))
    application.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        on_captcha_reply,
    ))
