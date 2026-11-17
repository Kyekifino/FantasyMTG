"""The storage port. Adapters implement :class:`GameRepository`."""
from __future__ import annotations

from typing import Protocol

from .models import Game


class GameRepository(Protocol):
    def find_by_channel(self, channel_key: str) -> Game | None:
        """Return the channel's unfinished game, else its most recently created game."""

    def get(self, game_id: str) -> Game | None:
        """Return a game by id, whatever its status."""

    def list_active(self) -> list[Game]:
        """Return every game whose status is ACTIVE."""

    def list_needing_attention(self) -> list[Game]:
        """Return every game whose ``needs_attention`` is True, oldest first."""

    def save(self, game: Game) -> None:
        """Insert or replace the whole game aggregate."""
