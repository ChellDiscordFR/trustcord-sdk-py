"""The HTTP client.

One class, three calls, and the three fields the register is willing to say.
There is no method here that returns a reason, a piece of evidence or a report
count, because there is no endpoint that returns one: the limit is in the
service, not in this file.

``lookup`` answers ``None`` for an account that is not listed. An account whose
case is still being deliberated answers ``None`` too, and that is deliberate -
an open report is an accusation nobody has examined yet.
"""

from __future__ import annotations

import enum
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit

import httpx

__all__ = [
    "BULK_MAX",
    "Level",
    "Listing",
    "Trustcord",
    "TrustcordError",
    "RateLimited",
    "StaffRole",
    "Status",
    "Unauthorized",
]

# The bulk endpoint caps the list; longer calls are split by ``lookup_many``.
BULK_MAX = 100

_RANK = {"low": 0, "medium": 1, "high": 2}


class Level(enum.StrEnum):
    """How grave the jury found the conduct.

    Ordered, so ``listing.level >= Level.MEDIUM`` reads the way it sounds. The
    plain string comparison a StrEnum would give sorts alphabetically, which
    would put ``high`` below ``low``.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return _RANK[self.value]

    def __lt__(self, other: object) -> bool:
        if isinstance(other, Level):
            return self.rank < other.rank
        return NotImplemented

    def __le__(self, other: object) -> bool:
        if isinstance(other, Level):
            return self.rank <= other.rank
        return NotImplemented

    def __gt__(self, other: object) -> bool:
        if isinstance(other, Level):
            return self.rank > other.rank
        return NotImplemented

    def __ge__(self, other: object) -> bool:
        if isinstance(other, Level):
            return self.rank >= other.rank
        return NotImplemented


class StaffRole(enum.StrEnum):
    """Public role of an active staff member, whether listed or not."""

    JURY = "jury"
    ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class Listing:
    """An account the jury has listed. Everything the register will say."""

    user_id: str
    level: Level | None
    since: datetime | None
    staff_role: StaffRole | None = None

    @classmethod
    def parse(cls, user_id: str, payload: Mapping[str, Any]) -> Self | None:
        if not payload.get("blacklisted"):
            return None
        raw_level = payload.get("blacklistlevel")
        raw_date = payload.get("blacklistdate")
        return cls(
            user_id=user_id,
            level=Level(raw_level) if raw_level else None,
            since=datetime.fromtimestamp(raw_date, UTC) if raw_date else None,
            staff_role=StaffRole(payload["staff_role"]) if payload.get("staff_role") else None,
        )


@dataclass(frozen=True, slots=True)
class Status:
    """Public lookup, including a staff role for an unlisted account."""

    user_id: str
    listed: Listing | None
    staff_role: StaffRole | None


class TrustcordError(Exception):
    """Anything the API refused, with the status it refused it with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Unauthorized(TrustcordError):
    """Key missing, invalid, revoked, or the application is suspended."""


class RateLimited(TrustcordError):
    """Quota spent. ``retry_after`` is the wait, in seconds, the API asked for."""

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(429, message)
        self.retry_after = retry_after


class Trustcord:
    """Talks to the register on behalf of one application.

    The key identifies an application, never a person, and it stays server
    side: putting it in a client the users of a bot can reach hands them the
    application's whole quota.

    Usable as an async context manager, or long-lived on the bot and closed
    when the bot closes. One instance holds one connection pool; making a new
    one per lookup works but wastes a TLS handshake every time.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        url = base_url if base_url is not None else os.environ.get(
            "TRUSTCORD_API_URL", "https://api.trustcord.org"
        )
        parsed = urlsplit(url)
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or not (
                parsed.scheme == "https"
                or (
                    parsed.scheme == "http"
                    and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                )
            )
        ):
            raise ValueError("Expected HTTPS or loopback HTTP without URL credentials")
        self._http = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "trustcord-sdk/1.0",
            },
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    # -- Reading the register 

    async def lookup(self, user_id: int | str) -> Listing | None:
        """Is this account listed? ``None`` means no, and says nothing more."""
        return (await self.lookup_status(user_id)).listed

    async def lookup_status(self, user_id: int | str) -> Status:
        """Read listing and public staff role without hiding unlisted staff."""
        user_id = str(user_id)
        if re.fullmatch(r"[0-9]{17,20}", user_id) is None:
            raise ValueError("Expected a Discord snowflake with 17 to 20 digits")
        payload = await self._call("GET", f"/v1/lookup/{user_id}")
        return Status(
            user_id=user_id,
            listed=Listing.parse(user_id, payload),
            staff_role=StaffRole(payload["staff_role"]) if payload.get("staff_role") else None,
        )

    async def lookup_many(
        self, user_ids: Iterable[int | str]
    ) -> dict[str, Listing | None]:
        """Ask about a whole member list in one round trip.

        Counts as one call against the per-minute quota and as one call per
        identifier against the daily one, so this is cheaper than the same
        identifiers one at a time. Lists longer than the API's ceiling are
        split here rather than refused.
        """
        ids = [str(user_id) for user_id in user_ids]
        out: dict[str, Listing | None] = {}
        for start in range(0, len(ids), BULK_MAX):
            chunk = ids[start : start + BULK_MAX]
            payload = await self._call("POST", "/v1/lookup", json={"ids": chunk})
            for user_id in chunk:
                entry = payload.get(user_id)
                out[user_id] = Listing.parse(user_id, entry) if entry else None
        return out

    # -- Filing a report ------

    async def report(
        self,
        user_ids: Sequence[int | str],
        reason: str,
        evidence: Sequence[tuple[str, bytes]],
    ) -> str:
        """File a report on behalf of a user of your bot, and get its reference.

        ``evidence`` is a list of ``(filename, content)`` pairs, and at least
        one screenshot is required: the jury judges the conversation, not the
        summary of it. The real type of each file is checked against its bytes,
        so naming a .txt "capture.png" fails.

        Your key is spent for the quota and nothing else. No column on the
        report points back to your application, which also means you cannot
        read the report back, or learn what became of it. The reference is the
        only thread, and it belongs to whoever filed.
        """
        files = [("files", (name, content)) for name, content in evidence]
        data = {
            "reason": reason,
            "discord_ids": [str(user_id) for user_id in user_ids],
        }
        payload = await self._call("POST", "/v1/reports", data=data, files=files)
        return str(payload["reference"])

    # -- Plumbing ----

    async def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._http.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise self._failure(response)
        body: Any = response.json()
        if not isinstance(body, dict):
            raise TrustcordError(response.status_code, "unexpected response shape")
        return body

    @staticmethod
    def _failure(response: httpx.Response) -> TrustcordError:
        detail = "unknown error"
        try:
            body: Any = response.json()
            if isinstance(body, dict) and isinstance(body.get("detail"), str):
                detail = body["detail"]
        except ValueError:
            detail = response.text[:200] or detail

        if response.status_code == 429:
            retry = response.headers.get("Retry-After", "60")
            return RateLimited(detail, int(retry) if retry.isdigit() else 60)
        if response.status_code in (401, 403):
            return Unauthorized(response.status_code, detail)
        return TrustcordError(response.status_code, detail)
