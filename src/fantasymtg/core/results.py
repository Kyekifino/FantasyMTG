"""Values returned by GameService, and notifications produced by scheduled jobs.

Interfaces render these however they like; nothing here is pre-formatted text.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Union

from .config import Setting
from .models import PendingAction


@dataclass(frozen=True)
class SettingChange:
    setting: Setting
    old: str  # formatted
    new: str  # formatted


@dataclass(frozen=True)
class ConfigureResult:
    change: SettingChange
    applied: bool  # True if applied immediately (game not started yet)
    pending: PendingAction | None = None  # set when admin confirmation is required


@dataclass(frozen=True)
class TradeProposal:
    pending: PendingAction
    player_name: str
    balance_before: int
    balance_after: int


@dataclass(frozen=True)
class TradeExecuted:
    pending: PendingAction
    player_name: str
    balance_after: int


@dataclass(frozen=True)
class ConfigApplied:
    pending: PendingAction
    change: SettingChange


@dataclass(frozen=True)
class Declined:
    pending: PendingAction


ResponseOutcome = Union[TradeExecuted, ConfigApplied, Declined]


@dataclass(frozen=True)
class Standing:
    rank: int
    user_id: str
    name: str
    balance: int
    holdings_value: int
    total: int


@dataclass(frozen=True)
class PortfolioLine:
    name: str
    printing: str  # e.g. "(LEA #161)"; kept separate so it survives truncating the name
    quantity: int
    unit_price: int | None
    value: int
    url: str | None
    avg_cost: int | None = None  # average cents paid per copy, excluding fees

    @property
    def label(self) -> str:
        return f"{self.name} {self.printing}"

    @property
    def delta(self) -> int | None:
        """Change per copy from the average paid to the current price."""
        if self.unit_price is None or self.avg_cost is None:
            return None
        return self.unit_price - self.avg_cost


@dataclass(frozen=True)
class Portfolio:
    player_name: str
    balance: int
    lines: list[PortfolioLine]
    holdings_value: int
    total: int
    final: bool  # True when the game has ended and values are frozen
    cost_basis: int = 0  # total paid for the cards still held, excluding fees

    @property
    def unrealized(self) -> int:
        return self.holdings_value - self.cost_basis


@dataclass(frozen=True)
class Leaderboard:
    standings: list[Standing]
    final: bool


# --- notifications from GameService.tick() ------------------------------------------

@dataclass(frozen=True)
class PendingTimedOut:
    channel_key: str
    pending: PendingAction


@dataclass(frozen=True)
class BudgetToppedOff:
    channel_key: str
    amount: int
    at: datetime


@dataclass(frozen=True)
class WeeklyLeaderboard:
    channel_key: str
    leaderboard: Leaderboard


@dataclass(frozen=True)
class GameEnded:
    channel_key: str
    leaderboard: Leaderboard

    @property
    def winners(self) -> list[Standing]:
        return [s for s in self.leaderboard.standings if s.rank == 1]


Notification = Union[PendingTimedOut, BudgetToppedOff, WeeklyLeaderboard, GameEnded]


@dataclass(frozen=True)
class OutboxEntry:
    """A notification saved with its game until an interface confirms it was delivered."""

    id: str  # unique within the game, increasing in creation order
    game_id: str
    created_at: datetime
    notification: Notification
