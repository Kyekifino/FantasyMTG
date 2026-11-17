"""Entry point: ``python -m fantasymtg.discord_bot`` or the ``fantasymtg-discord`` script."""
from __future__ import annotations

import asyncio
import logging
import signal

from ..adapters.scryfall import ScryfallPriceSource
from ..adapters.sqlite_repo import SqliteGameRepository
from ..core.service import GameService
from .helpers import load_settings


async def _run() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if settings.ignored:
        logging.getLogger(__name__).warning(
            "Ignoring %s: no longer used; commands now register in every server",
            ", ".join(settings.ignored),
        )
    from .bot import FantasyMTGBot  # imported late so settings errors show before discord.py loads

    repo = SqliteGameRepository(settings.db_path)
    prices = ScryfallPriceSource()
    service = GameService(repo, prices)
    bot = FantasyMTGBot(service, tick_seconds=settings.tick_seconds)
    _close_on_signals(bot)
    try:
        async with bot:
            await bot.start(settings.token)
    finally:
        await prices.close()
        repo.close()


def _close_on_signals(bot) -> None:
    """Shut down cleanly on `systemctl stop`, `docker stop` or Ctrl+C.

    Without this, SIGTERM kills Python immediately. With it, the bot finishes the step it's
    on (so a save is never cut off), disconnects from Discord, and closes the database.
    """
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig: _request_close(bot, s))
        except (NotImplementedError, RuntimeError):  # not supported on Windows
            pass


def _request_close(bot, sig: signal.Signals) -> None:
    logging.getLogger(__name__).info("Received %s, shutting down", sig.name)
    asyncio.ensure_future(bot.close())


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
