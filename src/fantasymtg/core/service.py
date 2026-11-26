"""GameService: the single entry point any interface (Discord, CLI, web...) talks to.

Every public method takes plain identifiers (a channel key and a user id, both opaque
strings chosen by the interface) and returns structured results or raises GameError.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Callable

from . import errors
from .clock import Clock, SystemClock
from .config import DEFAULT_GAME_LENGTH, Setting, describe, find_setting, setting_names
from .models import (
    Game,
    GameStatus,
    Holding,
    PendingAction,
    PendingKind,
    PendingStatus,
    Player,
    TradeRecord,
)
from .pricing import CardQuote, PriceSource
from .repository import GameRepository
from .results import (
    BudgetToppedOff,
    ConfigApplied,
    ConfigureResult,
    Declined,
    GameEnded,
    Leaderboard,
    Notification,
    OutboxEntry,
    PendingTimedOut,
    Portfolio,
    PortfolioLine,
    ResponseOutcome,
    SettingChange,
    Standing,
    TradeExecuted,
    TradeProposal,
    WeeklyLeaderboard,
)

log = logging.getLogger(__name__)

CONFIRM_TIMEOUT = timedelta(minutes=30)
LEADERBOARD_INTERVAL = timedelta(days=7)
PENDING_RETENTION = timedelta(days=2)  # keep resolved requests so late reactions get a reply
OUTBOX_RETENTION = timedelta(days=14)  # give up on an announcement that can't be delivered
FINAL_PRICE_GRACE = timedelta(hours=6)  # how long the final standings wait for fresh prices
MAX_NAME_LENGTH = 32
MAX_QUANTITY = 1_000_000


class GameService:
    def __init__(
        self,
        repo: GameRepository,
        prices: PriceSource,
        clock: Clock | None = None,
        *,
        confirm_timeout: timedelta = CONFIRM_TIMEOUT,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self._repo = repo
        self._prices = prices
        self._clock = clock or SystemClock()
        self._confirm_timeout = confirm_timeout
        self._new_id = id_factory
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ------------------------------------------------------------------ setup phase

    async def create_game(self, channel_key: str, admin_id: str) -> Game:
        async with self._locks[channel_key]:
            existing = self._repo.find_by_channel(channel_key)
            if existing and existing.status != GameStatus.FINISHED:
                raise errors.GameAlreadyExists()
            game = Game(
                id=self._new_id(),
                channel_key=channel_key,
                admin_id=admin_id,
                created_at=self._clock.now(),
            )
            self._repo.save(game)
            return game

    def get_game(self, channel_key: str) -> Game:
        return self._require_game(channel_key)

    def describe_settings(self, channel_key: str) -> list[tuple[Setting, str]]:
        return describe(self._require_game(channel_key).config)

    async def configure(
        self, channel_key: str, user_id: str, name: str, raw_value: str
    ) -> ConfigureResult:
        async with self._locks[channel_key]:
            game = self._require_game(channel_key)
            self._require_admin(game, user_id)
            if game.status == GameStatus.FINISHED:
                raise errors.GameFinished()
            setting = self._require_setting(name)
            value = self._parse_setting(game, setting, raw_value)
            change = SettingChange(
                setting, setting.format(getattr(game.config, setting.attr)), setting.format(value)
            )
            if game.status == GameStatus.SETUP:
                game.config = game.config.with_value(setting.attr, value)
                self._repo.save(game)
                return ConfigureResult(change, applied=True)

            now = self._clock.now()
            pending = PendingAction(
                id=game.next_pending_id(),
                kind=PendingKind.CONFIG,
                user_id=user_id,
                created_at=now,
                expires_at=now + self._confirm_timeout,
                setting_name=setting.name,
                setting_value=raw_value,
            )
            game.pending[pending.id] = pending
            self._repo.save(game)
            return ConfigureResult(change, applied=False, pending=pending)

    async def join_game(self, channel_key: str, user_id: str, name: str) -> Player:
        name = " ".join(name.split())
        if not name:
            raise errors.InvalidName("a name is required")
        if len(name) > MAX_NAME_LENGTH:
            raise errors.InvalidName(f"names can be at most {MAX_NAME_LENGTH} characters")
        if any(ch in name for ch in "`*_~|\\"):
            raise errors.InvalidName("names can't contain formatting characters like ` * _ ~ |")
        async with self._locks[channel_key]:
            game = self._require_game(channel_key)
            if game.status == GameStatus.FINISHED:
                raise errors.GameFinished()
            if game.status == GameStatus.ACTIVE and not game.config.allow_late_join:
                raise errors.LateJoinNotAllowed()
            existing = game.player_by_user(user_id)
            if existing:
                raise errors.AlreadyJoined(existing.name)
            if len(game.players) >= game.config.max_player_count:
                raise errors.GameFull(game.config.max_player_count)
            if game.player_by_name(name):
                raise errors.NameTaken(name)
            # Mid-game joiners get startingBudget only; top-offs already paid aren't backdated.
            player = Player(user_id=user_id, name=name, balance=game.config.starting_budget)
            game.players.append(player)
            self._repo.save(game)
            return player

    async def start_game(self, channel_key: str, user_id: str) -> Game:
        async with self._locks[channel_key]:
            game = self._require_game(channel_key)
            self._require_admin(game, user_id)
            if game.status == GameStatus.FINISHED:
                raise errors.GameFinished()
            if game.status != GameStatus.SETUP:
                raise errors.GameNotInSetup()
            if not game.players:
                raise errors.NoPlayers()
            now = self._clock.now()
            end = game.config.end_date or now + DEFAULT_GAME_LENGTH
            if end <= now:
                raise errors.InvalidSettingValue(
                    "endDate", end.isoformat(), "must be after the game starts"
                )
            game.config = game.config.with_value("end_date", end)
            game.status = GameStatus.ACTIVE
            game.started_at = now
            game.last_top_off_at = now
            game.last_leaderboard_at = now
            for player in game.players:  # budget may have been reconfigured after joining
                player.balance = game.config.starting_budget
            self._repo.save(game)
            return game

    # ------------------------------------------------------------------ trading

    async def request_buy(
        self, channel_key: str, user_id: str, link: str, quantity: int
    ) -> TradeProposal:
        self._check_quantity(quantity)
        self._precheck_trader(channel_key, user_id)
        quote = await self._lookup(link)
        async with self._locks[channel_key]:
            game = self._require_game(channel_key)
            self._require_active(game)
            player = self._require_player(game, user_id)
            self._check_buy(game, player, quote, quote.price, quantity, game.config.trade_fee)
            pending = self._new_trade(game, PendingKind.BUY, user_id, quote, quantity)
            self._repo.save(game)
            cost = pending.subtotal + pending.fee
            return TradeProposal(pending, player.name, player.balance, player.balance - cost)

    async def request_sell(
        self, channel_key: str, user_id: str, link: str, quantity: int
    ) -> TradeProposal:
        self._check_quantity(quantity)
        self._precheck_trader(channel_key, user_id)
        quote = await self._lookup(link)
        async with self._locks[channel_key]:
            game = self._require_game(channel_key)
            self._require_active(game)
            player = self._require_player(game, user_id)
            self._check_sell(game, player, quote, quote.price, quantity, game.config.trade_fee)
            pending = self._new_trade(game, PendingKind.SELL, user_id, quote, quantity)
            self._repo.save(game)
            proceeds = pending.subtotal - pending.fee
            return TradeProposal(pending, player.name, player.balance, player.balance + proceeds)

    # ------------------------------------------------------------------ confirmations

    async def attach_external_ref(self, channel_key: str, pending_id: str, ref: str) -> None:
        """Associate an interface handle (e.g. the bot's message id) with a pending request."""
        async with self._locks[channel_key]:
            game = self._require_game(channel_key)
            pending = game.pending.get(pending_id)
            if pending:
                pending.external_ref = ref
                self._repo.save(game)

    def open_confirmations(self) -> list[tuple[str, PendingAction]]:
        """Unexpired requests with an interface handle, across all active games.

        Interfaces use this after reconnecting to look for responses they missed while
        offline. Expired requests are left out: when a response was given can't be known,
        and tick() announces those timeouts.
        """
        now = self._clock.now()
        return [
            (game.channel_key, p)
            for game in self._repo.list_active()
            for p in game.pending.values()
            if p.status == PendingStatus.OPEN and p.external_ref and now < p.expires_at
        ]

    def find_pending_id(self, channel_key: str, ref: str) -> str | None:
        game = self._repo.find_by_channel(channel_key)
        if not game:
            return None
        return next((p.id for p in game.pending.values() if p.external_ref == ref), None)

    async def respond(
        self, channel_key: str, pending_id: str, user_id: str, approve: bool
    ) -> ResponseOutcome | None:
        """Approve or decline a pending request.

        Returns None when the response should be silently ignored: unknown request,
        a user who isn't the requester, or a request that was already resolved.
        Raises ConfirmationExpired if the request timed out, or another GameError if a
        rule check fails at execution time (the request is then marked FAILED).
        """
        async with self._locks[channel_key]:
            game = self._repo.find_by_channel(channel_key)
            if not game:
                return None
            pending = game.pending.get(pending_id)
            if pending is None or pending.user_id != user_id:
                return None
            now = self._clock.now()
            if pending.status == PendingStatus.OPEN and now >= pending.expires_at:
                pending.status = PendingStatus.EXPIRED
                pending.resolved_at = now
                pending.timeout_announced = True  # the late-response reply covers it
                self._repo.save(game)
            if pending.status == PendingStatus.EXPIRED:
                raise errors.ConfirmationExpired(pending.expires_at)
            if pending.status != PendingStatus.OPEN:
                return None

            pending.resolved_at = now
            if not approve:
                pending.status = PendingStatus.CANCELLED
                self._repo.save(game)
                return Declined(pending)
            try:
                outcome = self._execute(game, pending, now)
            except errors.GameError:
                pending.status = PendingStatus.FAILED
                self._repo.save(game)
                raise
            pending.status = PendingStatus.CONFIRMED
            self._repo.save(game)
            return outcome

    # ------------------------------------------------------------------ queries

    async def portfolio(
        self, channel_key: str, requester_id: str, name: str | None = None
    ) -> Portfolio:
        game = self._require_game(channel_key)
        if name:
            if not game.player_by_name(name):
                raise errors.PlayerNotFound(name)
        elif not game.player_by_user(requester_id):
            raise errors.NotAPlayer()
        game = await self._refresh_prices(channel_key)
        player = game.player_by_name(name) if name else game.player_by_user(requester_id)
        assert player is not None
        costs = game.cost_basis(player.user_id)
        lines = [
            PortfolioLine(
                name=h.name,
                printing=h.printing,
                quantity=h.quantity,
                unit_price=h.last_price,
                value=h.quantity * (h.last_price or 0),
                url=h.url,
                avg_cost=(
                    (2 * costs[h.card_id] + h.quantity) // (2 * h.quantity)
                    if h.card_id in costs else None
                ),
            )
            for h in sorted(
                player.holdings.values(), key=lambda h: -(h.quantity * (h.last_price or 0))
            )
            if h.quantity > 0
        ]
        return Portfolio(
            player_name=player.name,
            balance=player.balance,
            lines=lines,
            holdings_value=player.holdings_value(),
            total=player.total_value(),
            final=game.status == GameStatus.FINISHED,
            cost_basis=sum(c for cid, c in costs.items() if cid in player.holdings),
        )

    async def leaderboard(self, channel_key: str) -> Leaderboard:
        self._require_game(channel_key)
        game = await self._refresh_prices(channel_key)
        return self._leaderboard(game)

    def player_names(self, channel_key: str) -> list[str]:
        game = self._repo.find_by_channel(channel_key)
        return [p.name for p in game.players] if game else []

    # ------------------------------------------------------------------ scheduled jobs

    async def tick(self) -> list[Notification]:
        """Run time-based rules for every game that has scheduled work. Call it every minute.

        Announcements are saved to each game's outbox together with the state change that
        caused them; deliver them with undelivered() and mark_delivered(). The return value
        lists the notifications queued by this call, which is handy in tests.
        """
        out: list[Notification] = []
        for game in self._repo.list_needing_attention():
            try:
                out.extend(await self._tick_game(game.id))
            except Exception:  # one broken game must not stop the others
                log.exception("tick failed for game %s (%s)", game.id, game.channel_key)
        return out

    def undelivered(self) -> list[OutboxEntry]:
        """Every queued announcement, oldest first within each game."""
        return [e for game in self._repo.list_needing_attention() for e in game.outbox]

    async def mark_delivered(self, entry: OutboxEntry) -> None:
        """Remove an announcement from its game's outbox once it has been sent (or dropped)."""
        snapshot = self._repo.get(entry.game_id)
        if snapshot is None:
            return
        async with self._locks[snapshot.channel_key]:
            game = self._repo.get(entry.game_id)
            if game is None:
                return
            before = len(game.outbox)
            game.outbox = [e for e in game.outbox if e.id != entry.id]
            if len(game.outbox) != before:
                self._repo.save(game)

    async def _tick_game(self, game_id: str) -> list[Notification]:
        now = self._clock.now()
        snapshot = self._repo.get(game_id)
        if snapshot is None:
            return []
        need_prices = snapshot.final_prices_due_since is not None
        if snapshot.status == GameStatus.ACTIVE:
            end = snapshot.config.end_date
            assert end is not None and snapshot.last_leaderboard_at is not None
            need_prices = now >= end or now >= snapshot.last_leaderboard_at + LEADERBOARD_INTERVAL
        quotes, prices_ok = await self._try_fetch_quotes(snapshot) if need_prices else ({}, True)

        out: list[Notification] = []
        async with self._locks[snapshot.channel_key]:
            game = self._repo.get(game_id)
            if game is None:
                return []
            key = game.channel_key
            if game.status == GameStatus.ACTIVE:
                cfg = game.config
                end = cfg.end_date
                assert end is not None and game.last_top_off_at and game.last_leaderboard_at
                ending = now >= end

                for pending in game.pending.values():
                    if pending.status == PendingStatus.OPEN and now >= pending.expires_at:
                        pending.status = PendingStatus.EXPIRED
                        pending.resolved_at = pending.expires_at
                        if not ending and not pending.timeout_announced:
                            out.append(PendingTimedOut(key, pending))
                        pending.timeout_announced = True
                for pid in [
                    pid
                    for pid, p in game.pending.items()
                    if p.status != PendingStatus.OPEN
                    and p.resolved_at
                    and now - p.resolved_at > PENDING_RETENTION
                ]:
                    del game.pending[pid]

                interval = timedelta(days=cfg.top_off_days)
                while game.last_top_off_at + interval <= now and game.last_top_off_at + interval < end:
                    game.last_top_off_at += interval
                    if cfg.top_off_budget > 0:
                        for player in game.players:
                            player.balance += cfg.top_off_budget
                        out.append(BudgetToppedOff(key, cfg.top_off_budget, game.last_top_off_at))

                if ending:
                    # Trading stops at endDate no matter what. The final announcement waits
                    # (below) until final prices are in, up to FINAL_PRICE_GRACE.
                    game.status = GameStatus.FINISHED
                    game.ended_at = end
                    game.final_prices_due_since = now
                elif now >= game.last_leaderboard_at + LEADERBOARD_INTERVAL:
                    self._apply_quotes(game, quotes)
                    while now >= game.last_leaderboard_at + LEADERBOARD_INTERVAL:
                        game.last_leaderboard_at += LEADERBOARD_INTERVAL
                    out.append(WeeklyLeaderboard(key, self._leaderboard(game)))

            due = game.final_prices_due_since
            if due is not None:
                if prices_ok or now - due >= FINAL_PRICE_GRACE:
                    if not prices_ok:
                        log.warning("final prices for game %s unavailable; using last known", game.id)
                    self._apply_quotes(game, quotes)
                    game.final_prices_due_since = None
                    out.append(GameEnded(key, self._leaderboard(game)))

            for note in out:
                game.outbox_seq += 1
                game.outbox.append(OutboxEntry(str(game.outbox_seq), game.id, now, note))
            stale = [e for e in game.outbox if now - e.created_at > OUTBOX_RETENTION]
            for entry in stale:
                log.warning("dropping announcement %s for game %s: undeliverable for %s",
                            entry.id, game.id, OUTBOX_RETENTION)
            if stale:
                game.outbox = [e for e in game.outbox if e not in stale]
            self._repo.save(game)
        return out

    # ------------------------------------------------------------------ rules

    @staticmethod
    def _check_buy(
        game: Game, player: Player, card: CardQuote, unit_price: int | None, quantity: int, fee: int
    ) -> None:
        if unit_price is None:
            raise errors.PriceUnavailable(card.name)
        cfg = game.config
        if cfg.unique_ownership:
            owner = game.owner_of(card.card_id, excluding=player.user_id)
            if owner:
                raise errors.CardOwnedByAnother(card.name, owner.name)
        if player.quantity_of(card.card_id) == 0 and player.distinct_cards() >= cfg.max_spec_number:
            raise errors.TooManyDistinctCards(cfg.max_spec_number)
        cost = unit_price * quantity + fee
        if cost > player.balance:
            raise errors.InsufficientFunds(needed=cost, available=player.balance)

    @staticmethod
    def _check_sell(
        game: Game, player: Player, card: CardQuote, unit_price: int | None, quantity: int, fee: int
    ) -> None:
        if unit_price is None:
            raise errors.PriceUnavailable(card.name)
        owned = player.quantity_of(card.card_id)
        if owned < quantity:
            raise errors.NotEnoughCopies(card.name, owned, quantity)
        if player.balance + unit_price * quantity - fee < 0:
            raise errors.InsufficientFunds(
                needed=fee, available=player.balance + unit_price * quantity
            )

    def _execute(self, game: Game, pending: PendingAction, now: datetime) -> ResponseOutcome:
        if pending.kind == PendingKind.CONFIG:
            if game.status == GameStatus.FINISHED:
                raise errors.GameFinished()
            setting = self._require_setting(pending.setting_name or "")
            value = self._parse_setting(game, setting, pending.setting_value or "")
            change = SettingChange(
                setting, setting.format(getattr(game.config, setting.attr)), setting.format(value)
            )
            game.config = game.config.with_value(setting.attr, value)
            return ConfigApplied(pending, change)

        self._require_active(game)
        player = self._require_player(game, pending.user_id)
        card = pending.card
        assert card is not None
        # Price and fee are the ones quoted, even if the market or the admin changed them since.
        if pending.kind == PendingKind.BUY:
            self._check_buy(game, player, card, pending.unit_price, pending.quantity, pending.fee)
            player.balance -= pending.subtotal + pending.fee
            holding = player.holdings.get(card.card_id)
            if holding:
                holding.quantity += pending.quantity
                holding.last_price = pending.unit_price
            else:
                player.holdings[card.card_id] = Holding.from_quote(
                    card, pending.quantity, pending.unit_price
                )
        else:
            self._check_sell(game, player, card, pending.unit_price, pending.quantity, pending.fee)
            player.balance += pending.subtotal - pending.fee
            holding = player.holdings[card.card_id]
            holding.quantity -= pending.quantity
            holding.last_price = pending.unit_price
            if holding.quantity == 0:
                del player.holdings[card.card_id]
        game.trades.append(
            TradeRecord(
                user_id=player.user_id,
                side=pending.kind.value,
                card_id=card.card_id,
                card_name=card.name,
                quantity=pending.quantity,
                unit_price=pending.unit_price,
                fee=pending.fee,
                executed_at=now,
            )
        )
        return TradeExecuted(pending, player.name, player.balance)

    # ------------------------------------------------------------------ helpers

    def _new_trade(
        self, game: Game, kind: PendingKind, user_id: str, quote: CardQuote, quantity: int
    ) -> PendingAction:
        now = self._clock.now()
        assert quote.price is not None
        pending = PendingAction(
            id=game.next_pending_id(),
            kind=kind,
            user_id=user_id,
            created_at=now,
            expires_at=now + self._confirm_timeout,
            card=quote,
            quantity=quantity,
            unit_price=quote.price,
            fee=game.config.trade_fee,
        )
        game.pending[pending.id] = pending
        return pending

    async def _lookup(self, link: str) -> CardQuote:
        quote = await self._prices.lookup(link)
        if quote.price is None:
            raise errors.PriceUnavailable(quote.name)
        return quote

    async def _try_fetch_quotes(self, game: Game) -> tuple[dict[str, CardQuote], bool]:
        """Fetch current prices for every held card. Returns (quotes, succeeded)."""
        ids = game.held_card_ids()
        if not ids:
            return {}, True
        try:
            return await self._prices.quotes(ids), True
        except errors.PriceServiceError:
            log.warning("price refresh failed for game %s", game.id)
            return {}, False

    async def _fetch_quotes(self, game: Game) -> dict[str, CardQuote]:
        if game.status == GameStatus.FINISHED:
            return {}
        ids = game.held_card_ids()
        if not ids:
            return {}
        try:
            return await self._prices.quotes(ids)
        except errors.PriceServiceError:
            log.warning("price refresh failed; using last known prices")
            return {}

    async def _refresh_prices(self, channel_key: str) -> Game:
        snapshot = self._require_game(channel_key)
        quotes = await self._fetch_quotes(snapshot)
        async with self._locks[channel_key]:
            game = self._require_game(channel_key)
            if quotes and game.status != GameStatus.FINISHED:
                self._apply_quotes(game, quotes)
                self._repo.save(game)
            return game

    @staticmethod
    def _apply_quotes(game: Game, quotes: dict[str, CardQuote]) -> None:
        for player in game.players:
            for holding in player.holdings.values():
                quote = quotes.get(holding.card_id)
                if quote and quote.price is not None:
                    holding.last_price = quote.price

    @staticmethod
    def _leaderboard(game: Game) -> Leaderboard:
        ordered = sorted(game.players, key=lambda p: (-p.total_value(), p.name.casefold()))
        standings: list[Standing] = []
        for i, p in enumerate(ordered):
            total = p.total_value()
            rank = standings[-1].rank if standings and standings[-1].total == total else i + 1
            standings.append(Standing(rank, p.user_id, p.name, p.balance, p.holdings_value(), total))
        return Leaderboard(standings, final=game.status == GameStatus.FINISHED)

    def _precheck_trader(self, channel_key: str, user_id: str) -> None:
        """Fail fast on obvious problems before making a network call."""
        game = self._require_game(channel_key)
        self._require_active(game)
        self._require_player(game, user_id)

    def _require_game(self, channel_key: str) -> Game:
        game = self._repo.find_by_channel(channel_key)
        if game is None:
            raise errors.NoGameInChannel()
        return game

    @staticmethod
    def _require_admin(game: Game, user_id: str) -> None:
        if game.admin_id != user_id:
            raise errors.NotAdmin()

    @staticmethod
    def _require_active(game: Game) -> None:
        if game.status == GameStatus.FINISHED:
            raise errors.GameFinished()
        if game.status != GameStatus.ACTIVE:
            raise errors.GameNotActive()

    @staticmethod
    def _require_player(game: Game, user_id: str) -> Player:
        player = game.player_by_user(user_id)
        if player is None:
            raise errors.NotAPlayer()
        return player

    @staticmethod
    def _require_setting(name: str) -> Setting:
        setting = find_setting(name)
        if setting is None:
            raise errors.UnknownSetting(name, setting_names())
        return setting

    @staticmethod
    def _check_quantity(quantity: int) -> None:
        if quantity < 1 or quantity > MAX_QUANTITY:
            raise errors.InvalidQuantity(quantity)

    def _parse_setting(self, game: Game, setting: Setting, raw: str) -> object:
        try:
            value = setting.parse(raw)
        except ValueError as exc:
            raise errors.InvalidSettingValue(setting.name, raw, str(exc)) from exc
        reason = setting.check(value)
        if reason is None and setting.attr == "max_player_count" and value < len(game.players):
            reason = f"{len(game.players)} players have already joined"
        if reason is None and setting.attr == "end_date" and value <= self._clock.now():
            reason = "must be in the future"
        if reason:
            raise errors.InvalidSettingValue(setting.name, raw, reason)
        return value
