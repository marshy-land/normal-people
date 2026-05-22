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
    if await repo.is_banned(u.id):
        await update.message.reply_text("you can't come back here")
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

    # All 3 agreed → full access
    u = q.from_user
    cfg: Config = ctx.bot_data["config"]
    await repo.mark_certified(u.id)
    try:
        invite = await issue_single_use_invite(
            bot=ctx.bot,
            chat_id=cfg.tier2_group_id,
            user_id=u.id,
            target_tier=2,
            ttl_seconds=cfg.invite_ttl_seconds,
        )
    except Exception as e:
        log.exception("tier2 invite failed")
        await q.edit_message_text(f"something went wrong issuing your invite: {e}")
        return

    ctx.user_data.pop("certify_step", None)
    await q.edit_message_text(
        "welcome in. you have your voice now\n\n"
        f"this link works once and only for the next {cfg.invite_ttl_seconds // 60} minutes\n"
        f"{invite}\n\n"
        "look around\n"
        "see what people are talking about\n"
        "add what you can\n\n"
        "help others. don't lie. treat everyone as your equal",
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


# --- Registration ----------------------------------------------------------

def register(application) -> None:
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("certify", cmd_certify))
    application.add_handler(CallbackQueryHandler(on_accept_protocols, pattern="^accept_protocols$"))
    application.add_handler(CallbackQueryHandler(on_reverify_accept, pattern="^reverify_accept$"))
    application.add_handler(CallbackQueryHandler(on_certify_button, pattern="^certify_(agree|deny)_\\d+$"))
    application.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        on_captcha_reply,
    ))
