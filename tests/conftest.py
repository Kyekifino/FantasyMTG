"""Shared test doubles. The core only depends on ports, so tests swap in these fakes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

import pytest

from fantasymtg.adapters.memory_repo import InMemoryGameRepository
from fantasymtg.core import errors
from fantasymtg.core.pricing import CardQuote
from fantasymtg.core.service import GameService

CHANNEL = "test:guild:channel"
ADMIN = "admin"
ALICE = "alice-id"
BOB = "bob-id"
START = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, now: datetime = START) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


class FakePrices:
    """Price source keyed by link == card id, e.g. link "bolt" -> card id "bolt"."""

    def __init__(self) -> None:
        self.cards: dict[str, CardQuote] = {}
        self.fail = False
        self.lookups = 0

    def set(self, card_id: str, cents: int | None, name: str | None = None) -> CardQuote:
        quote = CardQuote(
            card_id=card_id,
            name=name or card_id.title(),
            set_code="tst",
            collector_number="1",
            price=cents,
            url=f"https://scryfall.com/card/tst/1/{card_id}",
        )
        self.cards[card_id] = quote
        return quote

    async def lookup(self, link: str) -> CardQuote:
        self.lookups += 1
        if self.fail:
            raise errors.PriceServiceError()
        if link not in self.cards:
            raise errors.CardNotFound(link)
        return self.cards[link]

    async def quotes(self, card_ids: Iterable[str]) -> dict[str, CardQuote]:
        if self.fail:
            raise errors.PriceServiceError()
        return {cid: self.cards[cid] for cid in card_ids if cid in self.cards}


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def prices() -> FakePrices:
    p = FakePrices()
    p.set("bolt", 1_00, "Lightning Bolt")
    p.set("ring", 40_00, "Sol Ring")
    p.set("lotus", 500_00, "Black Lotus")
    p.set("nope", None, "Unpriced Card")
    for i in range(10):
        p.set(f"c{i}", 1_00, f"Card {i}")
    return p


@pytest.fixture
def repo() -> InMemoryGameRepository:
    return InMemoryGameRepository()


@pytest.fixture
def service(repo, prices, clock) -> GameService:
    counter = iter(range(1, 10_000))
    return GameService(repo, prices, clock, id_factory=lambda: f"game-{next(counter)}")


@pytest.fixture
async def started(service: GameService) -> GameService:
    """A started game: admin ADMIN, players Alice and Bob, default settings."""
    await service.create_game(CHANNEL, ADMIN)
    await service.join_game(CHANNEL, ALICE, "Alice")
    await service.join_game(CHANNEL, BOB, "Bob")
    await service.start_game(CHANNEL, ADMIN)
    return service


async def buy(service: GameService, user: str, link: str, qty: int):
    proposal = await service.request_buy(CHANNEL, user, link, qty)
    return await service.respond(CHANNEL, proposal.pending.id, user, True)


async def sell(service: GameService, user: str, link: str, qty: int):
    proposal = await service.request_sell(CHANNEL, user, link, qty)
    return await service.respond(CHANNEL, proposal.pending.id, user, True)


def player(service: GameService, user: str):
    p = service.get_game(CHANNEL).player_by_user(user)
    assert p is not None
    return p
