"""Polite, interface-neutral wording for every core result, error and notification."""
from __future__ import annotations

from typing import Callable

from ..core import errors
from ..core.config import Setting, format_datetime
from ..core.models import Game, GameStatus, PendingAction, PendingKind, Player
from ..core.money import format_change, format_money
from ..core.results import (
    BudgetToppedOff,
    ConfigApplied,
    ConfigureResult,
    Declined,
    GameEnded,
    Leaderboard,
    Notification,
    PendingTimedOut,
    Portfolio,
    ResponseOutcome,
    TradeExecuted,
    TradeProposal,
    WeeklyLeaderboard,
)
from .commands import COMMANDS, find_command
from .table import render_table, truncate, truncate_keeping_suffix

CONFIRM_HINT = "React 👍 to confirm or 👎 to cancel within 30 minutes."
PORTFOLIO_WIDTH = 72  # widest portfolio row, in characters, before card names are shortened
PORTFOLIO_MIN_CARD_WIDTH = 24
MISSED_REACTION_NOTE ="I was offline when you reacted, but I've caught up. "
m = format_money
Mention = Callable[[str], str]


def _plain(user_id: str) -> str:
    return user_id


# --- errors --------------------------------------------------------------------------

def render_error(exc: errors.GameError, prefix: str = "/") -> str:
    e = errors
    if isinstance(exc, e.NoGameInChannel):
        return f"There's no Fantasy MTG game in this channel yet. Start one with `{prefix}creategame`."
    if isinstance(exc, e.GameAlreadyExists):
        return "This channel already has a game in progress. Only one game can run per channel at a time."
    if isinstance(exc, e.NotAdmin):
        return "Sorry, only the game's admin can do that."
    if isinstance(exc, e.GameNotInSetup):
        return "This game has already started, so it can't be started again."
    if isinstance(exc, e.LateJoinNotAllowed):
        return ("Sorry, this game has already started, so you can't join it. "
                "The admin can allow late joins by setting `allowLateJoin` to true.")
    if isinstance(exc, e.GameNotActive):
        return "The game hasn't started yet, so trading isn't open. The admin can start it once everyone has joined."
    if isinstance(exc, e.GameFinished):
        return "This game has ended, so that's no longer possible. Thanks for playing!"
    if isinstance(exc, e.GameFull):
        return f"Sorry, this game is full ({exc.max_players} players), so you can't join."
    if isinstance(exc, e.AlreadyJoined):
        return f"You're already in this game as **{exc.name}**."
    if isinstance(exc, e.NameTaken):
        return f"Sorry, someone in this game is already called **{exc.name}**. Please pick another name."
    if isinstance(exc, e.InvalidName):
        return f"Sorry, that name won't work: {exc.reason}."
    if isinstance(exc, e.NotAPlayer):
        return f"You're not a player in this game. Players join before the game starts with `{prefix}joingame`."
    if isinstance(exc, e.PlayerNotFound):
        return f"I couldn't find a player named **{exc.name}** in this game."
    if isinstance(exc, e.NoPlayers):
        return "The game needs at least one player before it can start."
    if isinstance(exc, e.UnknownSetting):
        names = ", ".join(f"`{n}`" for n in exc.known)
        return f"`{exc.name}` isn't a setting I know. Available settings: {names}."
    if isinstance(exc, e.InvalidSettingValue):
        return f"`{exc.raw}` isn't a valid value for `{exc.name}`: {exc.reason}."
    if isinstance(exc, e.InvalidQuantity):
        return "Please choose a quantity of at least 1."
    if isinstance(exc, e.InvalidCardLink):
        return ("That doesn't look like a Scryfall card link. Please paste the card page URL, "
                "like `https://scryfall.com/card/lea/161/lightning-bolt`.")
    if isinstance(exc, e.CardNotFound):
        return "I couldn't find that card on Scryfall. Please double-check the link."
    if isinstance(exc, e.PriceUnavailable):
        return f"Sorry, Scryfall doesn't report a price for **{exc.card_name}**, so it can't be bought or sold."
    if isinstance(exc, e.PriceServiceError):
        return "Sorry, I couldn't reach Scryfall just now. Please try again in a moment."
    if isinstance(exc, e.InsufficientFunds):
        return (f"Sorry, you have insufficient funds for that trade. It needs {m(exc.needed)} "
                f"(including the trade fee) and you have {m(exc.available)} available.")
    if isinstance(exc, e.CardOwnedByAnother):
        return f"Sorry, **{exc.card_name}** is already owned by **{exc.owner_name}**, and this game only allows one owner per card."
    if isinstance(exc, e.TooManyDistinctCards):
        return (f"Sorry, you already hold {exc.limit} different cards, which is the limit. "
                "Sell one of them before buying something new.")
    if isinstance(exc, e.NotEnoughCopies):
        if exc.owned == 0:
            return f"Sorry, you can't sell **{exc.card_name}** because you don't own any copies."
        return (f"Sorry, you can't sell {exc.requested} copies of **{exc.card_name}**; "
                f"you only own {exc.owned}.")
    if isinstance(exc, e.ConfirmationExpired):
        return "Sorry, it's been more than 30 minutes since that request, so it has expired. Please try again."
    return "Sorry, something went wrong with that request."


# --- setup ---------------------------------------------------------------------------

def render_game_created(game: Game, prefix: str = "/", mention: Mention = _plain) -> str:
    return (
        "🃏 **A new game of Fantasy MTG has been created!**\n"
        f"{mention(game.admin_id)} is the admin. Players can join with `{prefix}joingame <name>`, "
        f"the admin can adjust settings with `{prefix}configure`, then start with `{prefix}startgame`."
    )


def render_settings(settings: list[tuple[Setting, str]]) -> str:
    rows = [(s.name, value, s.kind) for s, value in settings]
    return "**Game settings**\n" + render_table(("Variable", "Value", "Type"), rows)


def render_configure(result: ConfigureResult) -> str:
    c = result.change
    if result.applied:
        return f"✅ `{c.setting.name}` changed from **{c.old}** to **{c.new}**."
    return (f"⚙️ Change `{c.setting.name}` from **{c.old}** to **{c.new}**?\n"
            f"The game is in progress, so the admin needs to confirm. {CONFIRM_HINT}")


def render_joined(player: Player, game: Game) -> str:
    text = (f"👋 Welcome, **{player.name}**! You're player {len(game.players)} of "
            f"{game.config.max_player_count}.")
    if game.status == GameStatus.ACTIVE:
        text += f" The game is already underway, so you start trading now with {m(player.balance)}."
    return text


def render_started(game: Game) -> str:
    cfg = game.config
    names = ", ".join(f"**{p.name}**" for p in game.players)
    return (
        "🚀 **The game has begun!**\n"
        f"Players: {names}\n"
        f"Everyone starts with {m(cfg.starting_budget)}. Each trade costs a {m(cfg.trade_fee)} fee, "
        f"and you can hold up to {cfg.max_spec_number} different cards.\n"
        f"Every {cfg.top_off_days} days everyone receives {m(cfg.top_off_budget)} more. "
        f"The game ends on **{format_datetime(cfg.end_date)}**. Good luck!"
    )


# --- trading -------------------------------------------------------------------------

def _finish_note(pending: PendingAction) -> str:
    card = pending.card
    return f" ({card.finish} price)" if card and card.finish != "nonfoil" else ""


def render_proposal(p: TradeProposal) -> str:
    pend = p.pending
    card = pend.card
    assert card is not None
    name = f"[{card.label}]({card.url})" if card.url else card.label
    if pend.kind == PendingKind.BUY:
        lines = [
            f"🛒 **{p.player_name}**, confirm this purchase:",
            f"Buy **{pend.quantity}× {name}** at {m(pend.unit_price)} each{_finish_note(pend)}",
            f"Cards: {m(pend.subtotal)} + fee {m(pend.fee)} = **{m(pend.subtotal + pend.fee)}**",
        ]
    else:
        lines = [
            f"💰 **{p.player_name}**, confirm this sale:",
            f"Sell **{pend.quantity}× {name}** at {m(pend.unit_price)} each{_finish_note(pend)}",
            f"Cards: {m(pend.subtotal)} − fee {m(pend.fee)} = **{m(pend.subtotal - pend.fee)}**",
        ]
    lines.append(f"Budget: {m(p.balance_before)} → {m(p.balance_after)}")
    lines.append(CONFIRM_HINT)
    return "\n".join(lines)


def render_outcome(outcome: ResponseOutcome) -> str:
    if isinstance(outcome, Declined):
        kind = {PendingKind.BUY: "Purchase", PendingKind.SELL: "Sale"}.get(outcome.pending.kind, "Change")
        return f"👍 No problem, {kind.lower()} cancelled."
    if isinstance(outcome, ConfigApplied):
        c = outcome.change
        return f"✅ `{c.setting.name}` changed from **{c.old}** to **{c.new}**."
    assert isinstance(outcome, TradeExecuted)
    pend = outcome.pending
    card = pend.card
    assert card is not None
    verb = "bought" if pend.kind == PendingKind.BUY else "sold"
    return (f"✅ **{outcome.player_name}** {verb} {pend.quantity}× **{card.label}** at "
            f"{m(pend.unit_price)} each. Free budget is now {m(outcome.balance_after)}.")


# --- queries -------------------------------------------------------------------------

def render_portfolio(p: Portfolio) -> str:
    title = f"📒 **{p.player_name}'s portfolio**" + (" (final)" if p.final else "")
    if p.lines:
        headers = ("Card", "Qty", "Paid", "Price", "Δ", "Value")
        numbers = [
            (str(line.quantity), _money_or_na(line.avg_cost), _money_or_na(line.unit_price),
             _money_or_na(line.delta, format_change), m(line.value))
            for line in p.lines
        ]
        # The card column gets whatever width the number columns leave, so names are only
        # shortened when the row would otherwise wrap in a typical desktop Discord window.
        others = sum(max(len(h), *(len(r[i]) for r in numbers)) for i, h in enumerate(headers[1:]))
        card_width = max(PORTFOLIO_MIN_CARD_WIDTH,
                         PORTFOLIO_WIDTH - others - 2 * (len(headers) - 1))
        rows = [
            (truncate_keeping_suffix(line.name, line.printing, card_width), *nums)
            for line, nums in zip(p.lines, numbers)
        ]
        table = render_table(headers, rows, align_right=(1, 2, 3, 4, 5))
        cards = (f"Cards: **{m(p.holdings_value)}** "
                 f"({format_change(p.unrealized)}{_percent(p.unrealized, p.cost_basis)})")
    else:
        table = "_No cards yet._"
        cards = f"Cards: **{m(p.holdings_value)}**"
    return (f"{title}\n{table}\n"
            f"{cards} · Free budget: **{m(p.balance)}** · Total value: **{m(p.total)}**")


def _money_or_na(cents: int | None, fmt: Callable[[int], str] = format_money) -> str:
    return fmt(cents) if cents is not None else "n/a"


def _percent(change: int, base: int) -> str:
    return f", {change / base * 100:+.1f}%" if base > 0 else ""


def render_leaderboard(board: Leaderboard, title: str = "🏆 **Leaderboard**") -> str:
    if not board.standings:
        return f"{title}\n_No players yet._"
    rows = [
        (f"#{s.rank}", truncate(s.name, 24), m(s.holdings_value), m(s.balance), m(s.total))
        for s in board.standings
    ]
    return f"{title}\n" + render_table(
        ("Rank", "Player", "Cards", "Budget", "Total"), rows, align_right=(2, 3, 4)
    )


def render_help(prefix: str = "/") -> str:
    lines = ["**Fantasy MTG commands**"]
    lines += [f"`{c.usage(prefix)}` — {c.summary}" for c in COMMANDS]
    lines.append(f"\nUse `{prefix}help <command>` for details on any command.")
    return "\n".join(lines)


def render_command_help(name: str, prefix: str = "/") -> str:
    cmd = find_command(name)
    if cmd is None:
        return f"I don't know a command called `{name}`. Try `{prefix}help` for the full list."
    parts = [f"**{prefix}{cmd.name}**", f"Usage: `{cmd.usage(prefix)}`", "", cmd.details]
    if cmd.args:
        parts.append("\n**Arguments**")
        parts += [f"• `{a.name}`{'' if a.required else ' (optional)'}: {a.description}" for a in cmd.args]
    return "\n".join(parts)


# --- notifications -------------------------------------------------------------------

def render_notification(n: Notification, mention: Mention = _plain) -> str:
    if isinstance(n, PendingTimedOut):
        pend = n.pending
        who = mention(pend.user_id)
        if pend.kind == PendingKind.CONFIG:
            what = f"the change to `{pend.setting_name}`"
        else:
            verb = "buy" if pend.kind == PendingKind.BUY else "sell"
            what = f"your request to {verb} {pend.quantity}× **{pend.card.label if pend.card else 'card'}**"
        return f"⌛ {who}, {what} wasn't confirmed within 30 minutes, so it has been cancelled. Feel free to try again."
    if isinstance(n, BudgetToppedOff):
        return f"💸 **Budget top-off!** Every player has received {m(n.amount)}."
    if isinstance(n, WeeklyLeaderboard):
        return render_leaderboard(n.leaderboard, "📅 **Weekly leaderboard**")
    if isinstance(n, GameEnded):
        board = render_leaderboard(n.leaderboard, "🏁 **The game is over! Final standings**")
        winners = n.winners
        if not winners:
            return board
        if len(winners) == 1:
            w = winners[0]
            cheer = f"🎉 Congratulations to **{w.name}** for winning with {m(w.total)}!"
        else:
            names = " and ".join(f"**{w.name}**" for w in winners)
            cheer = f"🎉 It's a tie! Congratulations to {names}, each finishing with {m(winners[0].total)}!"
        return f"{board}\n{cheer}"
    return str(n)

