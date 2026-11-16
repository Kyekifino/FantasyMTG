from datetime import datetime, timedelta, timezone

from fantasymtg.adapters.sqlite_backup import backup_database, main
from fantasymtg.adapters.sqlite_repo import SqliteGameRepository
from fantasymtg.core.models import Game
from conftest import ADMIN, CHANNEL, START


def test_backup_is_a_readable_copy(tmp_path):
    db = tmp_path / "live.db"
    repo = SqliteGameRepository(db)
    repo.save(Game(id="g", channel_key=CHANNEL, admin_id=ADMIN, created_at=START))
    dest = backup_database(db, tmp_path / "backups")  # taken while the live connection is open
    repo.close()
    restored = SqliteGameRepository(dest)
    assert restored.find_by_channel(CHANNEL).id == "g"
    restored.close()


def test_backup_rotation_keeps_newest(tmp_path):
    db = tmp_path / "live.db"
    SqliteGameRepository(db).close()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for day in range(5):
        backup_database(db, tmp_path / "b", keep=3, now=t0 + timedelta(days=day))
    names = sorted(p.name for p in (tmp_path / "b").iterdir())
    assert names == ["fantasymtg-20260103-000000.db", "fantasymtg-20260104-000000.db",
                     "fantasymtg-20260105-000000.db"]


def test_missing_database_is_not_an_error(tmp_path, capsys):
    assert backup_database(tmp_path / "nope.db", tmp_path / "b") is None
    assert main([str(tmp_path / "nope.db"), str(tmp_path / "b")]) == 0
    assert "nothing to back up" in capsys.readouterr().out
