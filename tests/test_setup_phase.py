from datetime import datetime, timedelta, timezone

import pytest

from fantasymtg.core import errors
from fantasymtg.core.models import GameStatus, PendingStatus
from conftest import ADMIN, ALICE, BOB, CHANNEL, START, player


async def test_create_game_assigns_admin(service):
    game = await service.create_game(CHANNEL, ADMIN)
    assert game.admin_id == ADMIN
    assert game.status == GameStatus.SETUP
    assert game.players == []


async def test_only_one_unfinished_game_per_channel(service):
    await service.create_game(CHANNEL, ADMIN)
    with pytest.raises(errors.GameAlreadyExists):
        await service.create_game(CHANNEL, ALICE)


async def test_other_channels_are_independent(service):
    await service.create_game(CHANNEL, ADMIN)
    await service.create_game("other", ALICE)
    assert service.get_game("other").admin_id == ALICE


async def test_no_game_in_channel(service):
    with pytest.raises(errors.NoGameInChannel):
        await service.join_game(CHANNEL, ALICE, "Alice")


async def test_configure_before_start_applies_immediately(service):
    await service.create_game(CHANNEL, ADMIN)
    result = await service.configure(CHANNEL, ADMIN, "tradeFee", "3.50")
    assert result.applied and result.pending is None
    assert (result.change.old, result.change.new) == ("$2.00", "$3.50")
    assert service.get_game(CHANNEL).config.trade_fee == 3_50


async def test_configure_setting_names_are_case_insensitive(service):
    await service.create_game(CHANNEL, ADMIN)
    await service.configure(CHANNEL, ADMIN, "UNIQUEOWNERSHIP", "false")
    assert service.get_game(CHANNEL).config.unique_ownership is False


async def test_only_admin_can_configure(service):
    await service.create_game(CHANNEL, ADMIN)
    with pytest.raises(errors.NotAdmin):
        await service.configure(CHANNEL, ALICE, "tradeFee", "0")


@pytest.mark.parametrize(
    "name, value",
    [
        ("maxPlayerCount", "abc"),
        ("maxPlayerCount", "0"),
        ("startingBudget", "-5"),
        ("startingBudget", "1.234"),
        ("uniqueOwnership", "maybe"),
        ("endDate", "next tuesday"),
        ("endDate", "2025-01-01"),  # in the past relative to START
        ("topOffDays", "0"),
        ("maxSpecNumber", "1.5"),
    ],
)
async def test_configure_rejects_bad_values(service, name, value):
    await service.create_game(CHANNEL, ADMIN)
    with pytest.raises(errors.InvalidSettingValue):
        await service.configure(CHANNEL, ADMIN, name, value)


async def test_configure_unknown_setting(service):
    await service.create_game(CHANNEL, ADMIN)
    with pytest.raises(errors.UnknownSetting) as info:
        await service.configure(CHANNEL, ADMIN, "startDate", "2026-01-01")
    assert "endDate" in info.value.known


async def test_cannot_lower_max_players_below_joined(service):
    await service.create_game(CHANNEL, ADMIN)
    await service.join_game(CHANNEL, ALICE, "Alice")
    await service.join_game(CHANNEL, BOB, "Bob")
    with pytest.raises(errors.InvalidSettingValue):
        await service.configure(CHANNEL, ADMIN, "maxPlayerCount", "1")


async def test_join_game(service):
    await service.create_game(CHANNEL, ADMIN)
    p = await service.join_game(CHANNEL, ALICE, "  Alice   Smith ")
    assert p.name == "Alice Smith"
    assert p.balance == 100_00


async def test_join_twice_rejected(service):
    await service.create_game(CHANNEL, ADMIN)
    await service.join_game(CHANNEL, ALICE, "Alice")
    with pytest.raises(errors.AlreadyJoined):
        await service.join_game(CHANNEL, ALICE, "Alice2")


async def test_duplicate_names_rejected_case_insensitively(service):
    await service.create_game(CHANNEL, ADMIN)
    await service.join_game(CHANNEL, ALICE, "Alice")
    with pytest.raises(errors.NameTaken):
        await service.join_game(CHANNEL, BOB, "ALICE")


@pytest.mark.parametrize("name", ["", "   ", "x" * 33, "bad`name", "**bold**"])
async def test_invalid_names_rejected(service, name):
    await service.create_game(CHANNEL, ADMIN)
    with pytest.raises(errors.InvalidName):
        await service.join_game(CHANNEL, ALICE, name)


async def test_game_full(service):
    await service.create_game(CHANNEL, ADMIN)
    await service.configure(CHANNEL, ADMIN, "maxPlayerCount", "1")
    await service.join_game(CHANNEL, ALICE, "Alice")
    with pytest.raises(errors.GameFull):
        await service.join_game(CHANNEL, BOB, "Bob")


async def test_cannot_join_started_game_by_default(started):
    with pytest.raises(errors.LateJoinNotAllowed):
        await started.join_game(CHANNEL, "carol", "Carol")


async def _allow_late_join(service):
    change = await service.configure(CHANNEL, ADMIN, "allowLateJoin", "true")
    await service.respond(CHANNEL, change.pending.id, ADMIN, True)  # mid-game: admin confirms


async def test_late_join_when_allowed(started, clock):
    clock.advance(days=61)
    await started.tick()  # two top-offs paid to existing players
    await _allow_late_join(started)
    carol = await started.join_game(CHANNEL, "carol", "Carol")
    assert carol.balance == 100_00  # startingBudget only; past top-offs aren't backdated
    assert player(started, ALICE).balance == 140_00
    # and they can trade straight away, and get future top-offs
    proposal = await started.request_buy(CHANNEL, "carol", "bolt", 1)
    await started.respond(CHANNEL, proposal.pending.id, "carol", True)
    clock.advance(days=30)
    await started.tick()
    assert player(started, "carol").balance == 100_00 - 3_00 + 20_00


async def test_late_join_still_respects_limits(started):
    await _allow_late_join(started)
    with pytest.raises(errors.NameTaken):
        await started.join_game(CHANNEL, "carol", "alice")
    with pytest.raises(errors.AlreadyJoined):
        await started.join_game(CHANNEL, ALICE, "Alice again")
    change = await started.configure(CHANNEL, ADMIN, "maxPlayerCount", "2")
    await started.respond(CHANNEL, change.pending.id, ADMIN, True)
    with pytest.raises(errors.GameFull):
        await started.join_game(CHANNEL, "carol", "Carol")


async def test_late_join_uses_current_starting_budget(started):
    for name, value in (("allowLateJoin", "true"), ("startingBudget", "50")):
        change = await started.configure(CHANNEL, ADMIN, name, value)
        await started.respond(CHANNEL, change.pending.id, ADMIN, True)
    assert (await started.join_game(CHANNEL, "carol", "Carol")).balance == 50_00


async def test_late_join_setting_can_be_turned_on_before_start(service):
    await service.create_game(CHANNEL, ADMIN)
    await service.configure(CHANNEL, ADMIN, "allowLateJoin", "yes")
    await service.join_game(CHANNEL, ALICE, "Alice")
    await service.start_game(CHANNEL, ADMIN)
    await service.join_game(CHANNEL, BOB, "Bob")
    assert [p.name for p in service.get_game(CHANNEL).players] == ["Alice", "Bob"]


async def test_no_late_join_after_game_ends(started, clock):
    await _allow_late_join(started)
    clock.advance(days=366)
    await started.tick()
    with pytest.raises(errors.GameFinished):
        await started.join_game(CHANNEL, "carol", "Carol")


async def test_start_requires_admin_and_players(service):
    await service.create_game(CHANNEL, ADMIN)
    with pytest.raises(errors.NoPlayers):
        await service.start_game(CHANNEL, ADMIN)
    await service.join_game(CHANNEL, ALICE, "Alice")
    with pytest.raises(errors.NotAdmin):
        await service.start_game(CHANNEL, ALICE)


async def test_start_sets_defaults(started):
    game = started.get_game(CHANNEL)
    assert game.status == GameStatus.ACTIVE
    assert game.started_at == START
    assert game.config.end_date == START + timedelta(days=365)


async def test_start_keeps_configured_end_date(service):
    await service.create_game(CHANNEL, ADMIN)
    await service.configure(CHANNEL, ADMIN, "endDate", "2026-03-01")
    await service.join_game(CHANNEL, ALICE, "Alice")
    await service.start_game(CHANNEL, ADMIN)
    assert service.get_game(CHANNEL).config.end_date == datetime(2026, 3, 1, tzinfo=timezone.utc)


async def test_start_fails_if_end_date_passed_before_start(service, clock):
    await service.create_game(CHANNEL, ADMIN)
    await service.configure(CHANNEL, ADMIN, "endDate", "2026-01-02")
    await service.join_game(CHANNEL, ALICE, "Alice")
    clock.advance(days=2)
    with pytest.raises(errors.InvalidSettingValue):
        await service.start_game(CHANNEL, ADMIN)


async def test_budget_change_after_joining_applies_at_start(service):
    await service.create_game(CHANNEL, ADMIN)
    await service.join_game(CHANNEL, ALICE, "Alice")
    await service.configure(CHANNEL, ADMIN, "startingBudget", "250")
    await service.start_game(CHANNEL, ADMIN)
    assert player(service, ALICE).balance == 250_00


async def test_cannot_start_twice(started):
    with pytest.raises(errors.GameNotInSetup):
        await started.start_game(CHANNEL, ADMIN)


# --- mid-game configuration requires admin confirmation ------------------------------

async def test_mid_game_configure_needs_confirmation(started):
    result = await started.configure(CHANNEL, ADMIN, "tradeFee", "5")
    assert not result.applied and result.pending is not None
    assert started.get_game(CHANNEL).config.trade_fee == 2_00

    outcome = await started.respond(CHANNEL, result.pending.id, ADMIN, True)
    assert outcome.change.new == "$5.00"
    assert started.get_game(CHANNEL).config.trade_fee == 5_00


async def test_mid_game_configure_ignores_non_admin_reactions(started):
    result = await started.configure(CHANNEL, ADMIN, "tradeFee", "5")
    assert await started.respond(CHANNEL, result.pending.id, ALICE, True) is None
    assert started.get_game(CHANNEL).config.trade_fee == 2_00


async def test_mid_game_configure_declined(started):
    result = await started.configure(CHANNEL, ADMIN, "tradeFee", "5")
    await started.respond(CHANNEL, result.pending.id, ADMIN, False)
    game = started.get_game(CHANNEL)
    assert game.config.trade_fee == 2_00
    assert game.pending[result.pending.id].status == PendingStatus.CANCELLED


async def test_mid_game_configure_expires(started, clock):
    result = await started.configure(CHANNEL, ADMIN, "tradeFee", "5")
    clock.advance(minutes=31)
    with pytest.raises(errors.ConfirmationExpired):
        await started.respond(CHANNEL, result.pending.id, ADMIN, True)
    assert started.get_game(CHANNEL).config.trade_fee == 2_00


async def test_mid_game_end_date_revalidated_on_confirm(started, clock):
    result = await started.configure(CHANNEL, ADMIN, "endDate", "2026-01-01T12:20:00Z")
    clock.advance(minutes=25)  # value is now in the past
    with pytest.raises(errors.InvalidSettingValue):
        await started.respond(CHANNEL, result.pending.id, ADMIN, True)
    game = started.get_game(CHANNEL)
    assert game.pending[result.pending.id].status == PendingStatus.FAILED
