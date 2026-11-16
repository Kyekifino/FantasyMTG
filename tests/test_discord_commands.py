"""Slash commands must work in any server the bot joins, with no per-server setup."""
import discord
import pytest

from fantasymtg.discord_bot.bot import FantasyMTGBot


@pytest.fixture
def bot(service):
    return FantasyMTGBot(service)


def test_commands_are_global_and_server_only(bot):
    payloads = [cmd.to_dict(bot.tree) for cmd in bot.tree.get_commands()]
    assert len(payloads) == 9
    for p in payloads:
        assert p.get("guild_ids") in (None, [])  # not tied to any one server
        assert p["contexts"] == [0]  # usable in servers only, not DMs
        assert p["integration_types"] == [0]  # installed to servers, not users


class FakeGuild:
    def __init__(self, gid):
        self.id = gid


async def test_remove_stale_guild_commands(bot, monkeypatch):
    guilds = [FakeGuild(1), FakeGuild(2), FakeGuild(3)]
    monkeypatch.setattr(FantasyMTGBot, "guilds", property(lambda self: guilds))
    remote = {1: ["old /buy"], 2: [], 3: ["old /sell"]}
    synced, cleared = [], []

    async def fetch_commands(*, guild=None):
        if guild.id == 3:
            class Resp:
                status, reason = 403, "Forbidden"
            raise discord.Forbidden(Resp(), "Missing Access")
        return remote[guild.id]

    async def sync(*, guild=None):
        synced.append(guild.id)
        return []

    monkeypatch.setattr(bot.tree, "fetch_commands", fetch_commands)
    monkeypatch.setattr(bot.tree, "sync", sync)
    monkeypatch.setattr(bot.tree, "clear_commands", lambda *, guild: cleared.append(guild.id))

    assert await bot.remove_stale_guild_commands() == 1
    assert cleared == synced == [1]  # only server 1 had leftovers; 3 failed gracefully
