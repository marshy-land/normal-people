"""Entry point. Long-polling worker suitable for Railway."""
from __future__ import annotations

import asyncio
import logging

from telegram.ext import Application

from .config import load_config
from .db.pool import init_pool, close_pool
from .handlers import onboarding


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
    return app


def main() -> None:
    app = build_application()
    app.run_polling(allowed_updates=None, drop_pending_updates=True)


if __name__ == "__main__":
    main()
