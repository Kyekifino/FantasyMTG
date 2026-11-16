"""Entry point: ``python -m fantasymtg.discord_bot`` or the ``fantasymtg-discord`` script."""
from __future__ import annotations

import asyncio
import logging

from ..adapters.scryfall import ScryfallPriceSource
from ..adapters.sqlite_repo import SqliteGameRepository
from ..core.service import GameService
from .helpers import load_settings


async def _run() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    from .bot import FantasyMTGBot  # imported late so settings errors show before discord.py loads

    repo = SqliteGameRepository(settings.db_path)
    prices = ScryfallPriceSource()
    service = GameService(repo, prices)
    bot = FantasyMTGBot(service, dev_guild_id=settings.dev_guild_id, tick_seconds=settings.tick_seconds)
    try:
        async with bot:
            await bot.start(settings.token)
    finally:
        await prices.close()
        repo.close()


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
