"""Reject unsafe client inputs before sending a request."""

from __future__ import annotations

import httpx
import pytest

from trustcord import Trustcord


@pytest.mark.parametrize("method", ["lookup", "lookup_status"])
@pytest.mark.parametrize(
    "user_id",
    [
        "../../v1/reports",
        "123?ids=1",
        "abc",
        "1234567890123456",
        "123456789012345678901",
        "123456789012345678\n",
    ],
)
async def test_invalid_user_ids_fail_before_any_request(method: str, user_id: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("Unexpected request")

    async with Trustcord("key", transport=httpx.MockTransport(handler)) as api:
        with pytest.raises(ValueError, match="Discord snowflake"):
            if method == "lookup":
                await api.lookup(user_id)
            else:
                await api.lookup_status(user_id)


@pytest.mark.parametrize("user_id", [123456789012345678, "123456789012345678"])
async def test_valid_user_ids_build_the_expected_path(user_id: int | str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/lookup/123456789012345678"
        return httpx.Response(200, json={"blacklisted": False})

    async with Trustcord("key", transport=httpx.MockTransport(handler)) as api:
        assert (await api.lookup_status(user_id)).user_id == str(user_id)


@pytest.mark.parametrize("from_environment", [False, True])
@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.trustcord.org",
        "https://example.test:8443/api/",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://[::1]:8000",
    ],
)
async def test_secure_and_loopback_base_urls_are_accepted(
    base_url: str, from_environment: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRUSTCORD_API_URL", base_url)
    async with Trustcord("key", base_url=None if from_environment else base_url) as api:
        assert str(api._http.base_url).rstrip("/") == base_url.rstrip("/")


@pytest.mark.parametrize("from_environment", [False, True])
@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.trustcord.org",
        "http://localhost.evil.test",
        "https://user:pass@host",
        "https://user@host",
        "https://:pass@host",
        "http://user:pass@localhost",
        "ftp://host",
        "",
        "/relative",
        "https:///missing-host",
    ],
)
def test_unsafe_base_urls_are_refused_at_construction(
    base_url: str, from_environment: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRUSTCORD_API_URL", base_url)
    with pytest.raises(ValueError):
        Trustcord("key", base_url=None if from_environment else base_url)


async def test_explicit_base_url_takes_precedence_over_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUSTCORD_API_URL", "http://api.trustcord.org")
    async with Trustcord("key", base_url="https://example.test") as api:
        assert str(api._http.base_url) == "https://example.test"
