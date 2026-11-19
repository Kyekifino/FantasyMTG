from datetime import datetime, timezone

import pytest

from fantasymtg.core.config import GameConfig, describe, find_setting
from fantasymtg.core.money import dollars_to_cents, format_money, parse_money


@pytest.mark.parametrize(
    "raw, cents",
    [("100", 100_00), ("$2", 2_00), ("2.5", 2_50), (" $1,000.05 ", 1000_05), ("0", 0), ("-3", -3_00)],
)
def test_parse_money(raw, cents):
    assert parse_money(raw) == cents


@pytest.mark.parametrize("raw", ["abc", "1.234", "", "$", "nan", "inf"])
def test_parse_money_rejects(raw):
    with pytest.raises(ValueError):
        parse_money(raw)


def test_format_money():
    assert format_money(0) == "$0.00"
    assert format_money(5) == "$0.05"
    assert format_money(123456_78) == "$123,456.78"
    assert format_money(-2_50) == "-$2.50"


def test_dollars_to_cents():
    assert dollars_to_cents("0.10") == 10
    assert dollars_to_cents("12") == 12_00
    assert dollars_to_cents(None) is None
    assert dollars_to_cents("garbage") is None


def test_defaults_match_spec():
    cfg = GameConfig()
    assert cfg.max_player_count == 10
    assert cfg.starting_budget == 100_00
    assert cfg.max_spec_number == 5
    assert cfg.trade_fee == 2_00
    assert cfg.top_off_days == 30
    assert cfg.top_off_budget == 20_00
    assert cfg.unique_ownership is True
    assert cfg.allow_late_join is False
    assert cfg.end_date is None  # one year after start


@pytest.mark.parametrize("raw", ["yes", "TRUE", "on", "1"])
def test_bool_parsing(raw):
    assert find_setting("uniqueOwnership").parse(raw) is True


def test_date_parsing():
    s = find_setting("enddate")
    assert s.parse("2027-01-31") == datetime(2027, 1, 31, tzinfo=timezone.utc)
    assert s.parse("2027-01-31T18:30:00-05:00") == datetime(2027, 1, 31, 23, 30, tzinfo=timezone.utc)
    assert s.format(datetime(2027, 1, 31, tzinfo=timezone.utc)) == "2027-01-31"
    assert s.format(None) == "1 year after the game starts"


def test_describe_lists_every_setting():
    names = [s.name for s, _ in describe(GameConfig())]
    assert names == [
        "endDate", "maxPlayerCount", "startingBudget", "maxSpecNumber",
        "tradeFee", "topOffDays", "topOffBudget", "uniqueOwnership", "allowLateJoin",
    ]
