"""One-shot script to create the six canonical topics in the Floor supergroup.

Usage:
    BOT_TOKEN=... TIER2_GROUP_ID=-100... python scripts/create_topics.py
"""
from __future__ import annotations

import os
import sys
import time
import urllib.parse
import urllib.request
import json

TOPICS = [
    "home",
    "harm reduction",
    "psychedelics",
    "pharmaceuticals",
    "art",
    "research",
]


def call(token: str, method: str, params: dict) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    token = os.environ.get("BOT_TOKEN")
    chat_id = os.environ.get("TIER2_GROUP_ID")
    if not token or not chat_id:
        print("Set BOT_TOKEN and TIER2_GROUP_ID env vars.", file=sys.stderr)
        return 1

    created = []
    for name in TOPICS:
        # Pace ourselves to stay under Telegram's anti-spam rate limit.
        time.sleep(0.5)
        resp = call(token, "createForumTopic", {
            "chat_id": chat_id,
            "name": name,
        })
        if resp.get("ok"):
            tid = resp["result"]["message_thread_id"]
            print(f"  ✓ {name:24} (thread_id={tid})")
            created.append((name, tid))
        else:
            print(f"  ✗ {name:24} FAILED: {resp.get('description')}", file=sys.stderr)

    print(f"\nCreated {len(created)}/{len(TOPICS)} topics.")
    return 0 if len(created) == len(TOPICS) else 1


if __name__ == "__main__":
    sys.exit(main())
