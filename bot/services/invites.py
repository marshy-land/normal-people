"""Single-use, time-limited invite link generation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from telegram import Bot

from ..db import repo


async def issue_single_use_invite(
    bot: Bot,
    chat_id: int,
    user_id: int,
    target_tier: int,
    ttl_seconds: int,
) -> str:
    expire_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    name = f"np-t{target_tier}-{user_id}-{int(expire_at.timestamp())}"
    link = await bot.create_chat_invite_link(
        chat_id=chat_id,
        name=name,
        expire_date=expire_at,
        member_limit=1,
        creates_join_request=False,
    )
    await repo.record_invite_link(
        link_id=link.invite_link,
        user_id=user_id,
        target_tier=target_tier,
        ttl_seconds=ttl_seconds,
    )
    return link.invite_link
