"""Discord bot: account creation for everyone, admin tools for devs.

config.json:
  "discord": {"token": "...", "guildId": 123, "devRoleIds": [456], "devUserIds": [789]}
"""
import json
import re

import discord
from discord import app_commands

from . import accounts, profiles
from .log import log, user
from .utils import CONFIG

CFG = CONFIG.get("discord", {})
NAME_RE = re.compile(r"^[A-Za-z0-9_\-. ]{3,24}$")

client = discord.Client(intents=discord.Intents.default())
tree = app_commands.CommandTree(client)
GUILD = discord.Object(id=int(CFG["guildId"])) if CFG.get("guildId") else None


def _is_dev(i: discord.Interaction) -> bool:
    if i.user.id in {int(x) for x in CFG.get("devUserIds", [])}:
        return True
    roles = {r.id for r in getattr(i.user, "roles", [])}
    return bool(roles & {int(x) for x in CFG.get("devRoleIds", [])})


dev_only = app_commands.check(_is_dev)


async def _reply(i: discord.Interaction, text: str, ok: bool = True):
    embed = discord.Embed(description=text, color=0x57F287 if ok else 0xED4245)
    await i.response.send_message(embed=embed, ephemeral=True)


def _by_discord(discord_id: int) -> dict | None:
    return next((a for a in accounts._accounts.values() if a.get("discordId") == str(discord_id)), None)


def _edit_profile(account_id: str, profile_id: str, edit) -> None:
    p = profiles.get_profile(account_id, profile_id)
    edit(p)
    p["rvn"] += 1
    p["commandRevision"] += 1
    profiles.save_profile(p)


@tree.command(name="create", description="Create your account", guild=GUILD)
@app_commands.describe(username="In-game name", password="Password for the game login")
async def create(i: discord.Interaction, username: str, password: str):
    if _by_discord(i.user.id):
        return await _reply(i, "You already have an account.", False)
    if not NAME_RE.match(username):
        return await _reply(i, "Names are 3-24 letters, numbers, spaces or `_ - .`", False)
    if len(password) < 6:
        return await _reply(i, "Password needs at least 6 characters.", False)
    try:
        accounts.create(username, password=password, discord_id=str(i.user.id))
    except ValueError:
        return await _reply(i, "That name is taken.", False)
    log("Bot", f"{i.user} created {user(username)}", "good")
    await _reply(i, f"Account **{username}** created. Log in with `{username}` and your password.")


@tree.command(name="account", description="Show your account", guild=GUILD)
async def account(i: discord.Interaction):
    acc = _by_discord(i.user.id)
    if not acc:
        return await _reply(i, "No account yet, use /create.", False)
    await _reply(i, f"**{acc['displayName']}**\nID `{acc['accountId']}`\nBanned: {acc.get('banned', False)}")


@tree.command(name="resetpassword", description="Change your game password", guild=GUILD)
async def resetpassword(i: discord.Interaction, password: str):
    acc = _by_discord(i.user.id)
    if not acc:
        return await _reply(i, "No account yet, use /create.", False)
    if len(password) < 6:
        return await _reply(i, "Password needs at least 6 characters.", False)
    accounts.update(acc["accountId"], password=password)
    await _reply(i, "Password changed.")


@tree.command(name="giveall", description="[Dev] Give every cosmetic", guild=GUILD)
@dev_only
async def giveall(i: discord.Interaction, username: str):
    acc = accounts.by_name(username)
    if not acc:
        return await _reply(i, "No such account.", False)
    added = {"n": 0}

    def edit(p):
        before = len(p["items"])
        profiles._grant_all(p)
        added["n"] = len(p["items"]) - before
    _edit_profile(acc["accountId"], "athena", edit)
    log("Bot", f"{i.user} gave all cosmetics to {user(username)} (+{added['n']})", "good")
    await _reply(i, f"Gave **{added['n']}** new cosmetics to **{username}**. Relog to see them.")


async def _set_ban(i: discord.Interaction, username: str, banned: bool, reason: str = ""):
    acc = accounts.by_name(username)
    if not acc:
        return await _reply(i, "No such account.", False)
    accounts.update(acc["accountId"], banned=banned, banReason=reason)
    if banned:
        from . import stomp, xmpp
        for mod in (xmpp, stomp):
            kick = getattr(mod, "kick", None)
            if kick:
                await kick(acc["accountId"])
    log("Ban", f"{i.user} {'banned' if banned else 'unbanned'} {user(username)} {reason}".rstrip(),
        "error" if banned else "good")
    await _reply(i, f"**{username}** {'banned' if banned else 'unbanned'}.")


@tree.command(name="ban", description="[Dev] Ban an account", guild=GUILD)
@dev_only
async def ban(i: discord.Interaction, username: str, reason: str = ""):
    await _set_ban(i, username, True, reason)


@tree.command(name="unban", description="[Dev] Unban an account", guild=GUILD)
@dev_only
async def unban(i: discord.Interaction, username: str):
    await _set_ban(i, username, False)


@tree.command(name="setlevel", description="[Dev] Set season level", guild=GUILD)
@dev_only
async def setlevel(i: discord.Interaction, username: str, level: app_commands.Range[int, 1, 10000]):
    acc = accounts.by_name(username)
    if not acc:
        return await _reply(i, "No such account.", False)
    _edit_profile(acc["accountId"], "athena",
                  lambda p: p["stats"]["attributes"].update(level=level, book_level=level, accountLevel=level))
    log("Bot", f"{i.user} set {user(username)} to level {level}")
    await _reply(i, f"**{username}** is now level **{level}**. Relog to see it.")


@tree.command(name="setvbucks", description="[Dev] Set V-Bucks", guild=GUILD)
@dev_only
async def setvbucks(i: discord.Interaction, username: str, amount: app_commands.Range[int, 0, 10_000_000]):
    acc = accounts.by_name(username)
    if not acc:
        return await _reply(i, "No such account.", False)

    def edit(p):
        mtx = p["items"].setdefault("Currency:MtxPurchased", {"templateId": "Currency:MtxPurchased",
                                                               "attributes": {"platform": "EpicPC"}, "quantity": 0})
        mtx["quantity"] = amount
    _edit_profile(acc["accountId"], "common_core", edit)
    log("Bot", f"{i.user} set {user(username)} V-Bucks to {amount}")
    await _reply(i, f"**{username}** has **{amount}** V-Bucks.")


@tree.command(name="setstat", description="[Dev] Set any profile stat (value is JSON or text)", guild=GUILD)
@dev_only
@app_commands.choices(profile=[app_commands.Choice(name=n, value=n) for n in ("athena", "common_core")])
async def setstat(i: discord.Interaction, username: str, profile: app_commands.Choice[str], stat: str, value: str):
    acc = accounts.by_name(username)
    if not acc:
        return await _reply(i, "No such account.", False)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    _edit_profile(acc["accountId"], profile.value, lambda p: p["stats"]["attributes"].__setitem__(stat, parsed))
    log("Bot", f"{i.user} set {user(username)} {profile.value}.{stat} = {parsed!r}")
    await _reply(i, f"**{username}** `{profile.value}.{stat}` = `{json.dumps(parsed)[:200]}`")


@tree.error
async def on_error(i: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        return await _reply(i, "Dev only.", False)
    log("Bot", f"/{i.command.name if i.command else '?'} failed: {error}", "error")
    if not i.response.is_done():
        await _reply(i, "Something went wrong.", False)


@client.event
async def on_ready():
    synced = await tree.sync(guild=GUILD)
    log("Bot", f"Logged in as {client.user}, {len(synced)} commands", "good")


async def start():
    if not CFG.get("token"):
        log("Bot", "No discord.token in config.json, bot disabled", "warn")
        return
    try:
        await client.start(CFG["token"])
    except Exception as e:
        log("Bot", f"Could not start: {e}", "error")
