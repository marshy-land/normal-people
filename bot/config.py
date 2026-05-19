"""Centralized config loaded from environment."""
from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _required(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


def _int_list(key: str) -> list[int]:
    raw = os.getenv(key, "")
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


@dataclass(frozen=True)
class Config:
    hub_bot_token: str
    tier1_channel_id: int
    tier2_group_id: int
    bootstrap_admin_ids: list[int]
    database_url: str
    invite_ttl_seconds: int
    captcha_ttl_seconds: int
    strike_mute_seconds: int
    strike_decay_days: int
    log_level: str


def load_config() -> Config:
    return Config(
        hub_bot_token=_required("HUB_BOT_TOKEN"),
        tier1_channel_id=int(_required("TIER1_CHANNEL_ID")),
        tier2_group_id=int(_required("TIER2_GROUP_ID")),
        bootstrap_admin_ids=_int_list("BOOTSTRAP_ADMIN_IDS"),
        database_url=_required("DATABASE_URL"),
        invite_ttl_seconds=int(os.getenv("INVITE_TTL_SECONDS", "300")),
        captcha_ttl_seconds=int(os.getenv("CAPTCHA_TTL_SECONDS", "120")),
        strike_mute_seconds=int(os.getenv("STRIKE_MUTE_SECONDS", "86400")),
        strike_decay_days=int(os.getenv("STRIKE_DECAY_DAYS", "90")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
