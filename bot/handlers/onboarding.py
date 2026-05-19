"""Phase 1 (Airlock) + Phase 2 (Behavioral Gate) handlers."""
from __future__ import annotations

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
)

from ..config import Config
from ..db import repo
from ..services.captcha import generate_math_challenge
from ..services.invites import issue_single_use_invite

log = logging.getLogger(__name__)

# --- copy ------------------------------------------------------------------

MANIFESTO = (
    "*normal people :: core protocols*\n\n"
    "`1.` *Help Others* — All contributions prioritize harm reduction, safety, "
    "and peer-to-peer survival logistics.\n"
    "`2.` *Do Not Lie* — Absolute data integrity. Exact measurements. "
    "Verifiable data is separated from personal theory.\n"
    "`3.` *We Are All Equals* — No gurus. No clout. Every protocol and theory "
    "is subject to peer review.\n\n"
    "Acceptance is binding."
)

CERTIFY_PROMPTS = [
    (
        "*Protocol 1 — Harm Reduction*\n\n"
        "This space operates on peer-to-peer survival and logistics. "
        "We do not post to flex; we post to inform.\n\n"
        "Do you agree to prioritize harm reduction and provide actionable, "
        "safe information to your peers?"
    ),
    (
        "*Protocol 2 — Data Integrity*\n\n"
        "Misinformation here carries real-world consequences. Ego has no place "
        "in the lab or the library.\n\n"
        "Do you commit to clearly separating verifiable data from personal "
        "theory, and to never misrepresent your experiences or sources?"
    ),
    (
        "*Protocol 3 — Radical Equality*\n\n"
        "There are no gurus in this group. Every protocol, source, and theory "
        "is subject to respectful peer review.\n\n"
        "Do you accept that you are an equal participant in a shared ecosystem, "
        "and leave all notions of internet clout at the door?"
    ),
]


# --- /start ----------------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    cfg: Config = ctx.bot_data["config"]

    await repo.upsert_user(u.id, u.username, u.first_name)
    if await repo.is_banned(u.id):
        await update.message.reply_text("Access denied.")
        return

    challenge = generate_math_challenge()
    await repo.set_captcha(u.id, challenge.answer, cfg.captcha_ttl_seconds)
    ctx.user_data["state"] = "awaiting_captcha"

    await update.message.reply_text(
        f"`normal people :: gateway`\n\n"
        f"Verify you are human. Reply with the answer:\n\n"
        f"  *{challenge.question}*\n\n"
        f"_Expires in {cfg.captcha_ttl_seconds // 60} minutes._",
        parse_mode=ParseMode.MARKDOWN,
    )


# --- CAPTCHA reply ---------------------------------------------------------

async def on_captcha_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if ctx.user_data.get("state") != "awaiting_captcha":
        return  # not in this state; ignore
    u = update.effective_user
    submitted = update.message.text or ""

    ok, remaining = await repo.check_captcha(u.id, submitted)
    if not ok:
        if remaining > 0:
            await update.message.reply_text(
                f"Incorrect. {remaining} attempt(s) remaining. Send /start to retry."
            )
        else:
            ctx.user_data.pop("state", None)
            await update.message.reply_text(
                "CAPTCHA failed. Send /start to receive a new challenge."
            )
        return

    ctx.user_data["state"] = "awaiting_manifesto"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✓ I Accept the Core Protocols", callback_data="accept_protocols")
    ]])
    await update.message.reply_text(MANIFESTO, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


# --- Manifesto acceptance --------------------------------------------------

async def on_accept_protocols(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    u = q.from_user
    cfg: Config = ctx.bot_data["config"]

    if ctx.user_data.get("state") != "awaiting_manifesto":
        await q.edit_message_text("Session expired. Send /start to begin again.")
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
        await q.edit_message_text(f"Failed to issue invite: {e}")
        return

    ctx.user_data["state"] = "in_tier1"
    await q.edit_message_text(
        f"*Access granted :: Tier 1 — The Library*\n\n"
        f"Single-use link (valid {cfg.invite_ttl_seconds // 60} min):\n{invite}\n\n"
        f"Read the pinned baseline. When ready to speak, return here and send /certify.",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


# --- /certify (Phase 2) ----------------------------------------------------

async def cmd_certify(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = await repo.get_user(u.id)
    if not user or not user.get("accepted_protocols_at"):
        await update.message.reply_text(
            "You must complete /start and accept the core protocols first."
        )
        return
    if user.get("certified_at"):
        await update.message.reply_text("You are already certified for Tier 2.")
        return

    ctx.user_data["certify_step"] = 0
    await _send_certify_prompt(update, ctx, step=0)


async def _send_certify_prompt(update, ctx, step: int) -> None:
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✓ I Agree",    callback_data=f"certify_agree_{step}"),
        InlineKeyboardButton("✗ I Disagree", callback_data=f"certify_deny_{step}"),
    ]])
    text = CERTIFY_PROMPTS[step]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def on_certify_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    data = q.data  # certify_agree_0 / certify_deny_1 etc
    parts = data.split("_")
    action, step = parts[1], int(parts[2])

    if action == "deny":
        ctx.user_data.pop("certify_step", None)
        await q.edit_message_text(
            "Certification aborted. You remain in Tier 1 read-only. "
            "Send /certify when ready to commit."
        )
        return

    next_step = step + 1
    if next_step < len(CERTIFY_PROMPTS):
        ctx.user_data["certify_step"] = next_step
        await _send_certify_prompt(update, ctx, step=next_step)
        return

    # All 3 agreed → Tier 2 invite
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
        await q.edit_message_text(f"Failed to issue Tier 2 invite: {e}")
        return

    ctx.user_data.pop("certify_step", None)
    await q.edit_message_text(
        f"*Certified :: Tier 2 — The Floor*\n\n"
        f"Single-use link (valid {cfg.invite_ttl_seconds // 60} min):\n{invite}\n\n"
        f"Operate within topic boundaries. Read the pinned thread index first.",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


# --- Registration ----------------------------------------------------------

def register(application) -> None:
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("certify", cmd_certify))
    application.add_handler(CallbackQueryHandler(on_accept_protocols, pattern="^accept_protocols$"))
    application.add_handler(CallbackQueryHandler(on_certify_button, pattern="^certify_(agree|deny)_\\d+$"))
    # CAPTCHA: any private text message that isn't a command
    application.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        on_captcha_reply,
    ))
