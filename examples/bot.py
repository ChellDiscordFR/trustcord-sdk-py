"""A whole bot wired to the register, short enough to read in one sitting.

Run it with:

    export TRUSTCORD_API_URL=https://api.example/  TRUSTCORD_API_KEY=or_live_…
    export TRUSTCORD_NOTICE_URL=https://trustcord.example/notice
    export DISCORD_TOKEN=…
    python examples/bot.py

Three things are worth copying rather than the code around them:

* the register is consulted when a member joins, not on every message;
* the person is informed by the same call that screens them, which is what
  article 14.1 asks for;
* nothing is enforced on an account the jury has not ruled on, because the API
  never says that an account is under deliberation.
"""

from __future__ import annotations

import asyncio
import os

import discord
from discord.ext import commands

from trustcord import Level, Trustcord
from trustcord.discord_ext import Listed, Registry, not_listed

NOTICE_URL = os.environ["TRUSTCORD_NOTICE_URL"]

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
client = Trustcord(os.environ["TRUSTCORD_API_KEY"])
registry = Registry(client, notice_url=NOTICE_URL)


@bot.event
async def on_member_join(member: discord.Member) -> None:
    """Screen an arrival, and let the level decide how hard to act."""
    listing = await registry.screen(member)
    if listing is None:
        return

    if listing.level is Level.HIGH:
        await member.ban(reason=f"Trustcord {listing.level}", delete_message_seconds=0)
    elif listing.level is Level.MEDIUM:
        quarantine = discord.utils.get(member.guild.roles, name="Quarantaine")
        if quarantine is not None:
            await member.add_roles(quarantine, reason="Trustcord medium")


@bot.tree.command(description="Ouvrir un giveaway")
@not_listed(registry, minimum=Level.MEDIUM)
async def giveaway(interaction: discord.Interaction) -> None:
    """A command the register closes to listed accounts.

    The check informs the caller before it blocks them, so the refusal below is
    only about telling them the command did not run.
    """
    await interaction.response.send_message("Giveaway ouvert.")


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: discord.app_commands.AppCommandError
) -> None:
    if isinstance(error, Listed):
        await interaction.response.send_message(
            "Cette commande est fermée à votre compte. Les raisons vous ont été "
            f"envoyées en message privé : {NOTICE_URL}",
            ephemeral=True,
        )
        return
    raise error


@bot.command(name="audit")
@commands.has_permissions(manage_guild=True)
async def audit(ctx: commands.Context[commands.Bot]) -> None:
    """Count the listed members of the guild in one round trip.

    An audit restricts nobody, so it informs nobody either. The notice is owed
    when you act on the answer, not when you read it.
    """
    if ctx.guild is None:
        return
    found = await registry.audit(ctx.guild)
    await ctx.send(f"{len(found)} compte(s) inscrit(s) sur {ctx.guild.member_count}.")


@bot.event
async def on_ready() -> None:
    await bot.tree.sync()


async def main() -> None:
    # The client holds a connection pool; closing it with the bot is what keeps
    # a shutdown from leaving sockets behind.
    async with client, bot:
        await bot.start(os.environ["DISCORD_TOKEN"])


if __name__ == "__main__":
    asyncio.run(main())
