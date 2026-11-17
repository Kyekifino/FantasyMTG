"""Game state. Plain dataclasses with no I/O so they are trivial to construct in tests."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from .config import GameConfig
from .pricing import CardQuote

if TYPE_CHECKING:
    from .results import OutboxEntry


class GameStatus(str, Enum):
    SETUP = "setup"  # created; players may join, admin configures freely
    ACTIVE = "active"  # started; trading open
    FINISHED = "finished"  # past endDate; read-only


class PendingKind(str, Enum):
    BUY = "buy"
    SELL = "sell"
    CONFIG = "config"


class PendingStatus(str, Enum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"  # confirmed, but a rule check failed at execution time


@dataclass
class Holding:
    card_id: str
    name: str
    set_code: str
    collector_number: str
    quantity: int
    last_price: int | None  # cents; most recent known price, used if the source is down
    finish: str = "nonfoil"
    url: str | None = None

    @property
    def label(self) -> str:
        return f"{self.name} ({self.set_code.upper()} #{self.collector_number})"

    @classmethod
    def from_quote(cls, quote: CardQuote, quantity: int, price: int | None) -> "Holding":
        return cls(
            card_id=quote.card_id,
            name=quote.name,
            set_code=quote.set_code,
            collector_number=quote.collector_number,
            quantity=quantity,
            last_price=price,
            finish=quote.finish,
            url=quote.url,
        )


@dataclass
class Player:
    user_id: str
    name: str
    balance: int  # cents of free budget
    holdings: dict[str, Holding] = field(default_factory=dict)

    def quantity_of(self, card_id: str) -> int:
        holding = self.holdings.get(card_id)
        return holding.quantity if holding else 0

    def distinct_cards(self) -> int:
        return sum(1 for h in self.holdings.values() if h.quantity > 0)

    def holdings_value(self) -> int:
        return sum(h.quantity * (h.last_price or 0) for h in self.holdings.values())

    def total_value(self) -> int:
        return self.balance + self.holdings_value()


@dataclass
class PendingAction:
    """A buy, sell or mid-game config change awaiting the owner's confirmation."""

    id: str
    kind: PendingKind
    user_id: str
    created_at: datetime
    expires_at: datetime
    status: PendingStatus = PendingStatus.OPEN
    # trades
    card: CardQuote | None = None
    quantity: int = 0
    unit_price: int = 0  # cents, locked in at quote time
    fee: int = 0
    # config changes
    setting_name: str | None = None
    setting_value: str | None = None  # raw text; re-parsed and re-validated on confirm
    # bookkeeping
    external_ref: str | None = None  # interface handle, e.g. a Discord message id
    timeout_announced: bool = False
    resolved_at: datetime | None = None

    @property
    def subtotal(self) -> int:
        return self.unit_price * self.quantity


@dataclass
class TradeRecord:
    user_id: str
    side: str  # "buy" or "sell"
    card_id: str
    card_name: str
    quantity: int
    unit_price: int
    fee: int
    executed_at: datetime


@dataclass
class Game:
    id: str
    channel_key: str  # interface-defined location, e.g. "discord:<guild>:<channel>"
    admin_id: str
    created_at: datetime
    config: GameConfig = field(default_factory=GameConfig)
    status: GameStatus = GameStatus.SETUP
    started_at: datetime | None = None
    ended_at: datetime | None = None
    players: list[Player] = field(default_factory=list)
    pending: dict[str, PendingAction] = field(default_factory=dict)
    trades: list[TradeRecord] = field(default_factory=list)
    last_top_off_at: datetime | None = None
    last_leaderboard_at: datetime | None = None
    pending_seq: int = 0
    # Announcements waiting to be delivered, oldest first (see GameService.undelivered).
    outbox: list["OutboxEntry"] = field(default_factory=list)
    outbox_seq: int = 0
    # Set when the game ended but final prices couldn't be fetched yet.
    final_prices_due_since: datetime | None = None

    @property
    def needs_attention(self) -> bool:
        """True while scheduled work remains: running, unsent announcements, or final prices."""
        return (
            self.status != GameStatus.FINISHED
            or bool(self.outbox)
            or self.final_prices_due_since is not None
        )

    def player_by_user(self, user_id: str) -> Player | None:
        return next((p for p in self.players if p.user_id == user_id), None)

    def player_by_name(self, name: str) -> Player | None:
        key = name.strip().casefold()
        return next((p for p in self.players if p.name.casefold() == key), None)

    def owner_of(self, card_id: str, *, excluding: str | None = None) -> Player | None:
        return next(
            (p for p in self.players if p.user_id != excluding and p.quantity_of(card_id) > 0),
            None,
        )

    def held_card_ids(self) -> set[str]:
        return {cid for p in self.players for cid, h in p.holdings.items() if h.quantity > 0}

    def next_pending_id(self) -> str:
        self.pending_seq += 1
        return str(self.pending_seq)
