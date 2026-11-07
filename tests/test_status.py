"""Public staff roles must remain visible without a blacklist listing."""

from __future__ import annotations

import httpx
import pytest

from trustcord import Level, Listing, Trustcord, StaffRole, Status


@pytest.mark.parametrize("role", ["jury", "admin", None])
async def test_status_preserves_unlisted_role(role: str | None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/lookup/123456789012345678"
        return httpx.Response(200, json={"blacklisted": False, "staff_role": role})

    async with Trustcord(
        "key", base_url="https://register.test", transport=httpx.MockTransport(handler)
    ) as api:
        status = await api.lookup_status(123456789012345678)
        assert isinstance(status, Status)
        assert status.user_id == "123456789012345678"
        assert status.listed is None
        assert status.staff_role == (StaffRole(role) if role else None)
        assert await api.lookup(123456789012345678) is None


async def test_listed_role_survives_status_lookup_and_bulk() -> None:
    payload: dict[str, object] = {
        "blacklisted": True,
        "blacklistlevel": "medium",
        "blacklistdate": 1758374400,
        "staff_role": "admin",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = {"123456789012345678": payload} if request.method == "POST" else payload
        return httpx.Response(200, json=body)

    async with Trustcord(
        "key", base_url="https://register.test", transport=httpx.MockTransport(handler)
    ) as api:
        status = await api.lookup_status("123456789012345678")
        listing = await api.lookup("123456789012345678")
        bulk = await api.lookup_many(["123456789012345678"])
    assert status.staff_role is StaffRole.ADMIN
    assert status.listed == listing == bulk["123456789012345678"]
    assert listing is not None
    assert listing.staff_role is StaffRole.ADMIN
    assert listing.level is Level.MEDIUM


async def test_old_server_and_listing_constructor_stay_compatible() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"blacklisted": False})

    async with Trustcord(
        "key", base_url="https://register.test", transport=httpx.MockTransport(handler)
    ) as api:
        assert (await api.lookup_status("123456789012345678")).staff_role is None
    assert Listing("123456789012345678", Level.LOW, None).staff_role is None
