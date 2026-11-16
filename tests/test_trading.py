import pytest

from fantasymtg.core import errors
from fantasymtg.core.models import PendingKind, PendingStatus
from fantasymtg.core.results import Declined, TradeExecuted
from conftest import ADMIN, ALICE, BOB, CHANNEL, buy, player, sell


async def test_buy_quote_shows_cost_and_locks_price(started):
    proposal = await started.request_buy(CHANNEL, ALICE, "bolt", 3)
    assert proposal.pending.kind == PendingKind.BUY
    assert proposal.pending.unit_price == 1_00
    assert proposal.pending.fee == 2_00
    assert proposal.balance_before == 100_00
    assert proposal.balance_after == 100_00 - 3_00 - 2_00
    assert player(started, ALICE).balance == 100_00  # nothing charged until confirmed


async def test_confirmed_buy_updates_balance_and_holdings(started):
    outcome = await buy(started, ALICE, "bolt", 3)
    assert isinstance(outcome, TradeExecuted)
    alice = player(started, ALICE)
    assert alice.balance == 95_00
    assert alice.quantity_of("bolt") == 3
    assert len(started.get_game(CHANNEL).trades) == 1


async def test_buy_executes_at_quoted_price_even_if_market_moves(started, prices):
    proposal = await started.request_buy(CHANNEL, ALICE, "ring", 1)
    prices.set("ring", 80_00, "Sol Ring")
    await started.respond(CHANNEL, proposal.pending.id, ALICE, True)
    assert player(started, ALICE).balance == 100_00 - 40_00 - 2_00


async def test_buy_uses_quoted_fee_even_if_admin_changes_it(started):
    proposal = await started.request_buy(CHANNEL, ALICE, "bolt", 1)
    change = await started.configure(CHANNEL, ADMIN, "tradeFee", "10")
    await started.respond(CHANNEL, change.pending.id, ADMIN, True)
    await started.respond(CHANNEL, proposal.pending.id, ALICE, True)
    assert player(started, ALICE).balance == 100_00 - 1_00 - 2_00


async def test_buying_more_copies_adds_to_holding(started):
    await buy(started, ALICE, "bolt", 2)
    await buy(started, ALICE, "bolt", 1)
    alice = player(started, ALICE)
    assert alice.quantity_of("bolt") == 3
    assert alice.distinct_cards() == 1


async def test_insufficient_funds_includes_fee(started):
    # $98 + $2 fee == $100 exactly: allowed
    await started.request_buy(CHANNEL, ALICE, "c0", 98)
    # $99 + $2 fee > $100: rejected
    with pytest.raises(errors.InsufficientFunds) as info:
        await started.request_buy(CHANNEL, ALICE, "c0", 99)
    assert info.value.needed == 101_00
    assert info.value.available == 100_00


async def test_funds_rechecked_on_confirm(started):
    first = await started.request_buy(CHANNEL, ALICE, "ring", 2)  # $82
    second = await started.request_buy(CHANNEL, ALICE, "ring", 1)  # $42
    await started.respond(CHANNEL, first.pending.id, ALICE, True)
    with pytest.raises(errors.InsufficientFunds):
        await started.respond(CHANNEL, second.pending.id, ALICE, True)
    game = started.get_game(CHANNEL)
    assert game.pending[second.pending.id].status == PendingStatus.FAILED


async def test_unpriced_card_cannot_be_traded(started):
    with pytest.raises(errors.PriceUnavailable):
        await started.request_buy(CHANNEL, ALICE, "nope", 1)
    with pytest.raises(errors.PriceUnavailable):
        await started.request_sell(CHANNEL, ALICE, "nope", 1)


async def test_unknown_card(started):
    with pytest.raises(errors.CardNotFound):
        await started.request_buy(CHANNEL, ALICE, "missing", 1)


async def test_price_service_down(started, prices):
    prices.fail = True
    with pytest.raises(errors.PriceServiceError):
        await started.request_buy(CHANNEL, ALICE, "bolt", 1)


@pytest.mark.parametrize("qty", [0, -1])
async def test_invalid_quantity(started, qty):
    with pytest.raises(errors.InvalidQuantity):
        await started.request_buy(CHANNEL, ALICE, "bolt", qty)


async def test_trading_requires_started_game(service, prices):
    await service.create_game(CHANNEL, ADMIN)
    await service.join_game(CHANNEL, ALICE, "Alice")
    with pytest.raises(errors.GameNotActive):
        await service.request_buy(CHANNEL, ALICE, "bolt", 1)
    assert prices.lookups == 0  # failed fast, no network call


async def test_non_player_cannot_trade(started):
    with pytest.raises(errors.NotAPlayer):
        await started.request_buy(CHANNEL, "stranger", "bolt", 1)


async def test_max_distinct_cards(started):
    for i in range(5):
        await buy(started, ALICE, f"c{i}", 1)
    with pytest.raises(errors.TooManyDistinctCards):
        await started.request_buy(CHANNEL, ALICE, "c5", 1)
    # more copies of a card already held is fine
    await buy(started, ALICE, "c0", 1)
    # selling out of one frees a slot
    await sell(started, ALICE, "c1", 1)
    await buy(started, ALICE, "c5", 1)


async def test_max_distinct_cards_rechecked_on_confirm(started):
    for i in range(4):
        await buy(started, ALICE, f"c{i}", 1)
    a = await started.request_buy(CHANNEL, ALICE, "c4", 1)
    b = await started.request_buy(CHANNEL, ALICE, "c5", 1)
    await started.respond(CHANNEL, a.pending.id, ALICE, True)
    with pytest.raises(errors.TooManyDistinctCards):
        await started.respond(CHANNEL, b.pending.id, ALICE, True)


# --- unique ownership -----------------------------------------------------------------

async def test_unique_ownership_blocks_second_owner(started):
    await buy(started, ALICE, "bolt", 1)
    with pytest.raises(errors.CardOwnedByAnother) as info:
        await started.request_buy(CHANNEL, BOB, "bolt", 1)
    assert info.value.owner_name == "Alice"


async def test_unique_ownership_race_resolved_at_confirm(started):
    a = await started.request_buy(CHANNEL, ALICE, "bolt", 1)
    b = await started.request_buy(CHANNEL, BOB, "bolt", 1)
    await started.respond(CHANNEL, a.pending.id, ALICE, True)
    with pytest.raises(errors.CardOwnedByAnother):
        await started.respond(CHANNEL, b.pending.id, BOB, True)
    assert player(started, BOB).balance == 100_00


async def test_unique_ownership_released_after_selling_out(started):
    await buy(started, ALICE, "bolt", 2)
    await sell(started, ALICE, "bolt", 2)
    await buy(started, BOB, "bolt", 1)


async def test_unique_ownership_off_allows_sharing(service):
    await service.create_game(CHANNEL, ADMIN)
    await service.configure(CHANNEL, ADMIN, "uniqueOwnership", "false")
    await service.join_game(CHANNEL, ALICE, "Alice")
    await service.join_game(CHANNEL, BOB, "Bob")
    await service.start_game(CHANNEL, ADMIN)
    await buy(service, ALICE, "bolt", 1)
    await buy(service, BOB, "bolt", 1)


# --- selling ----------------------------------------------------------------------------

async def test_sell_credits_price_minus_fee(started, prices):
    await buy(started, ALICE, "ring", 2)  # 100 - 80 - 2 = 18
    prices.set("ring", 50_00, "Sol Ring")
    proposal = await started.request_sell(CHANNEL, ALICE, "ring", 1)
    assert proposal.balance_after == 18_00 + 50_00 - 2_00
    await started.respond(CHANNEL, proposal.pending.id, ALICE, True)
    alice = player(started, ALICE)
    assert alice.balance == 66_00
    assert alice.quantity_of("ring") == 1


async def test_selling_all_copies_removes_holding(started):
    await buy(started, ALICE, "bolt", 2)
    await sell(started, ALICE, "bolt", 2)
    assert "bolt" not in player(started, ALICE).holdings


async def test_cannot_sell_unowned_or_too_many(started):
    with pytest.raises(errors.NotEnoughCopies) as info:
        await started.request_sell(CHANNEL, ALICE, "bolt", 1)
    assert info.value.owned == 0
    await buy(started, ALICE, "bolt", 2)
    with pytest.raises(errors.NotEnoughCopies):
        await started.request_sell(CHANNEL, ALICE, "bolt", 3)


async def test_sell_cannot_go_negative_after_fee(started, prices):
    # Spend down to $0: 98 copies at $1 + $2 fee.
    await buy(started, ALICE, "c0", 98)
    prices.set("c0", 1, "Card 0")  # card crashes to $0.01
    with pytest.raises(errors.InsufficientFunds):
        await started.request_sell(CHANNEL, ALICE, "c0", 1)  # 0.01 - 2.00 < 0
    # selling enough to exactly cover the fee is allowed: 0 + 4 * 0.50 - 2.00 == 0
    prices.set("c0", 50, "Card 0")
    await sell(started, ALICE, "c0", 4)
    assert player(started, ALICE).balance == 0


async def test_double_sell_rechecked_on_confirm(started):
    await buy(started, ALICE, "bolt", 2)
    a = await started.request_sell(CHANNEL, ALICE, "bolt", 2)
    b = await started.request_sell(CHANNEL, ALICE, "bolt", 2)
    await started.respond(CHANNEL, a.pending.id, ALICE, True)
    with pytest.raises(errors.NotEnoughCopies):
        await started.respond(CHANNEL, b.pending.id, ALICE, True)


# --- confirmation handling -----------------------------------------------------------

async def test_decline(started):
    proposal = await started.request_buy(CHANNEL, ALICE, "bolt", 1)
    outcome = await started.respond(CHANNEL, proposal.pending.id, ALICE, False)
    assert isinstance(outcome, Declined)
    assert player(started, ALICE).balance == 100_00


async def test_other_users_reactions_are_ignored(started):
    proposal = await started.request_buy(CHANNEL, ALICE, "bolt", 1)
    assert await started.respond(CHANNEL, proposal.pending.id, BOB, True) is None
    assert await started.respond(CHANNEL, proposal.pending.id, ADMIN, False) is None
    assert started.get_game(CHANNEL).pending[proposal.pending.id].status == PendingStatus.OPEN


async def test_second_response_is_ignored(started):
    proposal = await started.request_buy(CHANNEL, ALICE, "bolt", 1)
    await started.respond(CHANNEL, proposal.pending.id, ALICE, True)
    assert await started.respond(CHANNEL, proposal.pending.id, ALICE, True) is None
    assert player(started, ALICE).quantity_of("bolt") == 1


async def test_late_confirmation_is_rejected(started, clock):
    proposal = await started.request_buy(CHANNEL, ALICE, "bolt", 1)
    clock.advance(minutes=29, seconds=59)
    clock.advance(seconds=1)  # exactly 30 minutes
    with pytest.raises(errors.ConfirmationExpired):
        await started.respond(CHANNEL, proposal.pending.id, ALICE, True)
    # and any later reaction gets the same polite answer
    with pytest.raises(errors.ConfirmationExpired):
        await started.respond(CHANNEL, proposal.pending.id, ALICE, True)
    assert player(started, ALICE).balance == 100_00


async def test_confirmation_just_in_time(started, clock):
    proposal = await started.request_buy(CHANNEL, ALICE, "bolt", 1)
    clock.advance(minutes=29, seconds=59)
    assert isinstance(await started.respond(CHANNEL, proposal.pending.id, ALICE, True), TradeExecuted)


async def test_external_ref_lookup(started):
    proposal = await started.request_buy(CHANNEL, ALICE, "bolt", 1)
    await started.attach_external_ref(CHANNEL, proposal.pending.id, "msg-123")
    assert started.find_pending_id(CHANNEL, "msg-123") == proposal.pending.id
    assert started.find_pending_id(CHANNEL, "msg-999") is None
    assert started.find_pending_id("no-such-channel", "msg-123") is None


async def test_unknown_pending_is_ignored(started):
    assert await started.respond(CHANNEL, "404", ALICE, True) is None
