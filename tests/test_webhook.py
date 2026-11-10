"""Webhook signature and account matching."""

from __future__ import annotations

import hashlib
import hmac

from trustcord.webhook import account_hash, changed, verify

SECRET = "webhook-secret"
BODY = b'{"event":"accounts.changed","accounts":[]}'
NOW = 1_800_000_000


def _signature(timestamp: int, body: bytes = BODY) -> str:
    digest = hmac.new(SECRET.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"sha256={digest.hexdigest()}"


def test_valid_signature_is_accepted() -> None:
    assert verify(SECRET, BODY, str(NOW), _signature(NOW), now=NOW)


def test_tampered_body_is_refused() -> None:
    assert not verify(SECRET, BODY + b" ", str(NOW), _signature(NOW), now=NOW)


def test_wrong_secret_is_refused() -> None:
    assert not verify("other", BODY, str(NOW), _signature(NOW), now=NOW)


def test_old_notification_is_refused() -> None:
    assert not verify(SECRET, BODY, str(NOW - 301), _signature(NOW - 301), now=NOW)


def test_malformed_timestamp_is_refused() -> None:
    assert not verify(SECRET, BODY, "soon", _signature(NOW), now=NOW)


def test_changed_returns_only_matching_ids() -> None:
    accounts = [account_hash(SECRET, "123456789012345678")]

    assert changed(SECRET, accounts, [123456789012345678, "234567890123456789"]) == [
        "123456789012345678"
    ]
