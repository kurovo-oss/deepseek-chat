"""数据库每日备份：能读回来、一天只备一次、旧的不留着堆积。"""

import sqlite3
from datetime import date, timedelta
from types import SimpleNamespace

from qqbot import backup
from qqbot.store import Store


def make_settings(tmp_path, **overrides):
    values = dict(
        base_dir=tmp_path,
        db_path=tmp_path / "bot.db",
        backup_enabled=True,
        backup_dir=tmp_path / "backups",
        backup_keep=7,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def seed(settings, count=3):
    store = Store(settings.db_path)
    for index in range(count):
        store.save_message("100", "user", f"第{index}句")
    store.close()


def count_rows(path):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("select count(*) from messages").fetchone()[0]
    finally:
        conn.close()


def test_backup_writes_a_readable_copy(tmp_path):
    settings = make_settings(tmp_path)
    seed(settings, 4)

    path = backup.run(settings, now=date(2026, 11, 2).toordinal() * 0 + 1762000000)

    assert path is not None and path.exists()
    assert count_rows(path) == 4  # 不是空文件，是能读回来的快照


def test_backup_runs_once_a_day(tmp_path):
    settings = make_settings(tmp_path)
    seed(settings)
    now = 1762000000

    assert backup.run(settings, now=now) is not None
    assert backup.run(settings, now=now + 60) is None


def test_prune_keeps_the_newest(tmp_path):
    settings = make_settings(tmp_path, backup_keep=3)
    folder = tmp_path / "backups"
    folder.mkdir()
    for offset in range(5):
        day = date(2026, 11, 1) + timedelta(days=offset)
        (folder / f"bot-{day.isoformat()}.db").write_bytes(b"")

    removed = backup.prune(settings)

    assert len(removed) == 2
    kept = sorted(item.name for item in folder.glob("bot-*.db"))
    assert kept == ["bot-2026-11-03.db", "bot-2026-11-04.db", "bot-2026-11-05.db"]


def test_disabled_does_nothing(tmp_path):
    settings = make_settings(tmp_path, backup_enabled=False)
    seed(settings)

    assert backup.run(settings, now=1762000000) is None
    assert not (tmp_path / "backups").exists()


def test_missing_database_is_a_noop(tmp_path):
    settings = make_settings(tmp_path)  # 没建库

    assert backup.run(settings, now=1762000000) is None


def test_engine_backs_up_during_background_tick(tmp_path):
    """不靠定时任务，挂在已有的后台 tick 上——它本来就在跑。"""
    from tests.test_engine import build_env

    env = build_env(tmp_path)
    env.engine.background_tick()

    files = list((tmp_path / "backups").glob("bot-*.db"))
    assert files, "background_tick 应该顺手存一份备份"
