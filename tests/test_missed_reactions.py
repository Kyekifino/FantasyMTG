"""Catching up on 👍/👎 reactions added while the bot was offline."""
import pytest

from fantasymtg.core.models import PendingStatus
from fantasymtg.discord_bot.helpers import channel_key, owner_decision
from conftest import ADMIN, ALICE, BOB

KEY = channel_key(1, 2)  # the bot posts to Discord channel 2


# --- core query ------------------------------------------------------------------------

async def _game(service):
    await service.create_game(KEY, ADMIN)
    await service.join_game(KEY, ALICE, "Alice")
    await service.join_game(KEY, BOB, "Bob")
    await service.start_game(KEY, ADMIN)


async def test_open_confirmations_lists_only_open_unexpired_with_ref(service, clock):
    await _game(service)
    with_ref = await service.request_buy(KEY, ALICE, "bolt", 1)
    await service.attach_external_ref(KEY, with_ref.pending.id, "100")
    await service.request_buy(KEY, BOB, "ring", 1)  # no message attached
    resolved = await service.request_buy(KEY, BOB, "c0", 1)
    await service.attach_external_ref(KEY, resolved.pending.id, "101")
    await service.respond(KEY, resolved.pending.id, BOB, False)

    assert [(k, p.id) for k, p in service.open_confirmations()] == [(KEY, with_ref.pending.id)]
    clock.advance(minutes=30)
    assert service.open_confirmations() == []


# --- decision helper -------------------------------------------------------------------

@pytest.mark.parametrize(
    "reactions, expected",
    [
        ([], None),
        ([("👍", ["bot", "other"])], None),
        ([("👍", ["bot", "owner"])], True),
        ([("👎🏽", ["owner"])], False),
        ([("👍", ["owner"]), ("👎", ["owner"])], False),  # ambiguous: play it safe
        ([("❤️", ["owner"])], None),
        ([("👍", ["bot"]), ("👍🏻", ["owner"])], True),
    ],
)
def test_owner_decision(reactions, expected):
    assert owner_decision(reactions, "owner") is expected


# --- the bot's reconcile loop, with fake Discord objects --------------------------------

class FakeUser:
    def __init__(self, uid):
        self.id = uid


class FakeReaction:
    def __init__(self, emoji, user_ids):
        self.emoji = emoji
        self._users = [FakeUser(u) for u in user_ids]

    async def users(self):
        for u in self._users:
            yield u


class FakeMessage:
    def __init__(self, reactions):
        self.reactions = reactions


class FakeChannel:
    def __init__(self, messages):
        self.messages = messages

    async def fetch_message(self, message_id):
        return self.messages[message_id]


@pytest.fixture
async def bot(service):
    from fantasymtg.discord_bot.bot import FantasyMTGBot

    bot = FantasyMTGBot(service)
    bot.sent = []

    async def fake_send(key, text, reply_to=None):
        bot.sent.append((key, text, reply_to))

    bot._send = fake_send
    yield bot


async def test_reconcile_applies_missed_reactions(bot, service):
    await _game(service)
    alice = await service.request_buy(KEY, ALICE, "bolt", 2)
    bob = await service.request_buy(KEY, BOB, "ring", 1)
    untouched = await service.request_buy(KEY, BOB, "c0", 1)
    for pending, ref in ((alice, "10"), (bob, "11"), (untouched, "12")):
        await service.attach_external_ref(KEY, pending.pending.id, ref)

    channel = FakeChannel({
        10: FakeMessage([FakeReaction("👍", ["bot", ALICE])]),
        11: FakeMessage([FakeReaction("👍", ["bot", ALICE]), FakeReaction("👎", ["bot", BOB])]),
        12: FakeMessage([FakeReaction("👍", ["bot", ALICE])]),  # only someone else reacted
    })
    bot.get_channel = lambda cid: channel if cid == 2 else None

    await bot._reconcile_reactions()

    assert service.get_game(KEY).player_by_user(ALICE).quantity_of("bolt") == 2
    assert service.get_game(KEY).player_by_user(BOB).quantity_of("ring") == 0
    game = service.get_game(KEY)
    assert game.pending[bob.pending.id].status == PendingStatus.CANCELLED
    assert game.pending[untouched.pending.id].status == PendingStatus.OPEN
    assert [reply_to for _, _, reply_to in bot.sent] == [10, 11]
    assert all("I was offline" in text for _, text, _ in bot.sent)

    # running again (e.g. another reconnect) does nothing new
    await bot._reconcile_reactions()
    assert len(bot.sent) == 2


async def test_reconcile_skips_unreachable_messages(bot, service):
    import discord

    await _game(service)
    pending = await service.request_buy(KEY, ALICE, "bolt", 1)
    await service.attach_external_ref(KEY, pending.pending.id, "10")

    class Response:
        status = 404
        reason = "Not Found"

    class BrokenChannel:
        async def fetch_message(self, _id):
            raise discord.NotFound(Response(), "Unknown Message")

    bot.get_channel = lambda cid: BrokenChannel()
    await bot._reconcile_reactions()
    assert bot.sent == []
    assert service.get_game(KEY).pending[pending.pending.id].status == PendingStatus.OPEN
