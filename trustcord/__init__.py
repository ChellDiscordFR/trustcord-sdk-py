"""Trustcord SDK: read the register, file a report, inform the person.

``trustcord.discord_ext`` is imported separately and needs discord.py; this
module stays usable without it, so a bot on another library only pays for the
HTTP client.
"""

from trustcord.client import (
    BULK_MAX,
    Level,
    Listing,
    RateLimited,
    StaffRole,
    Status,
    Trustcord,
    TrustcordError,
    Unauthorized,
)

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
    "__version__",
]

__version__ = "1.0.0"
