"""SQLite GameRepository. One row per game; the aggregate is stored as JSON.

Game state is small (a handful of players and cards), so a document-per-game keeps the
schema trivial while status/channel columns stay indexed for lookups.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..core.models import Game, GameStatus
from ..core.serialization import game_from_dict, game_to_dict

_SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id          TEXT PRIMARY KEY,
    channel_key TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    data        TEXT NOT NULL,
    attention   INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS games_channel ON games (channel_key, created_at);
CREATE INDEX IF NOT EXISTS games_status ON games (status);
"""

# Columns added after the first release, with the SQL that fills them in for old rows.
_MIGRATIONS = (
    ("attention", "ALTER TABLE games ADD COLUMN attention INTEGER NOT NULL DEFAULT 1",
     "UPDATE games SET attention = (status != 'finished')"),
)


class SqliteGameRepository:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(games)")}
        for column, add_sql, fill_sql in _MIGRATIONS:
            if column not in columns:
                self._conn.execute(add_sql)
                self._conn.execute(fill_sql)
        self._conn.execute("CREATE INDEX IF NOT EXISTS games_attention ON games (attention)")
        self._conn.commit()

    def find_by_channel(self, channel_key: str) -> Game | None:
        row = self._conn.execute(
            """
            SELECT data FROM games WHERE channel_key = ?
            ORDER BY (status = ?) ASC, created_at DESC, rowid DESC LIMIT 1
            """,
            (channel_key, GameStatus.FINISHED.value),
        ).fetchone()
        return game_from_dict(json.loads(row[0])) if row else None

    def list_active(self) -> list[Game]:
        rows = self._conn.execute(
            "SELECT data FROM games WHERE status = ? ORDER BY created_at",
            (GameStatus.ACTIVE.value,),
        ).fetchall()
        return [game_from_dict(json.loads(r[0])) for r in rows]

    def get(self, game_id: str) -> Game | None:
        row = self._conn.execute("SELECT data FROM games WHERE id = ?", (game_id,)).fetchone()
        return game_from_dict(json.loads(row[0])) if row else None

    def list_needing_attention(self) -> list[Game]:
        rows = self._conn.execute(
            "SELECT data FROM games WHERE attention = 1 ORDER BY created_at, rowid"
        ).fetchall()
        return [game_from_dict(json.loads(r[0])) for r in rows]

    def save(self, game: Game) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO games (id, channel_key, status, created_at, data, attention)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status, data = excluded.data, attention = excluded.attention
                """,
                (
                    game.id,
                    game.channel_key,
                    game.status.value,
                    game.created_at.isoformat(),
                    json.dumps(game_to_dict(game)),
                    int(game.needs_attention),
                ),
            )

    def close(self) -> None:
        self._conn.close()
