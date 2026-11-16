"""Both repositories must satisfy the same contract, so the tests are shared."""
from datetime import timedelta

import pytest

from fantasymtg.adapters.memory_repo import InMemoryGameRepository
from fantasymtg.adapters.sqlite_repo import SqliteGameRepository
from fantasymtg.core.models import GameStatus
from fantasymtg.core.serialization import game_from_dict, game_to_dict
from fantasymtg.core.service import GameService
from conftest import ADMIN, ALICE, CHANNEL, START, FakeClock, FakePrices, buy


@pytest.fixture(params=["memory", "sqlite-memory", "sqlite-file"])
def any_repo(request, tmp_path):
    if request.param == "memory":
        yield InMemoryGameRepository()
    else:
        path = ":memory:" if request.param == "sqlite-memory" else tmp_path / "sub" / "game.db"
        repo = SqliteGameRepository(path)
        yield repo
        repo.close()


async def _played_game(repo):
    clock = FakeClock()
    prices = FakePrices()
    prices.set("bolt", 1_00, "Lightning Bolt")
    svc = GameService(repo, prices, clock)
    await svc.create_game(CHANNEL, ADMIN)
    await svc.configure(CHANNEL, ADMIN, "endDate", "2026-06-01")
    await svc.join_game(CHANNEL, ALICE, "Alice")
    await svc.start_game(CHANNEL, ADMIN)
    await buy(svc, ALICE, "bolt", 2)
    pending = await svc.request_buy(CHANNEL, ALICE, "bolt", 1)
    await svc.attach_external_ref(CHANNEL, pending.pending.id, "msg-1")
    return svc, clock


async def test_round_trip_preserves_state(any_repo):
    svc, _ = await _played_game(any_repo)
    game = any_repo.find_by_channel(CHANNEL)
    assert game.status == GameStatus.ACTIVE
    assert game.player_by_user(ALICE).quantity_of("bolt") == 2
    assert len(game.trades) == 1
    assert svc.find_pending_id(CHANNEL, "msg-1") is not None
    assert game_to_dict(game_from_dict(game_to_dict(game))) == game_to_dict(game)


async def test_find_by_channel_prefers_unfinished(any_repo):
    svc, clock = await _played_game(any_repo)
    clock.advance(days=200)
    await svc.tick()
    finished = any_repo.find_by_channel(CHANNEL)
    assert finished.status == GameStatus.FINISHED
    assert any_repo.list_active() == []

    clock.advance(seconds=1)
    new_game = await svc.create_game(CHANNEL, ADMIN)
    assert any_repo.find_by_channel(CHANNEL).id == new_game.id


async def test_list_active(any_repo):
    await _played_game(any_repo)
    assert [g.channel_key for g in any_repo.list_active()] == [CHANNEL]
    assert any_repo.find_by_channel("elsewhere") is None


def test_stored_games_are_isolated_from_callers():
    repo = InMemoryGameRepository()
    from fantasymtg.core.models import Game
    game = Game(id="g", channel_key=CHANNEL, admin_id=ADMIN, created_at=START)
    repo.save(game)
    game.admin_id = "mutated"
    assert repo.find_by_channel(CHANNEL).admin_id == ADMIN


def test_sqlite_persists_across_connections(tmp_path):
    from fantasymtg.core.models import Game
    path = tmp_path / "game.db"
    repo = SqliteGameRepository(path)
    repo.save(Game(id="g", channel_key=CHANNEL, admin_id=ADMIN, created_at=START))
    repo.close()
    reopened = SqliteGameRepository(path)
    assert reopened.find_by_channel(CHANNEL).id == "g"
    reopened.close()


def test_unknown_schema_rejected():
    with pytest.raises(ValueError):
        game_from_dict({"schema": 999})
