import pytest

from fantasymtg.core import errors
from conftest import ALICE, BOB, CHANNEL, buy, sell


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


async def test_portfolio_shows_average_paid_and_delta(started, prices):
    await buy(started, ALICE, "ring", 1)  # $40
    prices.set("ring", 50_00, "Sol Ring")
    await buy(started, ALICE, "ring", 1)  # $50 -> average $45
    prices.set("ring", 48_00, "Sol Ring")
    p = await started.portfolio(CHANNEL, ALICE)
    line = p.lines[0]
    assert (line.avg_cost, line.unit_price, line.delta) == (45_00, 48_00, 3_00)
    assert p.cost_basis == 90_00  # fees excluded
    assert p.unrealized == 6_00


async def test_selling_keeps_average_paid(started, prices):
    await buy(started, ALICE, "bolt", 2)  # $1
    prices.set("bolt", 2_00, "Lightning Bolt")
    await buy(started, ALICE, "bolt", 1)  # $2 -> 3 copies, $4 total
    await sell(started, ALICE, "bolt", 2)
    p = await started.portfolio(CHANNEL, ALICE)
    assert p.lines[0].quantity == 1
    assert p.lines[0].avg_cost == 1_33
    assert p.cost_basis == 1_33


async def test_selling_out_then_rebuying_starts_fresh(started, prices):
    await buy(started, ALICE, "bolt", 1)
    await sell(started, ALICE, "bolt", 1)
    prices.set("bolt", 3_00, "Lightning Bolt")
    await buy(started, ALICE, "bolt", 1)
    p = await started.portfolio(CHANNEL, ALICE)
    assert (p.lines[0].avg_cost, p.lines[0].delta) == (3_00, 0)


async def test_cost_basis_ignores_other_players(started, prices):
    await buy(started, BOB, "bolt", 1)
    prices.set("bolt", 5_00, "Lightning Bolt")
    await buy(started, ALICE, "ring", 1)
    assert started.get_game(CHANNEL).cost_basis(ALICE) == {"ring": 40_00}
