"""The Discord client: slash commands, confirmation reactions and the scheduler loop.

This module contains no game rules. Every handler follows the same shape:
    1. turn the Discord interaction into (channel_key, user_id, args)
    2. call GameService
    3. render the result or GameError with fantasymtg.presentation and reply
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import aiohttp
import discord
from discord import app_commands

from ..core.config import SETTINGS
from ..core.errors import GameError
from ..core.results import PendingTimedOut
from ..core.service import GameService
from ..presentation import messages
from ..presentation.commands import COMMANDS
from .helpers import (
    Delivery,
    THUMBS_DOWN,
    THUMBS_UP,
    channel_key,
    classify_reaction,
    fit_message,
    owner_decision,
    parse_channel_key,
    user_mention,
)

log = logging.getLogger(__name__)

ALLOWED_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False)
_DOCS = {c.name: c for c in COMMANDS}


class Reply:
    """What a command handler wants to send back."""

    def __init__(self, text: str, confirm_pending_id: str | None = None) -> None:
        self.text = text
        self.confirm_pending_id = confirm_pending_id


Handler = Callable[[str, str], Awaitable[Reply]]


class FantasyMTGBot(discord.Client):
    def __init__(
        self,
        service: GameService,
        *,
        tick_seconds: float = 60.0,
    ) -> None:
        # Default intents include guild reactions and exclude privileged message content.
        super().__init__(intents=discord.Intents.default(), allowed_mentions=ALLOWED_MENTIONS)
        self.service = service
        # Commands work in any server the bot is added to, and only in servers (not DMs).
        self.tree = app_commands.CommandTree(
            self,
            allowed_contexts=app_commands.AppCommandContext(
                guild=True, dm_channel=False, private_channel=False
            ),
            allowed_installs=app_commands.AppInstallationType(guild=True, user=False),
        )
        self._cleaned_guild_commands = False
        self._tick_seconds = tick_seconds
        self._ticker: asyncio.Task | None = None
        self._reconcile_lock = asyncio.Lock()
        self._background: set[asyncio.Task] = set()
        _register_commands(self)

    async def setup_hook(self) -> None:
        # Global registration: one set of commands for every server, including servers the
        # bot joins later. Nothing needs to change on the host when it's added somewhere new.
        synced = await self.tree.sync()
        log.info("Registered %d slash commands globally", len(synced))
        self._ticker = asyncio.create_task(self._tick_loop())

    async def remove_stale_guild_commands(self) -> int:
        """Delete per-server command copies left by older versions (DISCORD_DEV_GUILD_ID).

        Those copies would show up next to the global commands as duplicates. Returns the
        number of servers cleaned.
        """
        cleaned = 0
        for guild in list(self.guilds):
            try:
                if not await self.tree.fetch_commands(guild=guild):
                    continue
                self.tree.clear_commands(guild=guild)
                await self.tree.sync(guild=guild)
                cleaned += 1
                log.info("Removed old server-specific commands from %s", guild.id)
            except discord.HTTPException:
                log.warning("could not check server-specific commands in %s", guild.id)
        return cleaned

    async def close(self) -> None:
        if self._ticker:
            self._ticker.cancel()
        await super().close()

    async def on_ready(self) -> None:
        # Fires on first login and again whenever a gateway session couldn't be resumed,
        # i.e. whenever reaction events may have been missed.
        log.info("Logged in as %s (%s) in %d servers", self.user, getattr(self.user, "id", "?"),
                 len(self.guilds))
        jobs = [self._reconcile_reactions()]
        if not self._cleaned_guild_commands:
            self._cleaned_guild_commands = True
            jobs.append(self.remove_stale_guild_commands())
        for job in jobs:
            task = asyncio.create_task(job)
            self._background.add(task)
            task.add_done_callback(self._background.discard)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("Added to server %s (%s)", guild.name, guild.id)

    # ------------------------------------------------------------------ commands

    async def run_command(self, interaction: discord.Interaction, handler: Handler) -> None:
        if interaction.guild_id is None or interaction.channel_id is None:
            await interaction.response.send_message(
                "Fantasy MTG games live in server channels, so please use this command there.",
                ephemeral=True,
            )
            return
        key = channel_key(interaction.guild_id, interaction.channel_id)
        await interaction.response.defer(thinking=True)
        try:
            reply = await handler(key, str(interaction.user.id))
        except GameError as exc:
            reply = Reply(messages.render_error(exc))
        except Exception:
            log.exception("command %s failed", interaction.command and interaction.command.name)
            reply = Reply("Sorry, something went wrong on my end. Please try again.")
        message = await interaction.edit_original_response(content=fit_message(reply.text))
        if reply.confirm_pending_id:
            await self.service.attach_external_ref(key, reply.confirm_pending_id, str(message.id))
            try:
                await message.add_reaction(THUMBS_UP)
                await message.add_reaction(THUMBS_DOWN)
            except discord.HTTPException:
                log.warning("could not add reactions (missing Add Reactions permission?)")

    # ------------------------------------------------------------------ reactions

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None or (self.user and payload.user_id == self.user.id):
            return
        approve = classify_reaction(str(payload.emoji))
        if approve is None:
            return
        key = channel_key(payload.guild_id, payload.channel_id)
        pending_id = self.service.find_pending_id(key, str(payload.message_id))
        if pending_id is None:
            return
        await self._apply_response(key, pending_id, str(payload.user_id), approve, payload.message_id)

    async def _apply_response(
        self, key: str, pending_id: str, user_id: str, approve: bool, message_id: int,
        note: str = "",
    ) -> None:
        try:
            outcome = await self.service.respond(key, pending_id, user_id, approve)
        except GameError as exc:
            text = messages.render_error(exc)
        else:
            if outcome is None:  # someone else's reaction, or already handled
                return
            text = messages.render_outcome(outcome)
        await self._send(key, f"{user_mention(user_id)} {note}{text}", reply_to=message_id)

    async def _reconcile_reactions(self) -> None:
        """Act on 👍/👎 reactions that were added while the bot was offline.

        Discord doesn't replay reaction events missed during downtime, so after every
        (re)connect we re-read the reactions on each still-open confirmation message.
        """
        async with self._reconcile_lock:
            for key, pending in self.service.open_confirmations():
                ids = parse_channel_key(key)
                if ids is None or pending.external_ref is None:
                    continue
                message_id = int(pending.external_ref)
                try:
                    channel = self.get_channel(ids[1]) or await self.fetch_channel(ids[1])
                    message = await channel.fetch_message(message_id)  # type: ignore[union-attr]
                    reactions = []
                    for reaction in message.reactions:
                        if classify_reaction(str(reaction.emoji)) is None:
                            continue
                        users = [str(u.id) async for u in reaction.users()]
                        reactions.append((str(reaction.emoji), users))
                except discord.HTTPException:
                    log.warning("could not check reactions on message %s", message_id)
                    continue
                decision = owner_decision(reactions, pending.user_id)
                if decision is None:
                    continue
                log.info("applying reaction missed while offline on message %s", message_id)
                await self._apply_response(
                    key, pending.id, pending.user_id, decision, message_id,
                    note=messages.MISSED_REACTION_NOTE,
                )

    # ------------------------------------------------------------------ scheduler

    async def _tick_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await self.service.tick()
            except Exception:
                log.exception("scheduled tick failed")
            try:
                await self.deliver_outbox()
            except Exception:
                log.exception("delivering announcements failed")
            await asyncio.sleep(self._tick_seconds)

    async def deliver_outbox(self) -> None:
        """Post queued announcements. Anything that can't be sent yet is retried next tick.

        Each game's announcements go out in order: once one fails to send, the rest of that
        game's queue waits for the next round.
        """
        blocked: set[str] = set()
        for entry in self.service.undelivered():
            if entry.game_id in blocked:
                continue
            note = entry.notification
            reply_to = None
            if isinstance(note, PendingTimedOut) and note.pending.external_ref:
                reply_to = int(note.pending.external_ref)
            result = await self._send(
                note.channel_key,
                messages.render_notification(note, mention=user_mention),
                reply_to=reply_to,
            )
            if result == Delivery.RETRY:
                blocked.add(entry.game_id)
            else:  # SENT, or DROP for a channel the bot can never post in
                await self.service.mark_delivered(entry)

    async def _send(self, key: str, text: str, reply_to: int | None = None) -> Delivery:
        ids = parse_channel_key(key)
        if ids is None:
            return Delivery.DROP
        _, channel_id = ids
        try:
            channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
            reference = (
                discord.MessageReference(
                    message_id=reply_to, channel_id=channel_id, fail_if_not_exists=False
                )
                if reply_to
                else None
            )
            await channel.send(fit_message(text), reference=reference)  # type: ignore[union-attr]
        except Exception as exc:
            result = classify_send_error(exc)
            if result == Delivery.RETRY:
                log.warning("could not post to channel %s, will retry: %s", channel_id, exc)
            else:
                log.warning("giving up posting to channel %s: %s", channel_id, exc)
            return result
        return Delivery.SENT


def classify_send_error(exc: BaseException) -> Delivery:
    """Decide whether a failed send is worth retrying.

    Network problems, timeouts, rate limits and Discord outages are temporary. Missing
    permissions, deleted channels and other 4xx rejections won't fix themselves.
    """
    if isinstance(exc, discord.HTTPException):
        if exc.status == 429 or exc.status >= 500:
            return Delivery.RETRY
        return Delivery.DROP
    if isinstance(exc, (OSError, asyncio.TimeoutError, aiohttp.ClientError, discord.GatewayNotFound)):
        return Delivery.RETRY
    log.error("unexpected error while posting", exc_info=exc)
    return Delivery.DROP


def _register_commands(bot: FantasyMTGBot) -> None:
    tree, service = bot.tree, bot.service
    setting_choices = [app_commands.Choice(name=s.name, value=s.name) for s in SETTINGS]
    help_choices = [app_commands.Choice(name=c.name, value=c.name) for c in COMMANDS]

    def desc(name: str) -> str:
        return _DOCS[name].summary[:100]

    @tree.command(name="creategame", description=desc("creategame"))
    async def creategame(interaction: discord.Interaction) -> None:
        async def handle(key: str, uid: str) -> Reply:
            game = await service.create_game(key, uid)
            return Reply(messages.render_game_created(game, mention=user_mention))
        await bot.run_command(interaction, handle)

    @tree.command(name="configure", description=desc("configure"))
    @app_commands.describe(variable="Setting to change (omit to list settings)", value="New value")
    @app_commands.choices(variable=setting_choices)
    async def configure(
        interaction: discord.Interaction,
        variable: app_commands.Choice[str] | None = None,
        value: str | None = None,
    ) -> None:
        async def handle(key: str, uid: str) -> Reply:
            if variable is None:
                return Reply(messages.render_settings(service.describe_settings(key)))
            if value is None:
                return Reply(f"Please provide a value for `{variable.value}`.")
            result = await service.configure(key, uid, variable.value, value)
            return Reply(
                messages.render_configure(result),
                confirm_pending_id=result.pending.id if result.pending else None,
            )
        await bot.run_command(interaction, handle)

    @tree.command(name="joingame", description=desc("joingame"))
    @app_commands.describe(name="Your display name in this game")
    async def joingame(interaction: discord.Interaction, name: app_commands.Range[str, 1, 32]) -> None:
        async def handle(key: str, uid: str) -> Reply:
            player = await service.join_game(key, uid, name)
            return Reply(messages.render_joined(player, service.get_game(key)))
        await bot.run_command(interaction, handle)

    @tree.command(name="startgame", description=desc("startgame"))
    async def startgame(interaction: discord.Interaction) -> None:
        async def handle(key: str, uid: str) -> Reply:
            return Reply(messages.render_started(await service.start_game(key, uid)))
        await bot.run_command(interaction, handle)

    @tree.command(name="buy", description=desc("buy"))
    @app_commands.describe(scryfall_link="Link to the card's Scryfall page", quantity="Number of copies")
    async def buy(
        interaction: discord.Interaction,
        scryfall_link: str,
        quantity: app_commands.Range[int, 1, 1_000_000],
    ) -> None:
        async def handle(key: str, uid: str) -> Reply:
            proposal = await service.request_buy(key, uid, scryfall_link, quantity)
            return Reply(messages.render_proposal(proposal), confirm_pending_id=proposal.pending.id)
        await bot.run_command(interaction, handle)

    @tree.command(name="sell", description=desc("sell"))
    @app_commands.describe(scryfall_link="Link to the card's Scryfall page", quantity="Number of copies")
    async def sell(
        interaction: discord.Interaction,
        scryfall_link: str,
        quantity: app_commands.Range[int, 1, 1_000_000],
    ) -> None:
        async def handle(key: str, uid: str) -> Reply:
            proposal = await service.request_sell(key, uid, scryfall_link, quantity)
            return Reply(messages.render_proposal(proposal), confirm_pending_id=proposal.pending.id)
        await bot.run_command(interaction, handle)

    @tree.command(name="portfolio", description=desc("portfolio"))
    @app_commands.describe(name="Player name (defaults to you)")
    async def portfolio(interaction: discord.Interaction, name: str | None = None) -> None:
        async def handle(key: str, uid: str) -> Reply:
            return Reply(messages.render_portfolio(await service.portfolio(key, uid, name)))
        await bot.run_command(interaction, handle)

    @portfolio.autocomplete("name")
    async def portfolio_names(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None or interaction.channel_id is None:
            return []
        names = service.player_names(channel_key(interaction.guild_id, interaction.channel_id))
        return [
            app_commands.Choice(name=n, value=n) for n in names if current.lower() in n.lower()
        ][:25]

    @tree.command(name="leaderboard", description=desc("leaderboard"))
    async def leaderboard(interaction: discord.Interaction) -> None:
        async def handle(key: str, uid: str) -> Reply:
            board = await service.leaderboard(key)
            title = "🏆 **Final leaderboard**" if board.final else "🏆 **Leaderboard**"
            return Reply(messages.render_leaderboard(board, title))
        await bot.run_command(interaction, handle)

    @tree.command(name="help", description=desc("help"))
    @app_commands.describe(command="Command to explain")
    @app_commands.choices(command=help_choices)
    async def help_(interaction: discord.Interaction, command: app_commands.Choice[str] | None = None) -> None:
        text = messages.render_command_help(command.value) if command else messages.render_help()
        await interaction.response.send_message(fit_message(text))
