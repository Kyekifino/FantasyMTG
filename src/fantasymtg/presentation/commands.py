"""Catalog of player commands, used for help text and by interfaces to register commands."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core.config import SETTINGS


@dataclass(frozen=True)
class Arg:
    name: str
    description: str
    required: bool = True


@dataclass(frozen=True)
class CommandDoc:
    name: str
    summary: str
    details: str
    args: tuple[Arg, ...] = field(default_factory=tuple)

    def usage(self, prefix: str = "/") -> str:
        parts = [f"{prefix}{self.name}"]
        for a in self.args:
            parts.append(f"<{a.name}>" if a.required else f"[{a.name}]")
        return " ".join(parts)


_SETTING_LINES = "\n".join(f"• `{s.name}` ({s.kind}): {s.description}" for s in SETTINGS)

COMMANDS: tuple[CommandDoc, ...] = (
    CommandDoc(
        "creategame",
        "Create a new Fantasy MTG game in this channel. You become its admin.",
        "Creates a game tied to this channel. Only one unfinished game can exist per channel. "
        "The person who creates the game is the admin, and only the admin can configure or "
        "start it. Creating a game does not add you as a player; use `joingame` for that.",
    ),
    CommandDoc(
        "configure",
        "Admin only: view settings, or change one.",
        "With no arguments, lists every setting and its current value. With a variable and a "
        "value, changes that setting. Before the game starts, changes apply immediately. Once "
        "the game is running, the bot asks the admin to confirm with a 👍 or 👎 reaction.\n\n"
        "Changes made mid-game apply from then on; for example, a new `startingBudget` does not "
        "change existing balances, and lowering `maxSpecNumber` does not force anyone to sell.\n\n"
        "**Settings**\n" + _SETTING_LINES,
        (Arg("variable", "Setting to change", required=False),
         Arg("value", "New value", required=False)),
    ),
    CommandDoc(
        "joingame",
        "Join the game in this channel under a display name.",
        "Adds you as a player to this channel's game, using the name you provide (names must be "
        "unique within the game), as long as there is room. Normally you can only join before "
        "the game starts. If the admin has set `allowLateJoin` to true, you can also join a "
        "game in progress, starting with `startingBudget` (top-offs already paid out aren't "
        "included).",
        (Arg("name", "Your display name in this game"),),
    ),
    CommandDoc(
        "startgame",
        "Admin only: start the game. Joining closes and trading opens.",
        "Starts the game immediately with the current players and settings. Every player "
        "receives the starting budget. If `endDate` has not been set, the game ends one year "
        "from now. Budget top-offs and the weekly leaderboard are counted from this moment.",
    ),
    CommandDoc(
        "buy",
        "Buy copies of a card using its Scryfall link.",
        "Quotes the purchase using the card's current TCGplayer market price as reported on "
        "Scryfall, plus the trade fee. React 👍 within 30 minutes to buy at the quoted price, or "
        "👎 to cancel. Each Scryfall page (printing/treatment) is a distinct card. Cards without "
        "a Scryfall price can't be traded. You need enough budget for the whole cost including "
        "the fee, and can hold at most `maxSpecNumber` different cards. If `uniqueOwnership` is "
        "on, you can't buy a card another player owns.",
        (Arg("scryfall_link", "e.g. https://scryfall.com/card/lea/161/lightning-bolt"),
         Arg("quantity", "Number of copies")),
    ),
    CommandDoc(
        "sell",
        "Sell copies of a card you own using its Scryfall link.",
        "Quotes the sale at the card's current price minus the trade fee. React 👍 within 30 "
        "minutes to sell at the quoted price, or 👎 to cancel. You can only sell copies you own, "
        "and the trade fee can't take your budget below zero.",
        (Arg("scryfall_link", "Link to the card's Scryfall page"),
         Arg("quantity", "Number of copies")),
    ),
    CommandDoc(
        "portfolio",
        "Show a player's cards, free budget and total value.",
        "Shows each card the player holds with quantity and current price, their free budget, "
        "and their total value (holdings plus free budget). Leave the name blank to see your own.",
        (Arg("name", "Player name (defaults to you)", required=False),),
    ),
    CommandDoc(
        "leaderboard",
        "Show all players ranked by total value.",
        "Ranks every player by total value (current card values plus free budget), highest "
        "first. A leaderboard is also posted automatically every week, and a final one when the "
        "game ends.",
    ),
    CommandDoc(
        "help",
        "List commands, or explain one in depth.",
        "With no arguments, lists every command. Give a command name for details on it.",
        (Arg("command", "Command to explain", required=False),),
    ),
)

_BY_NAME = {c.name: c for c in COMMANDS}


def find_command(name: str) -> CommandDoc | None:
    return _BY_NAME.get(name.strip().lower().lstrip("/!"))
