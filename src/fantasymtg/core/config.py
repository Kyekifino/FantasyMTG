"""Adjustable game variables and how to parse/validate them."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable

from .money import format_money, parse_money

DEFAULT_GAME_LENGTH = timedelta(days=365)


@dataclass(frozen=True)
class GameConfig:
    # None means "one year after the game starts"; resolved by start_game.
    end_date: datetime | None = None
    max_player_count: int = 10
    starting_budget: int = 100_00  # cents
    max_spec_number: int = 5
    trade_fee: int = 2_00
    top_off_days: int = 30
    top_off_budget: int = 20_00
    unique_ownership: bool = True
    allow_late_join: bool = False

    def with_value(self, attr: str, value: Any) -> "GameConfig":
        return replace(self, **{attr: value})


# --- parsers -------------------------------------------------------------------------

def _parse_int(raw: str) -> int:
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError("expected a whole number") from exc


def _parse_bool(raw: str) -> bool:
    text = raw.strip().lower()
    if text in {"true", "yes", "y", "on", "1"}:
        return True
    if text in {"false", "no", "n", "off", "0"}:
        return False
    raise ValueError("expected true or false")


def _parse_datetime(raw: str) -> datetime:
    """Accept YYYY-MM-DD (midnight UTC) or a full ISO-8601 timestamp."""
    text = raw.strip()
    try:
        if len(text) == 10:
            parsed = datetime.combine(datetime.strptime(text, "%Y-%m-%d").date(), time(0, 0))
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("expected a date like 2027-01-31") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _min(minimum: int) -> Callable[[Any], str | None]:
    def check(value: Any) -> str | None:
        return None if value >= minimum else f"must be at least {minimum}"
    return check


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return "1 year after the game starts"
    if value.time() == time(0, 0):
        return value.strftime("%Y-%m-%d")
    return value.strftime("%Y-%m-%d %H:%M UTC")


# --- registry ------------------------------------------------------------------------

@dataclass(frozen=True)
class Setting:
    name: str  # what players type, e.g. "tradeFee"
    attr: str  # GameConfig attribute
    kind: str  # "integer", "dollars", "boolean", "date"
    description: str
    parse: Callable[[str], Any]
    format: Callable[[Any], str]
    check: Callable[[Any], str | None] = lambda _v: None


SETTINGS: tuple[Setting, ...] = (
    Setting("endDate", "end_date", "date",
            "When the game ends (YYYY-MM-DD, midnight UTC).",
            _parse_datetime, format_datetime),
    Setting("maxPlayerCount", "max_player_count", "integer",
            "Maximum number of players who may join.",
            _parse_int, str, _min(1)),
    Setting("startingBudget", "starting_budget", "dollars",
            "Budget each player starts with.",
            parse_money, format_money, _min(0)),
    Setting("maxSpecNumber", "max_spec_number", "integer",
            "Maximum number of different cards a player may hold at once.",
            _parse_int, str, _min(1)),
    Setting("tradeFee", "trade_fee", "dollars",
            "Fee charged on every buy and every sell.",
            parse_money, format_money, _min(0)),
    Setting("topOffDays", "top_off_days", "integer",
            "Days between budget top-offs.",
            _parse_int, str, _min(1)),
    Setting("topOffBudget", "top_off_budget", "dollars",
            "Amount added to every player's budget at each top-off.",
            parse_money, format_money, _min(0)),
    Setting("uniqueOwnership", "unique_ownership", "boolean",
            "If true, only one player may own a given card at a time.",
            _parse_bool, lambda v: "true" if v else "false"),
    Setting("allowLateJoin", "allow_late_join", "boolean",
            "If true, players may join after the game has started (with startingBudget).",
            _parse_bool, lambda v: "true" if v else "false"),
)

_BY_NAME = {s.name.lower(): s for s in SETTINGS}


def find_setting(name: str) -> Setting | None:
    return _BY_NAME.get(name.strip().lower())


def setting_names() -> list[str]:
    return [s.name for s in SETTINGS]


def describe(config: GameConfig) -> list[tuple[Setting, str]]:
    return [(s, s.format(getattr(config, s.attr))) for s in SETTINGS]
