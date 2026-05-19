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
    mod_review_chat_id: int  # 0 means "DM each bootstrap admin instead"
    home_topic_id: int       # thread_id for the #home topic (for intro detection)
    intro_grace_hours: int   # hours after join to post intro before kick
    silent_ping_days: int    # days of silence before "still here?" DM
    silent_demote_days: int  # additional days after ping before demotion
    inactivity_remove_days: int  # days demoted before full removal


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
        mod_review_chat_id=int(os.getenv("MOD_REVIEW_CHAT_ID", "0")),
        home_topic_id=int(os.getenv("HOME_TOPIC_ID", "14")),
        intro_grace_hours=int(os.getenv("INTRO_GRACE_HOURS", "24")),
        silent_ping_days=int(os.getenv("SILENT_PING_DAYS", "30")),
        silent_demote_days=int(os.getenv("SILENT_DEMOTE_DAYS", "7")),
        inactivity_remove_days=int(os.getenv("INACTIVITY_REMOVE_DAYS", "60")),
    )
