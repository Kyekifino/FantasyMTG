"""Consistent snapshots of the SQLite database, safe to take while the bot is running.

    python -m fantasymtg.adapters.sqlite_backup <db_path> <backup_dir> [--keep N]
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PREFIX = "fantasymtg-"


def backup_database(db_path: Path, backup_dir: Path, *, keep: int = 14,
                    now: datetime | None = None) -> Path | None:
    """Copy db_path into backup_dir and delete all but the newest `keep` backups.

    Returns the new backup's path, or None if there is no database yet.
    """
    if not db_path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"{PREFIX}{stamp}.db"
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)  # SQLite's online backup API: consistent even mid-write
    finally:
        dst.close()
        src.close()
    backups = sorted(backup_dir.glob(f"{PREFIX}*.db"))
    for old in backups[: max(len(backups) - keep, 0)]:
        old.unlink()
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("db_path", type=Path)
    parser.add_argument("backup_dir", type=Path)
    parser.add_argument("--keep", type=int, default=14, help="number of backups to keep")
    args = parser.parse_args(argv)
    dest = backup_database(args.db_path, args.backup_dir, keep=max(args.keep, 1))
    print(f"Backed up to {dest}" if dest else f"No database at {args.db_path}; nothing to back up")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
