"""Post and pin one description message per topic in the floor.

Usage:
    BOT_TOKEN=... TIER2_GROUP_ID=-100... python3 scripts/post_topic_pins.py
"""
from __future__ import annotations

import os
import sys
import time
import urllib.parse
import urllib.request
import json

# (thread_id, message body)
PINS = [
    (14, """home

this is where conversation lives when it doesn't fit anywhere else

introduce yourself if you want
ask a question
share something on your mind
check in

the three rules apply here like anywhere else"""),

    (15, """harm reduction

this is for keeping one another alive

post tested results and reagent reactions
post drug interaction warnings
post overdose response and signs
post safer use practices for any substance
ask before you take something you don't fully understand

cite your source when you can
if you don't know say you don't know
no one here judges what you take they only help you take it well"""),

    (16, """psychedelics

this is for the work and what it shows you

trip reports honest ones
set and setting
integration after the come down
extraction and synthesis discussion when it stays in theory
substance-specific dose curves duration onsets

what you saw matters less than what you did with it"""),

    (17, """pharmaceuticals

this is for what we take from a pharmacy and what it does

prescriptions and what they actually do
off-label use and risks
generic substitution
half-lives and washout periods
interactions with other things in this group
ssri ssnri benzo opioid stimulant titration and tapering

your prescriber is not in this room
nothing here is medical advice
share information not prescriptions"""),

    (45, """naturals

this is for cannabis kratom kava herbs supplements and what else grows from the ground

strain reports and effects
extraction methods that don't involve solvents you can't handle
tea preparations
dosing for unfamiliar plants
cultivation tips

if it grew it belongs here
if it came out of a lab it goes in pharmaceuticals"""),

    (18, """art

this is for what we make

writing music painting code film photography craft
share work in progress
share finished work
ask for feedback if you want it
give feedback only when asked

no self-promotion to outside platforms unless someone is asking where to find more"""),

    (19, """research

this is for what we are learning

paper drops with one paragraph of why it matters
survey responses
clinical trial recruitment if you trust it
methodology questions
data we are collecting ourselves

link the source
say what you actually read versus what the headline said
ego stays at the door here too"""),

    (25, """vendor reviews

this is for what we learn about who we buy from

share what you got and how it went
do not solicit
do not post prices or payment details
the rest is up to you"""),
]


def call(token: str, method: str, params: dict) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    token = os.environ["BOT_TOKEN"]
    chat_id = os.environ["TIER2_GROUP_ID"]
    posted = 0
    pinned = 0
    for tid, body in PINS:
        time.sleep(0.4)
        post = call(token, "sendMessage", {
            "chat_id": chat_id,
            "message_thread_id": tid,
            "text": body,
            "disable_web_page_preview": "true",
        })
        if not post.get("ok"):
            print(f"  ✗ topic {tid} POST failed: {post.get('description')}", file=sys.stderr)
            continue
        msg_id = post["result"]["message_id"]
        topic_name = body.split("\n", 1)[0]
        print(f"  ✓ topic {tid} ({topic_name}) posted as msg {msg_id}")
        posted += 1

        time.sleep(0.4)
        pin = call(token, "pinChatMessage", {
            "chat_id": chat_id,
            "message_id": msg_id,
            "disable_notification": "true",
        })
        if pin.get("ok"):
            pinned += 1
        else:
            print(f"    ✗ pin failed: {pin.get('description')}", file=sys.stderr)

    print(f"\n{posted}/{len(PINS)} posted, {pinned}/{len(PINS)} pinned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
