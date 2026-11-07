"""discord.py glue: screen an account, and inform the person you screened.

The two halves are deliberately one object. Article 14.1 of the GDPR puts the
duty to inform on the bot, not on Trustcord: if you restrict someone because
the register says so, you must tell them, on the server concerned, at the
latest when they first interact, and point them at the notice. A helper that
only answered "listed: yes" would make it easy to forget that half, so the
same call that gives you the answer sends the notice.

    registry = Registry(client, notice_url="https://trustcord.example/notice")

    @bot.event
    async def on_member_join(member):
        listing = await registry.screen(member)          # informs if listed
        if listing and listing.level >= Level.HIGH:
            await member.kick(reason=f"Trustcord {listing.level}")

    @bot.tree.command()
    @not_listed(registry, minimum=Level.MEDIUM)          # informs if it blocks
    async def giveaway(interaction: discord.Interaction): ...

Everything here is a helper. Whether to act on a listing, and how hard, stays
your decision and your responsibility: you are the controller for what your bot
does with the answer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from time import monotonic
from typing import Any, TypeVar

import httpx

from trustcord.client import Level, Listing, Trustcord, TrustcordError

try:
    import discord
    from discord import app_commands
    from discord.ext import commands
except ModuleNotFoundError as exc:  # pragma: no cover - import guard
    raise ModuleNotFoundError(
        "trustcord.discord_ext needs discord.py: pip install 'trustcord-sdk[discord]'"
    ) from exc

__all__ = ["DEFAULT_NOTICE", "Listed", "Registry", "not_listed"]

log = logging.getLogger("trustcord")

T = TypeVar("T")

# Sent to the person, so it is written in the language of the service and of
# the people it lists. Override it with ``notice=`` if your bot speaks another
# language, but keep what the article requires: who holds the register, what it
# holds, where it came from, and where to object.
DEFAULT_NOTICE = (
    "**Information au titre de l'article 14 du RGPD**\n"
    "Ce serveur utilise le registre Trustcord pour limiter certains accès. "
    "Votre compte y figure{level}{since}.\n\n"
    "Le registre est tenu par Trustcord, qui en est responsable de "
    "traitement. Les informations qu'il contient proviennent de signalements "
    "déposés par des tiers et examinés par un jury. Vous pouvez consulter "
    "votre fiche, contester l'inscription et exercer vos droits ici : "
    "{notice_url}\n\n"
    "La décision de restreindre votre accès sur ce serveur appartient à ce "
    "bot et à ses administrateurs, pas à Trustcord."
)

# The wording the notice uses for each level, in the notice's own language.
_LEVEL_LABEL = {Level.LOW: "faible", Level.MEDIUM: "moyen", Level.HIGH: "élevé"}

Actor = discord.Member | discord.User
Source = commands.Context[Any] | discord.Interaction


class Listed(commands.CheckFailure, app_commands.AppCommandError):
    """Raised by :func:`not_listed` when the register blocks the call.

    Inherits from both check-failure hierarchies, so one ``except Listed`` in
    an error handler covers prefix commands and application commands alike.
    """

    def __init__(self, listing: Listing) -> None:
        super().__init__(f"{listing.user_id} is listed in the Trustcord register")
        self.listing = listing


class _Ephemeral:
    """Answers an interaction so that only the person who ran it sees the text."""

    def __init__(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction

    async def send(self, text: str) -> None:
        if self.interaction.response.is_done():
            await self.interaction.followup.send(text, ephemeral=True)
        else:
            await self.interaction.response.send_message(text, ephemeral=True)


class Registry:
    """A lookup, and the notice that must go with it.

    ``notice_url`` is the address of the Trustcord information notice, the
    page the person must be pointed at. It is required because a notice with
    nowhere to go does not discharge anything.
    """

    def __init__(
        self,
        client: Trustcord,
        *,
        notice_url: str,
        notice: str = DEFAULT_NOTICE,
    ) -> None:
        self.client = client
        self.notice_url = notice_url
        self.notice = notice
        # (guild id, user id) pairs already informed. In memory, so a restart
        # can inform someone twice: that is the harmless direction. Losing the
        # notice entirely is the direction that costs something.
        self._informed: set[tuple[int, int]] = set()
        self._lookup_cache: dict[int, tuple[float, Listing | None]] = {}

    # -- Reading -----

    async def lookup(self, user: Actor | int) -> Listing | None:
        """Ask about an account without informing anybody.

        For auditing a member list, where nothing is being restricted yet.
        The moment you act on the answer, use :meth:`screen` instead.
        """
        return await self.client.lookup(user if isinstance(user, int) else user.id)

    async def screen(
        self,
        member: Actor,
        *,
        channel: discord.abc.Messageable | None = None,
    ) -> Listing | None:
        """Look the member up and, if they are listed, inform them.

        Returns the listing so you can decide what to do with it, or ``None``
        when the account is not listed. An account whose report is still being
        deliberated answers ``None`` as well: nothing is established yet.
        """
        listing = await self.client.lookup(member.id)
        if listing is not None:
            await self.inform(member, listing, channel=channel)
        return listing

    async def audit(self, guild: discord.Guild) -> dict[int, Listing]:
        """Every listed member of a guild, in as few calls as the API allows.

        Informs nobody: an audit restricts nothing. Inform when you act.
        """
        answers = await self.client.lookup_many(m.id for m in guild.members)
        return {
            int(user_id): listing
            for user_id, listing in answers.items()
            if listing is not None
        }

    # -- Informing ---

    async def inform(
        self,
        member: Actor,
        listing: Listing,
        *,
        channel: discord.abc.Messageable | _Ephemeral | None = None,
    ) -> bool:
        """Send the article 14 notice once, and say whether it got through.

        A direct message first, because the notice is addressed to the person
        and not to the server. If their DMs are closed, it falls back to
        ``channel`` when one is given: everyone in that channel reads the
        notice, so pass one only if it is private to this person. ``False``
        means nobody was informed and you have to do it another way before you
        restrict this person.
        """
        guild = getattr(member, "guild", None)
        key = (guild.id if guild is not None else 0, member.id)
        if key in self._informed:
            return True

        text = self._text(listing)
        for target in (member, channel):
            if target is None:
                continue
            try:
                await target.send(text)
            except discord.HTTPException:
                continue
            self._informed.add(key)
            return True

        log.warning(
            "trustcord: could not inform %s of their listing; "
            "article 14.1 still requires it",
            member.id,
        )
        return False

    def _text(self, listing: Listing) -> str:
        level = listing.level
        return self.notice.format(
            level=f" au niveau {_LEVEL_LABEL[level]}" if level else "",
            since=(
                f", depuis le {listing.since:%d/%m/%Y}" if listing.since else ""
            ),
            notice_url=self.notice_url,
        )

    # -- Checks ------

    def predicate(
        self, *, minimum: Level = Level.LOW
    ) -> Callable[[Source], Coroutine[Any, Any, bool]]:
        """The check body, for wiring into something this module does not cover.

        Fails open only on transport errors. API errors and invalid answers
        refuse the command. Successful answers are cached for 60 seconds.
        """

        async def check(source: Source) -> bool:
            actor = source.user if isinstance(source, discord.Interaction) else source.author
            cached = self._lookup_cache.get(actor.id)
            try:
                if cached is not None and monotonic() < cached[0]:
                    listing = cached[1]
                else:
                    self._lookup_cache.pop(actor.id, None)
                    listing = await self.client.lookup(actor.id)
                    if len(self._lookup_cache) >= 10_000:
                        self._lookup_cache.pop(next(iter(self._lookup_cache)))
                    self._lookup_cache[actor.id] = (monotonic() + 60, listing)
            except httpx.TransportError:
                log.exception("trustcord: lookup failed, letting the call through")
                return True
            except (TrustcordError, ValueError):
                log.exception("trustcord: lookup failed, refusing the call")
                return False
            if listing is None or (listing.level is not None and listing.level < minimum):
                return True
            # Never source.channel: closed DMs would announce the listing to
            # everyone there. A slash command can still answer the caller alone.
            fallback = _Ephemeral(source) if isinstance(source, discord.Interaction) else None
            await self.inform(actor, listing, channel=fallback)
            raise Listed(listing)

        return check


def not_listed(
    registry: Registry, *, minimum: Level = Level.LOW
) -> Callable[[T], T]:
    """Block a command for listed accounts, and inform them that it was blocked.

    Works on both prefix commands and application commands: the two check
    systems read different attributes, and this sets both.

    ``minimum`` is the level from which the command is refused, so
    ``minimum=Level.HIGH`` lets a ``low`` listing through untouched - and
    informs nobody, because nothing was restricted.
    """
    predicate = registry.predicate(minimum=minimum)

    def decorator(func: T) -> T:
        return commands.check(predicate)(app_commands.check(predicate)(func))

    return decorator
