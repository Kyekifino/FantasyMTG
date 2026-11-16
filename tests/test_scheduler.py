"""Time-based rules: timeouts, top-offs, weekly leaderboard and game end."""
from datetime import timedelta

import pytest

from fantasymtg.core import errors
from fantasymtg.core.models import GameStatus, PendingStatus
from fantasymtg.core.results import (
    BudgetToppedOff,
    GameEnded,
    PendingTimedOut,
    WeeklyLeaderboard,
)
from conftest import ADMIN, ALICE, BOB, CHANNEL, START, buy, player


def of_type(notes, cls):
    return [n for n in notes if isinstance(n, cls)]


async def test_tick_with_nothing_due(started):
    assert await started.tick() == []


async def test_pending_trade_times_out_once(started, clock):
    proposal = await started.request_buy(CHANNEL, ALICE, "bolt", 1)
    clock.advance(minutes=29)
    assert await started.tick() == []
    clock.advance(minutes=1)
    notes = await started.tick()
    assert [n.pending.id for n in of_type(notes, PendingTimedOut)] == [proposal.pending.id]
    assert of_type(await started.tick(), PendingTimedOut) == []
    with pytest.raises(errors.ConfirmationExpired):
        await started.respond(CHANNEL, proposal.pending.id, ALICE, True)


async def test_late_reaction_before_tick_suppresses_timeout_message(started, clock):
    proposal = await started.request_buy(CHANNEL, ALICE, "bolt", 1)
    clock.advance(minutes=31)
    with pytest.raises(errors.ConfirmationExpired):
        await started.respond(CHANNEL, proposal.pending.id, ALICE, True)
    assert of_type(await started.tick(), PendingTimedOut) == []


async def test_resolved_requests_are_eventually_pruned(started, clock):
    proposal = await started.request_buy(CHANNEL, ALICE, "bolt", 1)
    await started.respond(CHANNEL, proposal.pending.id, ALICE, False)
    clock.advance(days=3)
    await started.tick()
    assert proposal.pending.id not in started.get_game(CHANNEL).pending


async def test_top_off_every_30_days(started, clock):
    clock.advance(days=29, hours=23)
    assert of_type(await started.tick(), BudgetToppedOff) == []
    clock.advance(hours=1)
    notes = of_type(await started.tick(), BudgetToppedOff)
    assert len(notes) == 1 and notes[0].amount == 20_00
    assert player(started, ALICE).balance == 120_00
    assert player(started, BOB).balance == 120_00
    assert of_type(await started.tick(), BudgetToppedOff) == []


async def test_missed_top_offs_catch_up(started, clock):
    clock.advance(days=95)  # bot was offline
    notes = of_type(await started.tick(), BudgetToppedOff)
    assert [n.at for n in notes] == [START + timedelta(days=d) for d in (30, 60, 90)]
    assert player(started, ALICE).balance == 160_00


async def test_zero_top_off_is_silent(service, clock):
    await service.create_game(CHANNEL, ADMIN)
    await service.configure(CHANNEL, ADMIN, "topOffBudget", "0")
    await service.join_game(CHANNEL, ALICE, "Alice")
    await service.start_game(CHANNEL, ADMIN)
    clock.advance(days=31)
    assert of_type(await service.tick(), BudgetToppedOff) == []


async def test_changing_top_off_days_mid_game(started, clock):
    clock.advance(days=30)
    await started.tick()  # first top-off at day 30
    change = await started.configure(CHANNEL, ADMIN, "topOffDays", "10")
    await started.respond(CHANNEL, change.pending.id, ADMIN, True)
    clock.advance(days=10)
    assert len(of_type(await started.tick(), BudgetToppedOff)) == 1


async def test_weekly_leaderboard(started, clock, prices):
    await buy(started, ALICE, "ring", 1)  # Alice: 58 cash + 40 card
    prices.set("ring", 100_00, "Sol Ring")
    clock.advance(days=6, hours=23)
    assert of_type(await started.tick(), WeeklyLeaderboard) == []
    clock.advance(hours=1)
    notes = of_type(await started.tick(), WeeklyLeaderboard)
    assert len(notes) == 1
    standings = notes[0].leaderboard.standings
    assert [s.name for s in standings] == ["Alice", "Bob"]
    assert standings[0].total == 58_00 + 100_00  # refreshed price used
    assert of_type(await started.tick(), WeeklyLeaderboard) == []


async def test_weekly_leaderboard_posts_once_after_downtime(started, clock):
    clock.advance(days=22)
    assert len(of_type(await started.tick(), WeeklyLeaderboard)) == 1
    clock.advance(days=6)  # day 28 == next week boundary
    assert len(of_type(await started.tick(), WeeklyLeaderboard)) == 1


async def test_game_end(service, clock, prices):
    await service.create_game(CHANNEL, ADMIN)
    await service.configure(CHANNEL, ADMIN, "endDate", "2026-01-20")
    await service.join_game(CHANNEL, ALICE, "Alice")
    await service.join_game(CHANNEL, BOB, "Bob")
    await service.start_game(CHANNEL, ADMIN)
    await buy(service, BOB, "ring", 1)
    open_trade = await service.request_buy(CHANNEL, ALICE, "bolt", 1)
    prices.set("ring", 90_00, "Sol Ring")

    clock.advance(days=30)
    notes = await service.tick()
    ended = of_type(notes, GameEnded)
    assert len(ended) == 1
    assert [w.name for w in ended[0].winners] == ["Bob"]
    assert ended[0].leaderboard.final
    assert of_type(notes, WeeklyLeaderboard) == []
    assert of_type(notes, PendingTimedOut) == []  # end-of-game message covers it
    assert of_type(notes, BudgetToppedOff) == []  # top-off would land after the end

    game = service.get_game(CHANNEL)
    assert game.status == GameStatus.FINISHED
    stale = game.pending.get(open_trade.pending.id)
    assert stale is None or stale.status == PendingStatus.EXPIRED
    assert await service.tick() == []

    # values are frozen after the game ends
    prices.set("ring", 1, "Sol Ring")
    board = await service.leaderboard(CHANNEL)
    assert board.final and board.standings[0].total == 58_00 + 90_00
    with pytest.raises(errors.GameFinished):
        await service.request_buy(CHANNEL, ALICE, "bolt", 1)

    # a new game can now be created in the channel
    await service.create_game(CHANNEL, ADMIN)


async def test_game_end_with_tie(started, clock):
    clock.advance(days=366)
    ended = of_type(await started.tick(), GameEnded)[0]
    assert {w.name for w in ended.winners} == {"Alice", "Bob"}
    assert all(s.rank == 1 for s in ended.leaderboard.standings)


async def test_tick_ignores_games_in_setup(service, clock):
    await service.create_game(CHANNEL, ADMIN)
    clock.advance(days=400)
    assert await service.tick() == []


async def test_tick_survives_price_outage(started, clock, prices):
    await buy(started, ALICE, "ring", 1)
    prices.fail = True
    clock.advance(days=7)
    notes = of_type(await started.tick(), WeeklyLeaderboard)
    alice = next(s for s in notes[0].leaderboard.standings if s.name == "Alice")
    assert alice.total == 98_00  # last known price
