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
