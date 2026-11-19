"""Domain errors raised by the game service.

Errors carry structured data only. Wording shown to players lives in
``fantasymtg.presentation.messages`` so each interface can phrase things its own way.
"""
from __future__ import annotations

from datetime import datetime


class GameError(Exception):
    """Base class for every rule violation a player can trigger."""


class NoGameInChannel(GameError):
    pass


class GameAlreadyExists(GameError):
    pass


class NotAdmin(GameError):
    pass


class GameNotInSetup(GameError):
    """The action is only allowed before the game starts (e.g. joining)."""


class LateJoinNotAllowed(GameError):
    """The game has started and the admin hasn't turned on allowLateJoin."""


class GameNotActive(GameError):
    """The action is only allowed while the game is running (e.g. trading)."""


class GameFinished(GameError):
    pass


class GameFull(GameError):
    def __init__(self, max_players: int) -> None:
        super().__init__(max_players)
        self.max_players = max_players


class AlreadyJoined(GameError):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class NameTaken(GameError):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class InvalidName(GameError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class NotAPlayer(GameError):
    pass


class PlayerNotFound(GameError):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class NoPlayers(GameError):
    pass


class UnknownSetting(GameError):
    def __init__(self, name: str, known: list[str]) -> None:
        super().__init__(name)
        self.name = name
        self.known = known


class InvalidSettingValue(GameError):
    def __init__(self, name: str, raw: str, reason: str) -> None:
        super().__init__(name, raw, reason)
        self.name = name
        self.raw = raw
        self.reason = reason


class InvalidQuantity(GameError):
    def __init__(self, quantity: int) -> None:
        super().__init__(quantity)
        self.quantity = quantity


class InvalidCardLink(GameError):
    def __init__(self, link: str) -> None:
        super().__init__(link)
        self.link = link


class CardNotFound(GameError):
    def __init__(self, link: str) -> None:
        super().__init__(link)
        self.link = link


class PriceUnavailable(GameError):
    def __init__(self, card_name: str) -> None:
        super().__init__(card_name)
        self.card_name = card_name


class PriceServiceError(GameError):
    """The price source could not be reached. Not the player's fault."""


class InsufficientFunds(GameError):
    def __init__(self, needed: int, available: int) -> None:
        super().__init__(needed, available)
        self.needed = needed
        self.available = available


class CardOwnedByAnother(GameError):
    def __init__(self, card_name: str, owner_name: str) -> None:
        super().__init__(card_name, owner_name)
        self.card_name = card_name
        self.owner_name = owner_name


class TooManyDistinctCards(GameError):
    def __init__(self, limit: int) -> None:
        super().__init__(limit)
        self.limit = limit


class NotEnoughCopies(GameError):
    def __init__(self, card_name: str, owned: int, requested: int) -> None:
        super().__init__(card_name, owned, requested)
        self.card_name = card_name
        self.owned = owned
        self.requested = requested


class ConfirmationExpired(GameError):
    def __init__(self, expired_at: datetime) -> None:
        super().__init__(expired_at)
        self.expired_at = expired_at
