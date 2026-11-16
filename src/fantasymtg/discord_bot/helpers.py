"""Pure Discord-side helpers, kept free of discord.py imports so they're easy to test."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Mapping

THUMBS_UP = "👍"
THUMBS_DOWN = "👎"
_SKIN_TONES = {chr(cp) for cp in range(0x1F3FB, 0x1F400)}
_VARIATION_SELECTOR = "️"
DISCORD_MESSAGE_LIMIT = 2000


def channel_key(guild_id: int, channel_id: int) -> str:
    return f"discord:{guild_id}:{channel_id}"


def parse_channel_key(key: str) -> tuple[int, int] | None:
    parts = key.split(":")
    if len(parts) != 3 or parts[0] != "discord":
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


def user_mention(user_id: str) -> str:
    return f"<@{user_id}>"


def classify_reaction(emoji: str) -> bool | None:
    """True for thumbs-up, False for thumbs-down (any skin tone), None otherwise."""
    base = "".join(ch for ch in emoji if ch not in _SKIN_TONES and ch != _VARIATION_SELECTOR)
    if base == THUMBS_UP:
        return True
    if base == THUMBS_DOWN:
        return False
    return None


def owner_decision(reactions: Iterable[tuple[str, Iterable[str]]], owner_id: str) -> bool | None:
    """Work out the owner's answer from a message's current reactions.

    `reactions` is (emoji, ids of users who reacted with it). Returns True for 👍, False
    for 👎, or None if the owner hasn't answered. If the owner left both, we can't tell
    which came last, so treat it as a decline: nothing is bought or sold by mistake.
    """
    answers = set()
    for emoji, user_ids in reactions:
        choice = classify_reaction(emoji)
        if choice is not None and owner_id in set(user_ids):
            answers.add(choice)
    if not answers:
        return None
    return answers == {True}


def fit_message(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> str:
    if len(text) <= limit:
        return text
    suffix = "\n…(truncated)"
    cut = text[: limit - len(suffix) - 4]
    if cut.count("```") % 2 == 1:  # don't leave a code block open
        cut += "\n```"
    return cut + suffix


OBSOLETE_SETTINGS = ("DISCORD_DEV_GUILD_ID",)


@dataclass(frozen=True)
class BotSettings:
    token: str
    db_path: str = "data/fantasymtg.db"
    tick_seconds: float = 60.0
    log_level: str = "INFO"
    ignored: tuple[str, ...] = ()  # obsolete settings that are still present


def load_settings(env: Mapping[str, str] = os.environ) -> BotSettings:
    token = env.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
    return BotSettings(
        token=token,
        db_path=env.get("FANTASYMTG_DB_PATH", "").strip() or "data/fantasymtg.db",
        tick_seconds=float(env.get("FANTASYMTG_TICK_SECONDS", "").strip() or 60),
        log_level=env.get("LOG_LEVEL", "").strip().upper() or "INFO",
        ignored=tuple(k for k in OBSOLETE_SETTINGS if env.get(k, "").strip()),
    )
