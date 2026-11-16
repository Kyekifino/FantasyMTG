"""The price-source port. Adapters (e.g. Scryfall) implement :class:`PriceSource`."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol


@dataclass(frozen=True)
class CardQuote:
    card_id: str  # stable unique id for this printing/treatment (Scryfall id)
    name: str
    set_code: str
    collector_number: str
    price: int | None  # cents; None when the source reports no price
    finish: str = "nonfoil"  # which price was used: nonfoil, foil, or etched
    url: str | None = None

    @property
    def label(self) -> str:
        return f"{self.name} ({self.set_code.upper()} #{self.collector_number})"


class PriceSource(Protocol):
    async def lookup(self, link: str) -> CardQuote:
        """Resolve a player-supplied card link to a quote.

        Raises InvalidCardLink, CardNotFound or PriceServiceError.
        """

    async def quotes(self, card_ids: Iterable[str]) -> dict[str, CardQuote]:
        """Fetch current quotes for known card ids. Missing ids are omitted."""
