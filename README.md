# trustcord-sdk

A Python client for the Trustcord register, and the discord.py helpers that go
with it.

The register answers one question: has this Discord account been reported, had
that report examined by a jury, and upheld? It also exposes the public role of
active jurors and administrators, including accounts that are not listed. There is
no endpoint that returns a reason, a piece of evidence, a username or a report
count, so there is no method here that returns one either.

## Install

```sh
pip install trustcord-sdk           # the HTTP client alone
pip install 'trustcord-sdk[discord]' # plus the discord.py helpers
```

Python 3.11 or newer.

## Getting a key

Access is not open. Apply on the `/developers` page of the instance you want
to use: you describe your bot, you say why a register lookup belongs in it, and
an administrator reads the application before a key is issued.

A key identifies an application, never a person, and it must stay server side.
Anything that reaches the users of your bot can spend your whole quota.

## The client

```python
import asyncio
from trustcord import Level, Trustcord

async def main():
    async with Trustcord("or_live_…", base_url="https://api.example") as client:
        listing = await client.lookup(123456789012345678)
        if listing and listing.level >= Level.MEDIUM:
            print(f"listed {listing.level} since {listing.since:%Y-%m-%d}")

asyncio.run(main())
```

`base_url` can come from the `TRUSTCORD_API_URL` environment variable instead
of the argument. If neither is set, it defaults to `https://api.trustcord.org`.

`lookup` returns `None` for an account that is not listed. It also returns
`None` for an account whose report is still being deliberated, and that is
deliberate: an open report is an accusation nobody has examined yet, and the API
makes it indistinguishable from an account nobody ever named. Nothing here lets
you sanction on suspicion.

### Public staff roles

```python
from trustcord import StaffRole

status = await client.lookup_status(123456789012345678)
if status.staff_role is StaffRole.JURY:
    print("Active Trustcord juror")
listing = status.listed  # Listing or None, as with lookup().
```

`Status` exposes `user_id`, `listed` and `staff_role`. The role is `StaffRole.JURY`,
`StaffRole.ADMIN` or `None`; it does not imply that an NDA has been signed.
`Listing.staff_role` also exposes the role when an account is listed, including
results from `lookup_many`. Existing `lookup` and `lookup_many` return types are
unchanged. Missing or null role fields from older servers become `None`.

### A whole member list at once

```python
answers = await client.lookup_many(member.id for member in guild.members)
listed = {user_id: l for user_id, l in answers.items() if l is not None}
```

One call against the per-minute quota, one call per identifier against the
daily quota. Lists longer than the API's ceiling of 100 are split here rather
than refused.

### Filing a report

```python
reference = await client.report(
    [123456789012345678],
    "What happened, in at least 30 characters.",
    [("capture.png", screenshot_bytes)],
)
```

At least one screenshot is required: the jury judges the conversation, not a
summary of it. Each file's real type is checked against its bytes, so naming a
text file `capture.png` fails.

Your key is spent for the quota and nothing else. No column on the report points
back to your application, which also means you cannot read the report back or
learn what became of it. The reference is the only thread, and it belongs to
whoever filed.

### Errors

| Exception | When |
| --- | --- |
| `Unauthorized` | key missing, invalid, revoked, application suspended (401, 403) |
| `RateLimited` | quota spent; `retry_after` is the wait in seconds (429) |
| `TrustcordError` | anything else the API refused; `status` and `message` |

## The discord.py helpers

Article 14.1 of the GDPR puts the duty to inform on the bot, not on Trustcord.
If you restrict someone because the register says so, you must tell them, on the
server concerned, at the latest when they first interact, and point them at the
notice page. So the call that screens a member is the same call that informs
them:

```python
from trustcord import Level, Trustcord
from trustcord.discord_ext import Listed, Registry, not_listed

client = Trustcord("or_live_…")
registry = Registry(client, notice_url="https://trustcord.example/notice")

@bot.event
async def on_member_join(member):
    listing = await registry.screen(member)   # informs if listed
    if listing and listing.level is Level.HIGH:
        await member.ban(reason=f"Trustcord {listing.level}")
```

| Method | Looks up | Informs |
| --- | --- | --- |
| `screen(member, channel=None)` | one account | yes, if listed |
| `lookup(member_or_id)` | one account | no |
| `audit(guild)` | every member, batched | no |
| `inform(member, listing, channel=None)` | nothing | yes |

`lookup` and `audit` inform nobody because they restrict nobody. The notice is
owed when you act on the answer, not when you read it.

`inform` sends a direct message first, since the notice is addressed to the
person and not to the server, and falls back to `channel` when their DMs are
closed. Everyone in that channel reads the notice, so pass one only if it is
private to that person. The `not_listed` check never uses the command's
channel: for a slash command it answers ephemerally instead, and for a prefix
command it has no fallback. It returns `False` when neither worked: that means nobody was informed,
and you have to inform them another way before you restrict them. It sends once
per member and per guild.

### Blocking a command

```python
@bot.tree.command()
@not_listed(registry, minimum=Level.MEDIUM)
async def giveaway(interaction): ...
```

`not_listed` works on prefix commands and application commands alike. It raises
`Listed`, which inherits from both check-failure hierarchies, so one
`except Listed` in an error handler covers both:

```python
@bot.tree.error
async def on_error(interaction, error):
    if isinstance(error, Listed):
        await interaction.response.send_message("…", ephemeral=True)
```

The check informs the caller before it blocks them. It fails open: if the
register is unreachable, the command runs. A moderation aid that takes the bot
down with it is worse than one that misses a listing for a minute.

`minimum` is the level from which the command is refused, so
`minimum=Level.HIGH` lets a `low` listing through untouched, and informs nobody
because nothing was restricted.

### The notice text

`DEFAULT_NOTICE` is in French, the language of the service and of the people it
lists. Override it with `notice=` if your bot speaks another language, but keep
what the article requires: who holds the register, what it holds, where it came
from, and where to object. Three fields are interpolated: `{level}`, `{since}`
and `{notice_url}`.

```python
registry = Registry(client, notice_url=…, notice=MY_NOTICE)
```

## What stays your responsibility

The SDK screens and informs. Whether to act on a listing, and how hard, is your
decision: for what your bot does with the answer, you are the controller, and
the person you restricted comes to you, not to Trustcord. A `high` listing
means a jury thought the facts might be criminal; it is not a conviction.

## Levels

| Level | What the jury found |
| --- | --- |
| `Level.LOW` | a lapse, nothing grave |
| `Level.MEDIUM` | conduct Discord itself forbids |
| `Level.HIGH` | facts that may be criminal; reported to Pharos |

They compare by gravity, so `listing.level >= Level.MEDIUM` reads the way it
sounds. A plain string comparison would sort `high` below `low`.

## Development

```sh
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

The tests use `httpx.MockTransport` for the client and stand-in objects for
discord.py, so nothing reaches the network.
