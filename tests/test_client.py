"""What the client promises, checked against a fake API.

The register's whole contract is three fields, so most of these tests are about
what the client refuses to invent from them: no listing for an account that is
not listed, no listing for one whose case is still open, and no silent
truncation when a caller asks about more accounts than the bulk endpoint takes.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from trustcord import (
    BULK_MAX,
    Level,
    RateLimited,
    Trustcord,
    TrustcordError,
    Unauthorized,
)

BASE = "https://register.test"


def client(handler) -> Trustcord:
    return Trustcord("key", base_url=BASE, transport=httpx.MockTransport(handler))


async def test_a_listed_account_comes_back_whole() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer key"
        return httpx.Response(
            200,
            json={
                "blacklisted": True,
                "blacklistdate": 1_758_374_400,
                "blacklistlevel": "medium",
            },
        )

    async with client(handler) as api:
        listing = await api.lookup(123456789012345678)

    assert listing is not None
    assert listing.user_id == "123456789012345678"
    assert listing.level is Level.MEDIUM
    assert listing.since == datetime(2025, 9, 20, 13, 20, tzinfo=UTC)


async def test_an_unlisted_account_is_none_not_an_empty_listing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"blacklisted": False})

    async with client(handler) as api:
        assert await api.lookup(123456789012345678) is None


async def test_an_account_under_deliberation_reads_as_unlisted() -> None:
    """The API answers the same for an open case as for an unknown account."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"blacklisted": False, "blacklistdate": None, "blacklistlevel": None},
        )

    async with client(handler) as api:
        assert await api.lookup(123456789012345678) is None


async def test_levels_compare_by_gravity_not_alphabetically() -> None:
    assert Level.HIGH > Level.MEDIUM > Level.LOW
    assert Level.LOW < Level.HIGH
    assert not Level.LOW >= Level.MEDIUM


async def test_a_long_member_list_is_split_instead_of_refused() -> None:
    chunks: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        ids = json.loads(request.content)["ids"]
        chunks.append(len(ids))
        return httpx.Response(
            200,
            json={user_id: {"blacklisted": False} for user_id in ids},
        )

    async with client(handler) as api:
        answers = await api.lookup_many(range(BULK_MAX + 5))

    assert chunks == [BULK_MAX, 5]
    assert len(answers) == BULK_MAX + 5
    assert all(listing is None for listing in answers.values())


async def test_bulk_keeps_the_listings_it_is_given() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "1": {"blacklisted": True, "blacklistdate": None, "blacklistlevel": "high"},
                "2": {"blacklisted": False},
            },
        )

    async with client(handler) as api:
        answers = await api.lookup_many([1, 2])

    assert answers["1"] is not None
    assert answers["1"].level is Level.HIGH
    assert answers["1"].since is None
    assert answers["2"] is None


async def test_filing_a_report_sends_the_reason_the_ids_and_the_files() -> None:
    seen: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return httpx.Response(201, json={"reference": "OR-2025-0001", "targets": 1})

    async with client(handler) as api:
        reference = await api.report(
            [123456789012345678],
            "a reason long enough for the API to accept it",
            [("capture.png", b"\x89PNG\r\n\x1a\n")],
        )

    assert reference == "OR-2025-0001"
    body = seen["body"]
    assert b"a reason long enough" in body
    assert b"123456789012345678" in body
    assert b"capture.png" in body


async def test_a_spent_quota_raises_with_the_wait_the_api_asked_for() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"detail": "quota dépassé"}, headers={"Retry-After": "42"}
        )

    async with client(handler) as api:
        with pytest.raises(RateLimited) as caught:
            await api.lookup(123456789012345678)

    assert caught.value.retry_after == 42
    assert caught.value.status == 429


async def test_a_revoked_key_raises_unauthorized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "clé révoquée"})

    async with client(handler) as api:
        with pytest.raises(Unauthorized):
            await api.lookup(123456789012345678)


async def test_an_unparseable_error_still_carries_its_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="<html>gateway</html>")

    async with client(handler) as api:
        with pytest.raises(TrustcordError) as caught:
            await api.lookup(123456789012345678)

    assert caught.value.status == 500


async def test_the_base_url_defaults_to_trustcord(monkeypatch) -> None:
    monkeypatch.delenv("TRUSTCORD_API_URL", raising=False)
    api = Trustcord("key")
    assert str(api._http.base_url) == "https://api.trustcord.org"
    assert api._http.headers["User-Agent"] == "trustcord-sdk/1.0"
    await api.close()


async def test_the_base_url_can_come_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("TRUSTCORD_API_URL", BASE)
    api = Trustcord("key")
    assert str(api._http.base_url) == BASE
    await api.close()
