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

    # accepted_protocols_at implies they were issued a Floor invite at the
    # manifesto step. Free text in DM is nothing to act on.
    await update.message.reply_text(
        "you already have full access. talk in the group, not here\n"
        "if you never got your invite send /start"
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

    # Accepting the three agreements here is now the full gate. We mark the
    # user protocols-accepted AND certified in one shot (the three agreements
    # shown above are the same contract /certify used to re-ask), bump them to
    # tier 2, and hand out a single-use invite straight to The Floor. No
    # Library detour, no /certify questionnaire, no post-certify hold window.
    await repo.mark_protocols_accepted(u.id)
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
        log.exception("invite generation failed")
        await q.edit_message_text(f"something went wrong issuing your invite: {e}")
        return

    ctx.user_data["state"] = "in_tier2"
    await q.edit_message_text(
        "thank you. here is the way in\n\n"
        f"this link works once and only for the next {cfg.invite_ttl_seconds // 60} minutes\n"
        f"{invite}\n\n"
        "it takes you straight to the floor\n"
        "read the room first, then help where you can",
        disable_web_page_preview=True,
    )


# --- /certify --------------------------------------------------------------

async def cmd_certify(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Legacy no-op. The three-agreement gate now lives entirely at the
    manifesto "i accept" step, which issues the Floor invite directly. This
    command is kept registered only so old links/pins that reference /certify
    don't error out — it just reports the user's current state.
    """
    u = update.effective_user
    user = await repo.get_user(u.id)
    if user and user.get("certified_at"):
        await update.message.reply_text("you already have full access. you are good")
        return
    if user and user.get("accepted_protocols_at"):
        # Accepted the agreements but somehow no invite — nudge them to /start
        # which will re-issue from the manifesto step.
        await update.message.reply_text(
            "the floor invite is handed out the moment you accept the three "
            "agreements. if you missed your link send /start"
        )
        return
    await update.message.reply_text(
        "there is nothing to certify anymore. send /start and accept the "
        "three agreements — that is the whole door now"
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

    # Preferred path: a Telegram `startapp` deep link into the store bot's
    # Main Web App. Form: `https://t.me/<STORE_BOT_USERNAME>?startapp=<token>`.
    #
    # When the user taps it, Telegram launches the registered Main Web App
    # directly (no chat round-trip, no /start race condition on iOS) and
    # surfaces the token to the client as `initDataUnsafe.start_param`. The
    # storefront SPA reads it on boot and POSTs `/api/storefront/grants/redeem`
    # which validates the token, stamps consumed_at, and enforces a strict
    # chat_id match against the Telegram user we already verified via initData.
    #
    # Why not `?start=shop_<token>`: on iOS, returning users who already have
    # the chat opened never see the `/start` payload fire — Telegram just
    # re-opens the existing chat and silently relaunches the Mini App via
    # the menu button, so the grant never gets consumed. `startapp` bypasses
    # this entirely because the token rides inside initData, not as a chat
    # command. We confirmed this against argylesweaters' chat tonight: 0
    # `bot_start` events fired despite the SPA loading.
    #
    # Background on why this isn't the bare https URL: handing out
    # `https://<store-url>/?t=<token>` opens the SPA OUTSIDE Telegram, so the
    # mini-app has no window.Telegram.WebApp.initData, so every customer-API
    # call 401s missing_init_data, so the page never finishes booting.
    #
    # STORE_BOT_USERNAME lives in np_secrets (e.g. "ArgyleApothecarie_bot").
    # STORE_URL is kept as a graceful fallback — the store has a bare-URL
    # redirect middleware that 302s `?t=<token>` into the deep link, and the
    # SPA also reads `?t=` directly as a belt-and-braces redemption path.
    store_bot_username = await repo.get_secret("STORE_BOT_USERNAME")
    store_url = await repo.get_secret("STORE_URL")
    if not store_bot_username and not store_url:
        log.error("STORE_BOT_USERNAME and STORE_URL both missing from np_secrets")
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

    if store_bot_username:
        # Strip a stray '@' if the secret was saved with one.
        handle = store_bot_username.lstrip("@")
        link = f"https://t.me/{handle}?startapp={token}"
    else:
        # Backwards-compat fallback. The store-side redirect middleware will
        # 302 this into the canonical deep link, so the customer still ends
        # up in the Mini App — just with one extra hop. Append cleanly
        # whether store_url already has a query.
        sep = "&" if "?" in store_url else "?"
        link = f"{store_url}{sep}t={token}"
        log.warning(
            "STORE_BOT_USERNAME missing from np_secrets; falling back to bare URL for user=%s",
            u.id,
        )

    await update.message.reply_text(
        "here is your store session\n\n"
        f"{link}\n\n"
        f"the link is yours alone and lasts {ttl_hours} hours. "
        "if you close it just send /shop again",
        disable_web_page_preview=True,
    )


# --- /earnings -------------------------------------------------------------

async def cmd_earnings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """DM-only. Tier-2 certified users see lifetime referral points earned.

    No team-code surface, no payouts, no level breakdown. One number: total
    points credited to this chat_id across all referral activity, plus the
    referral counters so people can see how the number maps to behavior.
    """
    if update.effective_chat.type != "private":
        return  # DM-only

    u = update.effective_user
    user_row = await repo.get_user(u.id)
    if not user_row or (user_row.get("current_tier") or 0) < 2:
        await update.message.reply_text(
            "earnings are tracked once you are in the floor. "
            "send /start, get through the airlock, /certify, then come back"
        )
        return

    try:
        s = await repo.get_earnings_summary(u.id)
    except Exception:
        log.exception("get_earnings_summary failed for user=%s", u.id)
        await update.message.reply_text(
            "couldn't pull your numbers right now. try again in a minute"
        )
        return

    lifetime_pts = s["lifetime_pts"]
    total_refs   = s["total_referrals"]
    qualified    = s["total_qualified"]
    quarantined  = s["quarantined_count"]
    last_at      = s["last_earned_at"]

    if lifetime_pts == 0 and total_refs == 0:
        await update.message.reply_text(
            "nothing tabulated yet\n\n"
            "share /ref with people you actually vouch for. "
            "points credit when they cross the spend threshold"
        )
        return

    last_line = (
        f"last credit: {last_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
        if last_at else ""
    )
    quarantine_line = (
        f"quarantined: {quarantined}\n" if quarantined else ""
    )

    await update.message.reply_text(
        "your earnings — lifetime\n\n"
        f"points:     {lifetime_pts:,}\n"
        f"referrals:  {total_refs} ({qualified} qualified)\n"
        f"{quarantine_line}"
        f"{last_line}"
        "\n"
        "points accrue when a referee crosses the spend threshold. "
        "no leaderboard, no public counters"
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
    application.add_handler(CommandHandler("earnings", cmd_earnings))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CallbackQueryHandler(on_accept_protocols, pattern="^accept_protocols$"))
    application.add_handler(CallbackQueryHandler(on_reverify_accept, pattern="^reverify_accept$"))
    application.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        on_captcha_reply,
    ))
