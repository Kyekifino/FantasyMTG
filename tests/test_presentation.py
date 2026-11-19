import inspect

import pytest

from fantasymtg.core import errors
from fantasymtg.core.results import GameEnded, Leaderboard, Standing
from fantasymtg.presentation import messages
from fantasymtg.presentation.commands import COMMANDS, find_command
from fantasymtg.presentation.table import render_table, truncate
from conftest import ALICE, CHANNEL, buy


def _error_instances():
    """Build one instance of every GameError subclass with dummy args."""
    out = []
    for _, cls in inspect.getmembers(errors, inspect.isclass):
        if issubclass(cls, errors.GameError) and cls is not errors.GameError:
            params = [p for p in inspect.signature(cls.__init__).parameters.values() if p.name != "self"]
            dummy = {"known": ["a"], "max_players": 1, "limit": 1, "owned": 0, "requested": 1,
                     "needed": 1, "available": 0}
            out.append(cls(*[dummy.get(p.name, "x") for p in params]))
    return out


@pytest.mark.parametrize("exc", _error_instances(), ids=lambda e: type(e).__name__)
def test_every_error_has_specific_wording(exc):
    text = messages.render_error(exc)
    assert text != "Sorry, something went wrong with that request."


def test_render_table_alignment():
    table = render_table(("A", "Num"), [("x", "1"), ("long", "100")], align_right=(1,))
    assert table.splitlines() == ["```", "A     Num", "----  ---", "x       1", "long  100", "```"]


def test_truncate():
    assert truncate("abcdef", 4) == "abc…"
    assert truncate("abc", 4) == "abc"


def test_help_lists_every_command_from_spec():
    text = messages.render_help()
    for name in ["creategame", "configure", "joingame", "startgame", "buy", "sell",
                 "portfolio", "leaderboard", "help"]:
        assert f"/{name}" in text


def test_command_help():
    assert "maxSpecNumber" in messages.render_command_help("configure")
    assert "Usage: `/buy <scryfall_link> <quantity>`" in messages.render_command_help("/buy")
    assert "don't know" in messages.render_command_help("dance")
    assert find_command("!SELL").name == "sell"
    assert all(len(c.summary) <= 100 for c in COMMANDS)  # Discord description limit


async def test_proposal_and_portfolio_render(started):
    proposal = await started.request_buy(CHANNEL, ALICE, "ring", 1)
    text = messages.render_proposal(proposal)
    assert "$40.00" in text and "$42.00" in text and "👍" in text
    await started.respond(CHANNEL, proposal.pending.id, ALICE, True)
    await buy(started, ALICE, "bolt", 2)
    p = messages.render_portfolio(await started.portfolio(CHANNEL, ALICE))
    assert "Sol Ring" in p and "Total value: **$96.00**" in p


def test_game_end_message_congratulates_winner_and_ties():
    single = GameEnded("c", Leaderboard([Standing(1, "u1", "Ann", 0, 0, 500), Standing(2, "u2", "Ben", 0, 0, 1)], True))
    assert "Congratulations to **Ann**" in messages.render_notification(single)
    tie = GameEnded("c", Leaderboard([Standing(1, "u1", "Ann", 0, 0, 5), Standing(1, "u2", "Ben", 0, 0, 5)], True))
    assert "tie" in messages.render_notification(tie)


async def test_late_join_welcome_mentions_budget(started):
    change = await started.configure(CHANNEL, "admin", "allowLateJoin", "true")
    await started.respond(CHANNEL, change.pending.id, "admin", True)
    carol = await started.join_game(CHANNEL, "carol", "Carol")
    text = messages.render_joined(carol, started.get_game(CHANNEL))
    assert "already underway" in text and "$100.00" in text


def test_mentions_are_injected():
    from fantasymtg.core.models import Game
    from conftest import START
    game = Game(id="g", channel_key="c", admin_id="42", created_at=START)
    assert "<@42>" in messages.render_game_created(game, mention=lambda u: f"<@{u}>")
    assert "<@" not in messages.render_game_created(game)
