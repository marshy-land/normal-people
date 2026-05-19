"""Auto-moderation rule engine.

Each detector returns either None (clean) or a Detection describing what
tripped, with severity 'block' (auto-action) or 'flag' (review).

Run with: result = scan(message_text, user_meta)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


# --- data types -----------------------------------------------------------

@dataclass(frozen=True)
class Detection:
    rule_code: str          # short identifier, e.g. "spam.too_many_links"
    severity: str           # "block" or "flag"
    reason: str             # human-readable, shown in mod review
    excerpt: Optional[str] = None


@dataclass(frozen=True)
class UserMeta:
    user_id: int
    account_age_seconds: Optional[int] = None    # how long ago they joined Telegram (best-effort)
    joined_chat_seconds_ago: Optional[int] = None  # how long ago they joined THIS chat
    is_premium: bool = False
    has_username: bool = True


# --- detector 1: spam ------------------------------------------------------

URL_RE = re.compile(r"https?://\S+|www\.\S+|\bt\.me/\S+|\btelegram\.me/\S+", re.IGNORECASE)
TELEGRAM_INVITE_RE = re.compile(r"\b(?:t\.me|telegram\.me|telegram\.dog)/\+[A-Za-z0-9_-]+", re.IGNORECASE)
SHORTENER_DOMAINS = (
    "bit.ly", "tinyurl.com", "goo.gl", "t.co", "ow.ly", "is.gd",
    "buff.ly", "adf.ly", "rb.gy", "shorturl.at", "cutt.ly",
)


def detect_spam(text: str) -> Optional[Detection]:
    if not text:
        return None

    # poaching: an invite to ANOTHER telegram chat
    if TELEGRAM_INVITE_RE.search(text):
        return Detection(
            rule_code="spam.telegram_invite",
            severity="block",
            reason="message contains a telegram invite link to another chat",
        )

    urls = URL_RE.findall(text)
    if len(urls) >= 5:
        return Detection(
            rule_code="spam.too_many_links",
            severity="block",
            reason=f"message contains {len(urls)} links",
        )

    # url shorteners are a known scam vector — flag, don't block
    for u in urls:
        for s in SHORTENER_DOMAINS:
            if s in u.lower():
                return Detection(
                    rule_code="spam.shortener_url",
                    severity="flag",
                    reason=f"message contains a shortened url ({s})",
                )

    return None


# --- detector 2: doxxing patterns ----------------------------------------

# US phone number patterns. International is harder; v1 catches NANP only.
PHONE_RE = re.compile(
    r"\b(?:\+?1[\s.-]?)?\(?[2-9]\d{2}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"
)

# SSN-like patterns (US): 9 digits with separators
SSN_RE = re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b")

# US street address: number + street name + suffix
STREET_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9\s]{2,40}\b(street|st|avenue|ave|road|rd|drive|dr|"
    r"lane|ln|boulevard|blvd|court|ct|way|circle|cir|place|pl)\b",
    re.IGNORECASE,
)


def detect_doxxing(text: str) -> Optional[Detection]:
    if not text:
        return None

    if SSN_RE.search(text):
        return Detection(
            rule_code="doxxing.ssn_pattern",
            severity="block",
            reason="message contains a social security number pattern",
        )

    if PHONE_RE.search(text):
        return Detection(
            rule_code="doxxing.phone",
            severity="block",
            reason="message contains a phone number",
        )

    if STREET_RE.search(text):
        return Detection(
            rule_code="doxxing.address",
            severity="block",
            reason="message contains a street address",
        )

    return None


# --- detector 3: slurs (flag-only, never auto-action) ---------------------

# Conservative core list. Stored deliberately as plain strings so the file
# can be reviewed and tuned. Obfuscation handled by normalization below.
# We treat ALL slur hits as FLAGS for human review, never auto-actions —
# the false-positive cost (medical/clinical/reclamation usage) is too high.
SLUR_TERMS = (
    # racial
    "n!gger", "n!gga", "nigger", "nigga",
    # homophobic
    "f4ggot", "f@ggot", "faggot",
    # ableist
    "r3t@rd", "ret@rd", "retard",
    # transphobic
    "tr@nny", "tranny",
    # gendered
    "wh0re", "whore", "cunt",
)

_LEET_MAP = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "@": "a", "!": "i", "$": "s"})


def _normalize_for_slur_check(text: str) -> str:
    # lowercase, leet → letters, collapse spaces, strip punctuation between letters
    t = text.lower().translate(_LEET_MAP)
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def detect_slurs(text: str) -> Optional[Detection]:
    if not text:
        return None
    normalized = _normalize_for_slur_check(text)
    for term in SLUR_TERMS:
        # also normalize the term itself
        norm_term = _normalize_for_slur_check(term)
        # word boundary check
        if re.search(r"\b" + re.escape(norm_term) + r"\b", normalized):
            return Detection(
                rule_code="slur.match",
                severity="flag",     # always flag, never auto-block
                reason="message contains a term on the slur watchlist (may be in-context use)",
            )
    return None


# --- detector 4: new-account heuristics ----------------------------------

DM_REQUEST_RE = re.compile(r"\b(dm|pm)\s+me\b", re.IGNORECASE)


def detect_new_account_behavior(text: str, user: UserMeta) -> Optional[Detection]:
    if user.joined_chat_seconds_ago is None:
        return None

    # joined chat less than 1 hour ago
    is_brand_new = user.joined_chat_seconds_ago < 3600

    if not is_brand_new:
        return None

    has_link = bool(URL_RE.search(text or ""))
    asks_for_dm = bool(DM_REQUEST_RE.search(text or ""))

    if has_link or asks_for_dm:
        signal = "asks for DM" if asks_for_dm else "posts a link"
        return Detection(
            rule_code="new_account.suspicious",
            severity="flag",
            reason=f"account joined < 1h ago and {signal}",
        )

    return None


# --- detector 5: self-promotion ------------------------------------------

PROMO_PHRASES = (
    "check out my",
    "follow me on",
    "my channel",
    "my page",
    "my profile",
    "subscribe to my",
    "join my",
    "shameless plug",
)

SOCIAL_DOMAINS = (
    "instagram.com", "twitter.com", "x.com", "tiktok.com", "youtube.com",
    "youtu.be", "onlyfans.com", "patreon.com", "twitch.tv", "linkedin.com",
)


def detect_self_promo(text: str) -> Optional[Detection]:
    if not text:
        return None
    low = text.lower()

    has_promo_phrase = any(p in low for p in PROMO_PHRASES)
    has_social_link = any(d in low for d in SOCIAL_DOMAINS)

    if has_promo_phrase and has_social_link:
        return Detection(
            rule_code="self_promo.combined",
            severity="flag",
            reason="message combines self-promotion language with a social media link",
        )

    return None


# --- main entry point -----------------------------------------------------

# Order matters: block-severity detectors first, then flag-severity.
DETECTORS = [
    detect_spam,
    detect_doxxing,
    detect_slurs,
    detect_self_promo,
]


def scan(text: str, user: UserMeta) -> Optional[Detection]:
    """Run all detectors. Returns first hit, or None if clean.

    Block-severity hits short-circuit. Flag-severity hits are still returned
    but the caller decides whether to take action.
    """
    for fn in DETECTORS:
        result = fn(text)
        if result:
            return result
    # new-account check needs user meta, can't be in the static list
    result = detect_new_account_behavior(text, user)
    if result:
        return result
    return None


def excerpt(text: Optional[str], limit: int = 200) -> str:
    if not text:
        return ""
    return (text[:limit] + "…") if len(text) > limit else text
