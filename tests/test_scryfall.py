import pytest

from fantasymtg.adapters.scryfall import (
    CardRef,
    ScryfallPriceSource,
    parse_card_link,
    quote_from_card_json,
)
from fantasymtg.core import errors

ID = "e3285e6b-3e79-4d7c-bf96-d920f973b80c"


@pytest.mark.parametrize(
    "link, expected",
    [
        ("https://scryfall.com/card/lea/161/lightning-bolt",
         CardRef(set_code="lea", collector_number="161")),
        ("scryfall.com/card/LEA/161/lightning-bolt?utm_source=x",
         CardRef(set_code="lea", collector_number="161")),
        ("<https://www.scryfall.com/card/2xm/229/sol-ring>",
         CardRef(set_code="2xm", collector_number="229")),
        ("https://scryfall.com/card/war/1%E2%98%85/karn-the-great-creator",
         CardRef(set_code="war", collector_number="1★")),
        ("https://scryfall.com/card/sta/42/ja/lightning-bolt",
         CardRef(set_code="sta", collector_number="42", lang="ja")),
        ("https://scryfall.com/card/plst/2XM-229",
         CardRef(set_code="plst", collector_number="2XM-229")),
        (f"https://api.scryfall.com/cards/{ID}", CardRef(scryfall_id=ID)),
        (ID.upper(), CardRef(scryfall_id=ID)),
    ],
)
def test_parse_card_link(link, expected):
    assert parse_card_link(link) == expected


@pytest.mark.parametrize(
    "link",
    ["lightning bolt", "https://example.com/card/lea/161", "https://scryfall.com/search?q=bolt",
     "https://scryfall.com/card/lea"],
)
def test_parse_card_link_rejects(link):
    with pytest.raises(errors.InvalidCardLink):
        parse_card_link(link)


def test_api_path_escapes_collector_number():
    assert CardRef(set_code="war", collector_number="1★").api_path() == "/cards/war/1%E2%98%85"
    assert CardRef(set_code="sta", collector_number="42", lang="ja").api_path() == "/cards/sta/42/ja"


def card_json(**prices):
    return {
        "object": "card",
        "id": ID,
        "name": "Lightning Bolt",
        "set": "lea",
        "collector_number": "161",
        "scryfall_uri": "https://scryfall.com/card/lea/161/lightning-bolt?utm_source=api",
        "prices": {"usd": None, "usd_foil": None, "usd_etched": None, **prices},
    }


def test_quote_prefers_nonfoil():
    q = quote_from_card_json(card_json(usd="350.10", usd_foil="900.00"))
    assert (q.price, q.finish) == (350_10, "nonfoil")
    assert q.url == "https://scryfall.com/card/lea/161/lightning-bolt"
    assert q.label == "Lightning Bolt (LEA #161)"


def test_quote_falls_back_to_foil_then_etched():
    assert quote_from_card_json(card_json(usd_foil="2.50")).finish == "foil"
    q = quote_from_card_json(card_json(usd_etched="0.75"))
    assert (q.price, q.finish) == (75, "etched")


def test_quote_without_price():
    assert quote_from_card_json(card_json()).price is None


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, url, json=None):
        self.calls.append((method, url, json))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def source(transport, now=[0.0]):
    return ScryfallPriceSource(
        transport, monotonic=lambda: now[0], sleep=_no_sleep, min_interval_seconds=0
    )


async def _no_sleep(_s):
    return None


async def test_lookup_by_set_and_number():
    t = FakeTransport([(200, card_json(usd="1.00"))])
    q = await source(t).lookup("https://scryfall.com/card/lea/161/lightning-bolt")
    assert q.price == 1_00
    assert t.calls == [("GET", "https://api.scryfall.com/cards/lea/161", None)]


async def test_lookup_not_found():
    t = FakeTransport([(404, {"object": "error", "details": "No card found"})])
    with pytest.raises(errors.CardNotFound):
        await source(t).lookup("https://scryfall.com/card/lea/999/x")


async def test_lookup_server_error_and_network_error():
    t = FakeTransport([(500, {}), OSError("boom")])
    s = source(t)
    with pytest.raises(errors.PriceServiceError):
        await s.lookup("https://scryfall.com/card/lea/161/x")
    with pytest.raises(errors.PriceServiceError):
        await s.lookup("https://scryfall.com/card/lea/161/x")


async def test_quotes_uses_collection_and_cache():
    now = [0.0]
    t = FakeTransport([(200, {"data": [card_json(usd="1.00")]}), (200, {"data": [card_json(usd="2.00")]})])
    s = ScryfallPriceSource(t, monotonic=lambda: now[0], sleep=_no_sleep, cache_seconds=60)
    assert (await s.quotes([ID]))[ID].price == 1_00
    assert t.calls[0][0:2] == ("POST", "https://api.scryfall.com/cards/collection")
    assert t.calls[0][2] == {"identifiers": [{"id": ID}]}
    assert (await s.quotes([ID]))[ID].price == 1_00  # cached
    assert len(t.calls) == 1
    now[0] = 61
    assert (await s.quotes([ID]))[ID].price == 2_00  # cache expired
    # lookups by id also hit the cache
    assert (await s.lookup(ID)).price == 2_00
    assert len(t.calls) == 2


async def test_quotes_batches_by_75():
    ids = [f"{i:08d}-0000-0000-0000-000000000000" for i in range(80)]
    t = FakeTransport([(200, {"data": []}), (200, {"data": []})])
    await source(t).quotes(ids)
    assert [len(c[2]["identifiers"]) for c in t.calls] == [75, 5]


async def test_rate_limit_waits_between_requests():
    now = [100.0]
    slept = []

    async def sleep(s):
        slept.append(s)
        now[0] += s

    t = FakeTransport([(200, card_json(usd="1.00")), (200, card_json(usd="1.00"))])
    s = ScryfallPriceSource(t, monotonic=lambda: now[0], sleep=sleep, min_interval_seconds=0.1)
    await s.lookup("https://scryfall.com/card/lea/161/x")
    await s.lookup("https://scryfall.com/card/lea/161/x")
    assert slept == [pytest.approx(0.1)]
