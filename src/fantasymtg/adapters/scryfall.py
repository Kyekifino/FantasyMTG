"""Scryfall implementation of the PriceSource port.

Prices are Scryfall's ``prices.usd`` family, which Scryfall sources from TCGplayer
market price. Each Scryfall card page (one printing/treatment) is one tradeable card.
We use the nonfoil price, falling back to foil and then etched for printings that only
exist in those finishes.

Pure helpers (:func:`parse_card_link`, :func:`quote_from_card_json`) hold the logic and
are unit tested directly; :class:`ScryfallPriceSource` only adds HTTP, caching and
rate limiting, and its transport is injectable.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable, Protocol
from urllib.parse import quote as urlquote
from urllib.parse import unquote, urlsplit, urlunsplit

from ..core import errors
from ..core.money import dollars_to_cents
from ..core.pricing import CardQuote

log = logging.getLogger(__name__)

API_ROOT = "https://api.scryfall.com"
USER_AGENT = "FantasyMTG/0.1 (self-hosted Discord bot)"
FINISH_PRICE_KEYS = (("nonfoil", "usd"), ("foil", "usd_foil"), ("etched", "usd_etched"))
LANG_CODES = {
    "en", "es", "fr", "de", "it", "pt", "ja", "ko", "ru", "zhs", "zht",
    "he", "la", "grc", "ar", "sa", "ph", "qya",
}
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
COLLECTION_BATCH = 75  # Scryfall's /cards/collection limit


@dataclass(frozen=True)
class CardRef:
    """Where to find a card on the Scryfall API."""

    scryfall_id: str | None = None
    set_code: str | None = None
    collector_number: str | None = None
    lang: str | None = None

    def api_path(self) -> str:
        if self.scryfall_id:
            return f"/cards/{self.scryfall_id}"
        path = f"/cards/{urlquote(self.set_code or '')}/{urlquote(self.collector_number or '')}"
        return f"{path}/{self.lang}" if self.lang else path


def parse_card_link(link: str) -> CardRef:
    """Parse a scryfall.com card page URL, an api.scryfall.com card URL, or a bare id."""
    text = link.strip().strip("<>")
    if _UUID.match(text):
        return CardRef(scryfall_id=text.lower())
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    host = parts.netloc.lower().removeprefix("www.")
    segments = [unquote(s) for s in parts.path.split("/") if s]

    if host == "scryfall.com" and len(segments) >= 3 and segments[0] == "card":
        set_code, number = segments[1].lower(), segments[2]
        lang = segments[3].lower() if len(segments) >= 5 and segments[3].lower() in LANG_CODES else None
        return CardRef(set_code=set_code, collector_number=number, lang=lang)
    if host == "api.scryfall.com" and len(segments) >= 2 and segments[0] == "cards":
        if len(segments) == 2 and _UUID.match(segments[1]):
            return CardRef(scryfall_id=segments[1].lower())
        if len(segments) >= 3:
            lang = segments[3].lower() if len(segments) >= 4 else None
            return CardRef(set_code=segments[1].lower(), collector_number=segments[2], lang=lang)
    raise errors.InvalidCardLink(link)


def quote_from_card_json(data: dict[str, Any]) -> CardQuote:
    prices = data.get("prices") or {}
    price, finish = None, "nonfoil"
    for finish_name, key in FINISH_PRICE_KEYS:
        price = dollars_to_cents(prices.get(key))
        if price is not None:
            finish = finish_name
            break
    uri = data.get("scryfall_uri")
    if uri:  # drop utm tracking params
        p = urlsplit(uri)
        uri = urlunsplit((p.scheme, p.netloc, p.path, "", ""))
    return CardQuote(
        card_id=data["id"],
        name=data.get("name", "Unknown card"),
        set_code=data.get("set", "?"),
        collector_number=str(data.get("collector_number", "?")),
        price=price,
        finish=finish,
        url=uri,
    )


# --- transport -----------------------------------------------------------------------

class HttpTransport(Protocol):
    async def request(
        self, method: str, url: str, json: dict[str, Any] | None = None
    ) -> tuple[int, dict[str, Any]]:
        """Perform a request and return (status, decoded JSON body)."""


class AiohttpTransport:
    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self._timeout = timeout_seconds
        self._session = None

    async def request(
        self, method: str, url: str, json: dict[str, Any] | None = None
    ) -> tuple[int, dict[str, Any]]:
        import aiohttp

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            )
        async with self._session.request(method, url, json=json) as resp:
            try:
                body = await resp.json(content_type=None)
            except Exception:
                body = {}
            return resp.status, body if isinstance(body, dict) else {}

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()


class ScryfallPriceSource:
    """PriceSource backed by the Scryfall API, with a short cache and polite rate limit."""

    def __init__(
        self,
        transport: HttpTransport | None = None,
        *,
        cache_seconds: float = 3600.0,  # Scryfall refreshes prices about once a day
        min_interval_seconds: float = 0.1,  # Scryfall asks for <= 10 requests/second
        monotonic=time.monotonic,
        sleep=asyncio.sleep,
    ) -> None:
        self._http = transport or AiohttpTransport()
        self._cache_seconds = cache_seconds
        self._min_interval = min_interval_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._cache: dict[str, tuple[float, CardQuote]] = {}
        self._rate_lock = asyncio.Lock()
        self._last_request = float("-inf")

    async def lookup(self, link: str) -> CardQuote:
        ref = parse_card_link(link)
        if ref.scryfall_id:
            cached = self._cached(ref.scryfall_id)
            if cached:
                return cached
        status, body = await self._request("GET", API_ROOT + ref.api_path())
        if status == 404:
            raise errors.CardNotFound(link)
        if status != 200 or body.get("object") != "card":
            log.warning("Scryfall lookup %s returned %s: %s", link, status, body.get("details"))
            raise errors.PriceServiceError()
        quote = quote_from_card_json(body)
        self._store(quote)
        return quote

    async def quotes(self, card_ids: Iterable[str]) -> dict[str, CardQuote]:
        result: dict[str, CardQuote] = {}
        missing: list[str] = []
        for cid in dict.fromkeys(card_ids):
            cached = self._cached(cid)
            if cached:
                result[cid] = cached
            else:
                missing.append(cid)
        for start in range(0, len(missing), COLLECTION_BATCH):
            batch = missing[start : start + COLLECTION_BATCH]
            status, body = await self._request(
                "POST",
                API_ROOT + "/cards/collection",
                json={"identifiers": [{"id": cid} for cid in batch]},
            )
            if status != 200:
                log.warning("Scryfall collection returned %s: %s", status, body.get("details"))
                raise errors.PriceServiceError()
            for card in body.get("data", []):
                quote = quote_from_card_json(card)
                self._store(quote)
                result[quote.card_id] = quote
        return result

    async def close(self) -> None:
        close = getattr(self._http, "close", None)
        if close:
            await close()

    def _cached(self, card_id: str) -> CardQuote | None:
        hit = self._cache.get(card_id)
        if hit and self._monotonic() - hit[0] < self._cache_seconds:
            return hit[1]
        return None

    def _store(self, quote: CardQuote) -> None:
        self._cache[quote.card_id] = (self._monotonic(), quote)

    async def _request(
        self, method: str, url: str, json: dict[str, Any] | None = None
    ) -> tuple[int, dict[str, Any]]:
        async with self._rate_lock:
            wait = self._last_request + self._min_interval - self._monotonic()
            if wait > 0:
                await self._sleep(wait)
            self._last_request = self._monotonic()
            try:
                return await self._http.request(method, url, json=json)
            except Exception as exc:
                log.warning("Scryfall request failed: %s", exc)
                raise errors.PriceServiceError() from exc
