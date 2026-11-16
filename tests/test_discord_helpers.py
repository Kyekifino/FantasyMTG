import pytest

from fantasymtg.discord_bot.helpers import (
    channel_key,
    classify_reaction,
    fit_message,
    load_settings,
    parse_channel_key,
)


def test_channel_key_round_trip():
    key = channel_key(123, 456)
    assert key == "discord:123:456"
    assert parse_channel_key(key) == (123, 456)
    assert parse_channel_key("cli:local") is None
    assert parse_channel_key("discord:x:y") is None


@pytest.mark.parametrize(
    "emoji, expected",
    [("👍", True), ("👍🏽", True), ("👍️", True), ("👎", False), ("👎🏿", False), ("❤️", None), ("thumbsup", None)],
)
def test_classify_reaction(emoji, expected):
    assert classify_reaction(emoji) is expected


def test_fit_message_closes_code_blocks():
    text = "```\n" + "x" * 3000 + "\n```"
    fitted = fit_message(text)
    assert len(fitted) <= 2000
    assert fitted.count("```") % 2 == 0
    assert fit_message("short") == "short"


def test_load_settings():
    s = load_settings({"DISCORD_TOKEN": "t", "FANTASYMTG_TICK_SECONDS": "5"})
    assert (s.token, s.tick_seconds, s.db_path, s.ignored) == ("t", 5.0, "data/fantasymtg.db", ())
    with pytest.raises(SystemExit):
        load_settings({})


def test_obsolete_server_setting_is_flagged_not_used():
    assert load_settings({"DISCORD_TOKEN": "t", "DISCORD_DEV_GUILD_ID": ""}).ignored == ()
    s = load_settings({"DISCORD_TOKEN": "t", "DISCORD_DEV_GUILD_ID": "123"})
    assert s.ignored == ("DISCORD_DEV_GUILD_ID",)
