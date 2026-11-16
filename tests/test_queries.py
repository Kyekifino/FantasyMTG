import pytest

from fantasymtg.core import errors
from conftest import ALICE, BOB, CHANNEL, buy


async def test_portfolio_defaults_to_requester(started):
    await buy(started, ALICE, "ring", 1)
    await buy(started, ALICE, "bolt", 3)
    p = await started.portfolio(CHANNEL, ALICE)
    assert p.player_name == "Alice"
    assert [(line.label.split(" (")[0], line.quantity) for line in p.lines] == [
        ("Sol Ring", 1),
        ("Lightning Bolt", 3),
    ]
    assert p.balance == 100_00 - 40_00 - 2_00 - 3_00 - 2_00
    assert p.holdings_value == 43_00
    assert p.total == p.balance + p.holdings_value


async def test_portfolio_by_name_uses_current_prices(started, prices):
    await buy(started, BOB, "ring", 1)
    prices.set("ring", 60_00, "Sol Ring")
    p = await started.portfolio(CHANNEL, ALICE, "bob")
    assert p.player_name == "Bob"
    assert p.lines[0].unit_price == 60_00
    assert p.total == 58_00 + 60_00


async def test_portfolio_unknown_player(started):
    with pytest.raises(errors.PlayerNotFound):
        await started.portfolio(CHANNEL, ALICE, "Zed")


async def test_portfolio_for_non_player_without_name(started):
    with pytest.raises(errors.NotAPlayer):
        await started.portfolio(CHANNEL, "stranger")


async def test_leaderboard_ordering(started, prices):
    await buy(started, ALICE, "ring", 1)
    prices.set("ring", 10_00, "Sol Ring")  # Alice lost money
    board = await started.leaderboard(CHANNEL)
    assert [(s.rank, s.name, s.total) for s in board.standings] == [
        (1, "Bob", 100_00),
        (2, "Alice", 58_00 + 10_00),
    ]
    assert not board.final


async def test_leaderboard_before_start(service):
    await service.create_game(CHANNEL, "admin")
    await service.join_game(CHANNEL, ALICE, "Alice")
    board = await service.leaderboard(CHANNEL)
    assert [s.name for s in board.standings] == ["Alice"]


async def test_player_names(started):
    assert started.player_names(CHANNEL) == ["Alice", "Bob"]
    assert started.player_names("nowhere") == []
