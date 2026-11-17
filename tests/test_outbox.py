"""Announcements survive outages and restarts until they're actually delivered."""
import asyncio
import json
import sqlite3
from datetime import timedelta

import discord
import pytest

from fantasymtg.adapters.sqlite_repo import SqliteGameRepository
from fantasymtg.core import errors
from fantasymtg.core.models import GameStatus
from fantasymtg.core.results import BudgetToppedOff, GameEnded, PendingTimedOut, WeeklyLeaderboard
from fantasymtg.core.serialization import game_from_dict, game_to_dict
from fantasymtg.core.service import FINAL_PRICE_GRACE, GameService
from fantasymtg.discord_bot.bot import FantasyMTGBot, classify_send_error
from fantasymtg.discord_bot.helpers import Delivery
from conftest import ADMIN, ALICE, BOB, CHANNEL, FakeClock, FakePrices, buy


def kinds(entries):
    return [type(e.notification).__name__ for e in entries]


# --- core queue ------------------------------------------------------------------------

async def test_tick_queues_until_marked_delivered(started, clock):
    await started.request_buy(CHANNEL, ALICE, "bolt", 1)
    clock.advance(days=30)
    await started.tick()
    queued = started.undelivered()
    assert kinds(queued) == ["PendingTimedOut", "BudgetToppedOff", "WeeklyLeaderboard"]
    assert [int(e.id) for e in queued] == sorted(int(e.id) for e in queued)

    await started.tick()  # nothing new is due, and nothing is duplicated
    assert len(started.undelivered()) == 3

    await started.mark_delivered(queued[0])
    await started.mark_delivered(queued[0])  # idempotent
    assert kinds(started.undelivered()) == ["BudgetToppedOff", "WeeklyLeaderboard"]


async def test_announcements_are_eventually_dropped(started, clock):
    clock.advance(days=30)
    await started.tick()
    assert started.undelivered()
    clock.advance(days=15)
    await started.tick()
    # the old ones are gone; only what this tick produced remains
    assert all(clock.current - e.created_at < timedelta(days=14) for e in started.undelivered())


async def test_game_end_waits_for_final_prices(service, clock, prices):
    await service.create_game(CHANNEL, ADMIN)
    await service.configure(CHANNEL, ADMIN, "endDate", "2026-01-10")
    await service.join_game(CHANNEL, ALICE, "Alice")
    await service.start_game(CHANNEL, ADMIN)
    await buy(service, ALICE, "ring", 1)

    prices.fail = True
    clock.advance(days=10)
    assert of(await service.tick(), GameEnded) == []
    assert service.get_game(CHANNEL).status == GameStatus.FINISHED  # trading still stops on time
    with pytest.raises(errors.GameFinished):
        await service.request_buy(CHANNEL, ALICE, "bolt", 1)

    clock.advance(hours=1)
    assert of(await service.tick(), GameEnded) == []  # still down
    prices.fail = False
    prices.set("ring", 75_00, "Sol Ring")
    clock.advance(minutes=1)
    ended = of(await service.tick(), GameEnded)
    assert ended[0].leaderboard.standings[0].total == 58_00 + 75_00  # fresh final price
    assert kinds(service.undelivered()) == ["GameEnded"]
    assert await service.tick() == []


async def test_game_end_announced_with_last_prices_after_grace(service, clock, prices):
    await service.create_game(CHANNEL, ADMIN)
    await service.join_game(CHANNEL, ALICE, "Alice")
    await service.start_game(CHANNEL, ADMIN)
    await buy(service, ALICE, "ring", 1)
    prices.fail = True
    clock.advance(days=366)
    await service.tick()
    clock.advance(seconds=FINAL_PRICE_GRACE.total_seconds())
    ended = of(await service.tick(), GameEnded)
    assert ended[0].leaderboard.standings[0].holdings_value == 40_00  # last known price


async def test_finished_game_still_delivers_after_new_game_in_channel(service, clock, prices):
    await service.create_game(CHANNEL, ADMIN)
    await service.join_game(CHANNEL, ALICE, "Alice")
    await service.start_game(CHANNEL, ADMIN)
    await buy(service, ALICE, "ring", 1)
    prices.fail = True
    clock.advance(days=366)
    await service.tick()
    old_game_id = service.get_game(CHANNEL).id

    await service.create_game(CHANNEL, ADMIN)  # allowed: the old game has ended
    prices.fail = False
    await service.tick()
    entries = [e for e in service.undelivered() if isinstance(e.notification, GameEnded)]
    assert [e.game_id for e in entries] == [old_game_id]


def of(notes, cls):
    return [n for n in notes if isinstance(n, cls)]


# --- surviving restarts ----------------------------------------------------------------

def _service(path, clock, prices):
    return GameService(SqliteGameRepository(path), prices, clock)


async def test_setup_game_survives_restart(tmp_path):
    path, clock, prices = tmp_path / "game.db", FakeClock(), FakePrices()
    before = _service(path, clock, prices)
    await before.create_game(CHANNEL, ADMIN)
    await before.configure(CHANNEL, ADMIN, "startingBudget", "250")
    await before.configure(CHANNEL, ADMIN, "uniqueOwnership", "false")
    await before.join_game(CHANNEL, ALICE, "Alice")

    after = _service(path, clock, prices)  # simulates stopping and starting the bot
    game = after.get_game(CHANNEL)
    assert game.status == GameStatus.SETUP
    assert game.config.starting_budget == 250_00 and game.config.unique_ownership is False
    assert [p.name for p in game.players] == ["Alice"]
    await after.join_game(CHANNEL, BOB, "Bob")
    await after.start_game(CHANNEL, ADMIN)
    assert after.get_game(CHANNEL).player_by_user(BOB).balance == 250_00


async def test_queued_announcements_survive_restart(tmp_path):
    path, clock, prices = tmp_path / "game.db", FakeClock(), FakePrices()
    before = _service(path, clock, prices)
    await before.create_game(CHANNEL, ADMIN)
    await before.join_game(CHANNEL, ALICE, "Alice")
    await before.start_game(CHANNEL, ADMIN)
    clock.advance(days=30)
    await before.tick()
    expected = kinds(before.undelivered())

    after = _service(path, clock, prices)
    assert kinds(after.undelivered()) == expected
    assert isinstance(after.undelivered()[0].notification, BudgetToppedOff)


def test_schema_1_games_still_load(service):
    data = game_to_dict(game_from_dict({**_minimal_v2()}))
    data["schema"] = 1
    for key in ("outbox", "outbox_seq", "final_prices_due_since"):
        del data[key]
    game = game_from_dict(data)
    assert game.outbox == [] and game.outbox_seq == 0 and game.final_prices_due_since is None


def test_newer_schema_is_rejected_with_a_clear_message():
    with pytest.raises(ValueError, match="newer version"):
        game_from_dict({**_minimal_v2(), "schema": 99})


def _minimal_v2():
    from fantasymtg.core.models import Game
    from conftest import START
    return game_to_dict(Game(id="g", channel_key=CHANNEL, admin_id=ADMIN, created_at=START,
                             status=GameStatus.ACTIVE))


def test_sqlite_migrates_databases_from_first_release(tmp_path):
    path = tmp_path / "old.db"
    old = _minimal_v2()
    old["schema"] = 1
    for key in ("outbox", "outbox_seq", "final_prices_due_since"):
        del old[key]
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE games (id TEXT PRIMARY KEY, channel_key TEXT NOT NULL, "
                 "status TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL)")
    conn.execute("INSERT INTO games VALUES (?, ?, ?, ?, ?)",
                 ("g", CHANNEL, "active", old["created_at"], json.dumps(old)))
    conn.execute("INSERT INTO games VALUES (?, ?, ?, ?, ?)",
                 ("done", "elsewhere", "finished", old["created_at"],
                  json.dumps({**old, "id": "done", "status": "finished"})))
    conn.commit()
    conn.close()

    repo = SqliteGameRepository(path)
    assert [g.id for g in repo.list_needing_attention()] == ["g"]
    assert repo.get("done").status == GameStatus.FINISHED
    repo.close()


# --- Discord delivery ------------------------------------------------------------------

@pytest.fixture
def bot(service):
    return FantasyMTGBot(service)


async def test_deliver_outbox_retries_in_order(bot, started, clock, monkeypatch):
    await started.request_buy(CHANNEL, ALICE, "bolt", 1)
    clock.advance(days=30)
    await started.tick()  # queues timeout, top-off, weekly leaderboard

    results = iter([Delivery.RETRY])  # network down for the first attempt
    sent = []

    async def fake_send(key, text, reply_to=None):
        result = next(results, Delivery.SENT)
        if result == Delivery.SENT:
            sent.append(text)
        return result

    monkeypatch.setattr(bot, "_send", fake_send)
    await bot.deliver_outbox()
    assert sent == []  # first failed, so the rest of the game's queue waited
    assert len(started.undelivered()) == 3

    await bot.deliver_outbox()
    assert len(sent) == 3 and "wasn't confirmed" in sent[0] and "top-off" in sent[1]
    assert started.undelivered() == []


async def test_deliver_outbox_drops_permanent_failures(bot, started, clock, monkeypatch):
    clock.advance(days=30)
    await started.tick()

    async def fake_send(key, text, reply_to=None):
        return Delivery.DROP

    monkeypatch.setattr(bot, "_send", fake_send)
    await bot.deliver_outbox()
    assert started.undelivered() == []


class _Resp:
    def __init__(self, status):
        self.status = status
        self.reason = "x"


@pytest.mark.parametrize(
    "exc, expected",
    [
        (OSError("network unreachable"), Delivery.RETRY),
        (asyncio.TimeoutError(), Delivery.RETRY),
        (discord.HTTPException(_Resp(503), "unavailable"), Delivery.RETRY),
        (discord.HTTPException(_Resp(429), "slow down"), Delivery.RETRY),
        (discord.Forbidden(_Resp(403), "Missing Access"), Delivery.DROP),
        (discord.NotFound(_Resp(404), "Unknown Channel"), Delivery.DROP),
        (ValueError("bug"), Delivery.DROP),
    ],
)
def test_classify_send_error(exc, expected):
    assert classify_send_error(exc) == expected
