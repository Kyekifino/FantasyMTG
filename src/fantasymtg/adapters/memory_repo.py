"""In-memory GameRepository. Used by tests and handy for quick experiments.

Games are stored serialized so callers can never mutate stored state by accident,
which mirrors how the SQLite repository behaves.
"""
from __future__ import annotations

from ..core.models import Game, GameStatus
from ..core.serialization import game_from_dict, game_to_dict


class InMemoryGameRepository:
    def __init__(self) -> None:
        self._games: dict[str, dict] = {}
        self._order: list[str] = []

    def find_by_channel(self, channel_key: str) -> Game | None:
        candidates = [self._games[gid] for gid in self._order if self._games[gid]["channel_key"] == channel_key]
        if not candidates:
            return None
        unfinished = [g for g in candidates if g["status"] != GameStatus.FINISHED.value]
        return game_from_dict((unfinished or candidates)[-1])

    def list_active(self) -> list[Game]:
        return [
            game_from_dict(self._games[gid])
            for gid in self._order
            if self._games[gid]["status"] == GameStatus.ACTIVE.value
        ]

    def save(self, game: Game) -> None:
        if game.id not in self._games:
            self._order.append(game.id)
        self._games[game.id] = game_to_dict(game)
