"""Entry point. Long-polling worker suitable for Railway."""
from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import Application, ContextTypes

from .config import load_config
from .db.pool import init_pool, close_pool
from .handlers import onboarding, moderation
from .services import jobs

log = logging.getLogger(__name__)


async def _on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler: log without flooding, never crash the dispatcher."""
    err = ctx.error
    update_id = getattr(update, "update_id", None) if isinstance(update, Update) else None
    log.error("Handler error (update_id=%s): %s: %s", update_id, type(err).__name__, err)


async def _post_init(app: Application) -> None:
    cfg = app.bot_data["config"]
    await init_pool(cfg.database_url)
    logging.getLogger(__name__).info("DB pool initialized.")


async def _post_shutdown(app: Application) -> None:
    await close_pool()


def build_application() -> Application:
    cfg = load_config()
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )

    app = (
        Application.builder()
        .token(cfg.hub_bot_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.bot_data["config"] = cfg
    onboarding.register(app)
    moderation.register(app)
    jobs.register(app)
    app.add_error_handler(_on_error)
    return app


def main() -> None:
    app = build_application()
    # Note: keep drop_pending_updates=False so users' messages survive redeploys.
    app.run_polling(allowed_updates=None, drop_pending_updates=False)


if __name__ == "__main__":
    main()
