"""The half that is easy to forget: informing the person you just screened.

Article 14.1 puts that duty on the bot, so these tests are mostly about the
notice actually leaving: once per person and per guild, by DM when possible,
by a channel the caller picked when not, and loudly when neither worked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace

import discord
import httpx
import pytest

from trustcord import Level, Listing, RateLimited, TrustcordError, Unauthorized
from trustcord.discord_ext import Listed, Registry, not_listed

NOTICE_URL = "https://trustcord.test/notice"


def http_error(status: int = 403) -> discord.HTTPException:
    return discord.HTTPException(
        SimpleNamespace(status=status, reason="Forbidden"), "closed"
    )


@dataclass
class Channel:
    """Anything with a ``send``, which is all ``inform`` asks for."""

    sent: list[str] = field(default_factory=list)
    fails: bool = False

    async def send(self, text: str) -> None:
        if self.fails:
            raise http_error()
        self.sent.append(text)


@dataclass
class Member:
    id: int
    guild: SimpleNamespace | None = None
    dms: Channel = field(default_factory=Channel)

    async def send(self, text: str) -> None:
        await self.dms.send(text)


class FakeClient:
    """Stands in for :class:`Trustcord`: the registry only ever looks up."""

    def __init__(self, listings: dict[int, Listing] | None = None) -> None:
        self.listings = listings or {}
        self.raises: Exception | None = None
        self.calls: list[int] = []

    async def lookup(self, user_id: int | str) -> Listing | None:
        if self.raises is not None:
            raise self.raises
        self.calls.append(int(user_id))
        return self.listings.get(int(user_id))

    async def lookup_many(self, user_ids) -> dict[str, Listing | None]:
        ids = [int(user_id) for user_id in user_ids]
        self.calls.extend(ids)
        return {str(user_id): self.listings.get(user_id) for user_id in ids}


def listing(user_id: int = 1, level: Level = Level.MEDIUM) -> Listing:
    return Listing(
        user_id=str(user_id),
        level=level,
        since=datetime(2025, 9, 20, tzinfo=UTC),
    )


def registry(client: FakeClient) -> Registry:
    return Registry(client, notice_url=NOTICE_URL)  # type: ignore[arg-type]


async def test_screening_a_listed_member_informs_them_by_dm() -> None:
    member = Member(id=1, guild=SimpleNamespace(id=10))
    registrar = registry(FakeClient({1: listing()}))

    found = await registrar.screen(member)

    assert found is not None
    assert len(member.dms.sent) == 1
    notice = member.dms.sent[0]
    assert NOTICE_URL in notice
    assert "moyen" in notice
    assert "20/09/2025" in notice


async def test_screening_an_unlisted_member_informs_nobody() -> None:
    member = Member(id=1, guild=SimpleNamespace(id=10))
    registrar = registry(FakeClient())

    assert await registrar.screen(member) is None
    assert member.dms.sent == []


async def test_the_notice_goes_out_once_per_member_and_guild() -> None:
    member = Member(id=1, guild=SimpleNamespace(id=10))
    registrar = registry(FakeClient({1: listing()}))

    await registrar.screen(member)
    await registrar.screen(member)
    await registrar.screen(member)

    assert len(member.dms.sent) == 1


async def test_closed_dms_fall_back_to_the_channel() -> None:
    member = Member(id=1, guild=SimpleNamespace(id=10), dms=Channel(fails=True))
    channel = Channel()
    registrar = registry(FakeClient({1: listing()}))

    informed = await registrar.inform(member, listing(), channel=channel)

    assert informed is True
    assert len(channel.sent) == 1


async def test_nobody_informed_says_so_instead_of_pretending(caplog) -> None:
    member = Member(id=1, guild=SimpleNamespace(id=10), dms=Channel(fails=True))
    registrar = registry(FakeClient({1: listing()}))

    informed = await registrar.inform(member, listing(), channel=Channel(fails=True))

    assert informed is False
    assert "article 14.1" in caplog.text


async def test_a_listing_without_a_level_or_a_date_still_reads() -> None:
    member = Member(id=1)
    registrar = registry(FakeClient())

    await registrar.inform(member, Listing(user_id="1", level=None, since=None))

    notice = member.dms.sent[0]
    assert "Votre compte y figure." in notice


async def test_an_audit_informs_nobody_and_returns_only_the_listed() -> None:
    members = [Member(id=1), Member(id=2)]
    guild = SimpleNamespace(members=members)
    registrar = registry(FakeClient({2: listing(2, Level.HIGH)}))

    found = await registrar.audit(guild)  # type: ignore[arg-type]

    assert set(found) == {2}
    assert found[2].level is Level.HIGH
    assert all(member.dms.sent == [] for member in members)


async def test_a_plain_lookup_informs_nobody() -> None:
    member = Member(id=1)
    registrar = registry(FakeClient({1: listing()}))

    assert await registrar.lookup(member) is not None
    assert member.dms.sent == []


async def test_the_check_blocks_a_listed_caller_and_informs_them() -> None:
    member = Member(id=1, guild=SimpleNamespace(id=10))
    channel = Channel()
    registrar = registry(FakeClient({1: listing(level=Level.HIGH)}))
    check = registrar.predicate()

    with pytest.raises(Listed):
        await check(SimpleNamespace(author=member, channel=channel))

    assert len(member.dms.sent) == 1


async def test_the_check_never_announces_a_listing_in_the_channel() -> None:
    member = Member(id=1, guild=SimpleNamespace(id=10), dms=Channel(fails=True))
    channel = Channel()
    check = registry(FakeClient({1: listing(level=Level.HIGH)})).predicate()

    with pytest.raises(Listed):
        await check(SimpleNamespace(author=member, channel=channel))

    assert channel.sent == []


async def test_the_check_lets_a_listing_below_the_minimum_through() -> None:
    member = Member(id=1, guild=SimpleNamespace(id=10))
    registrar = registry(FakeClient({1: listing(level=Level.LOW)}))
    check = registrar.predicate(minimum=Level.HIGH)

    passed = await check(SimpleNamespace(author=member, channel=Channel()))

    assert passed is True
    assert member.dms.sent == []


@pytest.mark.parametrize("error", [httpx.ConnectError("offline"), httpx.ReadTimeout("timeout")])
async def test_the_check_fails_open_when_the_register_is_unreachable(
    error: httpx.TransportError, caplog: pytest.LogCaptureFixture
) -> None:
    member = Member(id=1, guild=SimpleNamespace(id=10))
    client = FakeClient()
    client.raises = error
    check = registry(client).predicate()

    assert await check(SimpleNamespace(author=member, channel=Channel())) is True
    assert "trustcord: lookup failed, letting the call through" in caplog.text


@pytest.mark.parametrize(
    "error",
    [
        RateLimited("quota", 60),
        Unauthorized(401, "invalid key"),
        Unauthorized(403, "revoked key"),
        TrustcordError(500, "server error"),
        ValueError("unknown level"),
    ],
)
async def test_the_check_refuses_api_errors_and_invalid_levels(error: Exception) -> None:
    member = Member(id=1)
    client = FakeClient()
    client.raises = error
    check = registry(client).predicate()
    source = SimpleNamespace(author=member)

    assert await check(source) is False
    assert member.dms.sent == []
    client.raises = None
    assert await check(source) is True
    assert client.calls == [1]


@pytest.mark.parametrize("found", [None, listing(level=Level.HIGH)])
async def test_the_check_caches_answers_for_60_seconds(
    found: Listing | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 100.0
    monkeypatch.setattr("trustcord.discord_ext.monotonic", lambda: now)
    client = FakeClient({1: found} if found is not None else {})
    registrar = registry(client)
    check = registrar.predicate()
    source = SimpleNamespace(author=Member(id=1, guild=SimpleNamespace(id=10)))

    async def invoke() -> None:
        if found is None:
            assert await check(source) is True
        else:
            with pytest.raises(Listed) as caught:
                await check(source)
            assert caught.value.listing is found

    await invoke()
    now = 159.9
    await invoke()
    assert client.calls == [1]
    now = 160.0
    await invoke()
    assert client.calls == [1, 1]


async def test_the_check_shares_raw_answers_between_predicates_by_user_id() -> None:
    client = FakeClient({1: listing(level=Level.LOW)})
    registrar = registry(client)
    source = SimpleNamespace(author=Member(id=1))

    assert await registrar.predicate(minimum=Level.HIGH)(source) is True
    with pytest.raises(Listed):
        await registrar.predicate()(source)
    assert await registrar.predicate()(SimpleNamespace(author=Member(id=2))) is True
    assert client.calls == [1, 2]


async def test_the_check_cache_evicts_the_oldest_answer_when_full() -> None:
    client = FakeClient()
    registrar = registry(client)
    check = registrar.predicate()

    for user_id in range(10_001):
        assert await check(SimpleNamespace(author=Member(id=user_id))) is True
    assert await check(SimpleNamespace(author=Member(id=1))) is True
    assert len(client.calls) == 10_001
    assert await check(SimpleNamespace(author=Member(id=0))) is True
    assert len(client.calls) == 10_002
    assert len(registrar._lookup_cache) == 10_000


async def test_the_decorator_wires_both_check_systems() -> None:
    registrar = registry(FakeClient())

    async def command(ctx: object) -> None: ...

    decorated = not_listed(registrar)(command)

    assert getattr(decorated, "__commands_checks__", [])
    assert getattr(decorated, "__discord_app_commands_checks__", [])
