"""Fantasy MTG: a fantasy-football style Magic: The Gathering trading game.

Layout:
    core/          Pure game rules and orchestration. No Discord, no HTTP, no SQL.
    adapters/      Implementations of core ports (Scryfall prices, SQLite storage).
    presentation/  Interface-neutral text rendering (tables, help, error wording).
    discord_bot/   The Discord interface. Thin: translates Discord events to core calls.
"""
