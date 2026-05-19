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
    "Welcome to normal people.\n\n"
    "This is a community for the betterment of everyone. "
    "Before you can come in, there are three simple agreements you need to accept."
)

THREE_AGREEMENTS = (
    "1. Everyone is equal. No one is above anyone else here.\n"
    "2. You will not harm. Not in your words, not in your actions, not in what you share.\n"
    "3. You are here to help others. This is a place to give, not just take.\n\n"
    "If you want a place here, you have to accept these. They are not negotiable."
)

REVERIFY_INTRO = (
    "You were put in read-only mode for breaking one of the three agreements. "
    "To get your voice back, agree to them again:"
)

# Three simple agreement prompts, one per step
CERTIFY_PROMPTS = [
    (
        "First agreement: everyone is equal.\n\n"
        "No gurus, no clout, no one above anyone. Every idea here is open to "
        "respectful peer review, including yours.\n\n"
        "Do you accept this?"
    ),
    (
        "Second agreement: you will not harm.\n\n"
        "Not with your words, not with what you share, not with bad information. "
        "If you don't know something for sure, say so.\n\n"
        "Do you accept this?"
    ),
    (
        "Third agreement: you are here to help others.\n\n"
        "This is a place to give, not just take. Share what you know honestly, "
        "and look out for the people around you.\n\n"
        "Do you accept this?"
    ),
]


# --- /start ----------------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    cfg: Config = ctx.bot_data["config"]

    await repo.upsert_user(u.id, u.username, u.first_name)
    if await repo.is_banned(u.id):
        await update.message.reply_text("You can't come back here.")
        return

    # Re-verification path: user was muted and needs to re-affirm
    user_row = await repo.get_user(u.id)
    if user_row and user_row.get("must_reverify"):
        ctx.user_data["state"] = "awaiting_reverify"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("I accept, again", callback_data="reverify_accept")
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
        "First, a quick check to make sure you're a person. "
        "Reply with the answer:\n\n"
        f"  {challenge.question}\n\n"
        f"You have {cfg.captcha_ttl_seconds // 60} minutes."
    )


# --- CAPTCHA reply ---------------------------------------------------------

async def on_captcha_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if ctx.user_data.get("state") != "awaiting_captcha":
        return
    u = update.effective_user
    submitted = update.message.text or ""

    ok, remaining = await repo.check_captcha(u.id, submitted)
    if not ok:
        if remaining > 0:
            await update.message.reply_text(
                f"That's not right. {remaining} attempts left. Or send /start to try a new question."
            )
        else:
            ctx.user_data.pop("state", None)
            await update.message.reply_text(
                "Too many wrong answers. Send /start to try again."
            )
        return

    ctx.user_data["state"] = "awaiting_manifesto"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("I accept", callback_data="accept_protocols")
    ]])
    await update.message.reply_text(
        "Good. Now, the three agreements:\n\n" + THREE_AGREEMENTS,
        reply_markup=kb,
    )


# --- Manifesto acceptance --------------------------------------------------

async def on_accept_protocols(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    u = q.from_user
    cfg: Config = ctx.bot_data["config"]

    if ctx.user_data.get("state") != "awaiting_manifesto":
        await q.edit_message_text("That offer has expired. Send /start to begin again.")
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
        await q.edit_message_text(f"Something went wrong issuing your invite: {e}")
        return

    ctx.user_data["state"] = "in_tier1"
    await q.edit_message_text(
        "Thank you. Here's your way in.\n\n"
        f"This link works once and only for the next {cfg.invite_ttl_seconds // 60} minutes:\n"
        f"{invite}\n\n"
        "It will take you to a quiet reading space. Look around. "
        "When you're ready to speak with everyone else, come back here and send /certify.",
        disable_web_page_preview=True,
    )


# --- /certify --------------------------------------------------------------

async def cmd_certify(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = await repo.get_user(u.id)
    if not user or not user.get("accepted_protocols_at"):
        await update.message.reply_text(
            "You need to send /start and accept the three agreements first."
        )
        return
    if user.get("certified_at"):
        await update.message.reply_text("You already have full access. You're good.")
        return

    ctx.user_data["certify_step"] = 0
    await _send_certify_prompt(update, ctx, step=0)


async def _send_certify_prompt(update, ctx, step: int) -> None:
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes",  callback_data=f"certify_agree_{step}"),
        InlineKeyboardButton("No",   callback_data=f"certify_deny_{step}"),
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
            "That's okay. You can still read everything in the quiet space. "
            "When you're ready, send /certify again."
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
        await q.edit_message_text(f"Something went wrong issuing your invite: {e}")
        return

    ctx.user_data.pop("certify_step", None)
    await q.edit_message_text(
        "Welcome in. You have full access now.\n\n"
        f"This link works once and only for the next {cfg.invite_ttl_seconds // 60} minutes:\n"
        f"{invite}\n\n"
        "Take a look at the topics, see what people are talking about, "
        "and add what you can. Help others. Don't lie. Treat everyone as your equal.",
        disable_web_page_preview=True,
    )


# --- Re-verification after a mute -----------------------------------------

async def on_reverify_accept(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    u = q.from_user
    cfg: Config = ctx.bot_data["config"]

    if ctx.user_data.get("state") != "awaiting_reverify":
        await q.edit_message_text("That offer has expired. Send /start to begin again.")
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
        "Thank you. You have your voice back. "
        "Go back to the group and keep helping."
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
