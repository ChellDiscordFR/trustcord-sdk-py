"""Webhook helpers: check that a notification comes from Trustcord, then match it.

When a listing is lifted or its level changes, Trustcord posts the keyed hashes
of the accounts concerned, never their IDs. Hash the IDs you hold with your
webhook secret and look those accounts up again.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Iterable

MAX_AGE = 300  # seconds a notification stays acceptable, against replays


def account_hash(secret: str, discord_id: int | str) -> str:
    """The hash Trustcord sends for this account, with your webhook secret."""
    return hmac.new(secret.encode(), str(discord_id).encode(), hashlib.sha256).hexdigest()


def verify(
    secret: str,
    body: bytes,
    timestamp: str,
    signature: str,
    *,
    max_age: int = MAX_AGE,
    now: float | None = None,
) -> bool:
    """True when the headers prove Trustcord sent this exact body recently.

    ``timestamp`` is the ``X-Trustcord-Timestamp`` header, ``signature`` the
    ``X-Trustcord-Signature`` header, ``body`` the raw request body.
    """
    try:
        sent = int(timestamp)
    except ValueError:
        return False
    current = time.time() if now is None else now
    if abs(current - sent) > max_age:
        return False
    digest = hmac.new(secret.encode(), f"{sent}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={digest}", signature)


def changed(secret: str, accounts: Iterable[str], discord_ids: Iterable[int | str]) -> list[str]:
    """Your IDs whose hash appears in the notification: look these up again."""
    wanted = set(accounts)
    return [str(i) for i in discord_ids if account_hash(secret, i) in wanted]
