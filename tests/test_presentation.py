import inspect

import pytest

from fantasymtg.core import errors
from fantasymtg.core.results import GameEnded, Leaderboard, Standing
from fantasymtg.presentation import messages
from fantasymtg.presentation.commands import COMMANDS, find_command
from fantasymtg.presentation.table import render_table, truncate, truncate_keeping_suffix
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


async def test_portfolio_render_shows_paid_and_delta(started, prices):
    await buy(started, ALICE, "ring", 1)
    prices.set("ring", 50_00, "Sol Ring")
    text = messages.render_portfolio(await started.portfolio(CHANNEL, ALICE))
    assert "Paid" in text and "Δ" in text
    assert "$40.00" in text and "+$10.00" in text
    assert "Cards: **$50.00** (+$10.00, +25.0%)" in text


async def test_portfolio_rows_stay_narrow(started, prices):
    prices.set("c0", 1_00, "Asmoranomardicadaistinaculdacar, the Very Long Name")
    await buy(started, ALICE, "c0", 10)
    prices.set("c0", 1234_56, "Asmoranomardicadaistinaculdacar, the Very Long Name")
    text = messages.render_portfolio(await started.portfolio(CHANNEL, ALICE))
    table = text.split("```")[1]
    assert "+$1,233.56" in table and "$12,345.60" in table
    # Even with four-figure prices, rows stay within the target width and keep the set code.
    assert max(len(row) for row in table.splitlines()) <= messages.PORTFOLIO_WIDTH
    assert "Asmoranomardica… (TST #1)" in table


def test_truncate_keeping_suffix():
    assert truncate_keeping_suffix("Sol Ring", "(LEA #270)", 40) == "Sol Ring (LEA #270)"
    assert (truncate_keeping_suffix("Avacyn, Angel of Horror", "(FRC #7)", 29)
            == "Avacyn, Angel of Ho… (FRC #7)")
    out = truncate_keeping_suffix("Avacyn, Angel of Horror", "(FRC #7)", 29)
    assert len(out) == 29
    # no room for the suffix plus a few letters: plain truncation
    assert truncate_keeping_suffix("Avacyn", "(FRC #7)", 10) == "Avacyn (F…"


async def test_portfolio_gives_spare_width_to_card_names(started, prices):
    name = "Asmoranomardicadaistinaculda"
    prices.set("c0", 1_00, name)
    await buy(started, ALICE, "c0", 1)
    text = messages.render_portfolio(await started.portfolio(CHANNEL, ALICE))
    table = text.split("```")[1]
    assert f"{name} (TST #1)" in table  # 37 characters: wider than the old fixed 28
    assert max(len(row) for row in table.splitlines()) <= messages.PORTFOLIO_WIDTH
