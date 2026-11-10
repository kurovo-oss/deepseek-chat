# -*- coding: utf-8 -*-
from pathlib import Path

from qqbot import memory_doctor
from qqbot.store import Store


def _paths(tmp_path: Path) -> dict:
    data = tmp_path / "data"
    data.mkdir()
    daily = data / "daily"
    daily.mkdir()
    (daily / "2026-11-10.md").write_text("测试日摘要", encoding="utf-8")
    journal = data / "journal.md"
    journal.write_text("测试记忆本", encoding="utf-8")
    recap = data / "recap.json"
    recap.write_text("{}", encoding="utf-8")
    return {
        "db_path": data / "chat.db",
        "daily_dir": daily,
        "journal_file": journal,
        "recap_file": recap,
        "backup_dir": tmp_path / "backups",
    }


def _seed(paths: dict) -> None:
    store = Store(paths["db_path"])
    store.save_message("123", "user", "在吗")
    store.add_card("fact", "已确认的事实", confirmed=True)
    store.add_card("fact", "还没确认的猜测", confirmed=False)
    store.close()


def test_inspect_reports_counts_without_content(tmp_path):
    paths = _paths(tmp_path)
    _seed(paths)

    report = memory_doctor.inspect(
        paths["db_path"], paths["daily_dir"], paths["journal_file"], paths["recap_file"]
    )

    assert report["messages"] == 1
    assert report["cards_active"] == 2
    assert report["cards_pending"] == 1
    assert report["daily_files"] == 1
    assert report["journal_chars"] == len("测试记忆本")
    assert "已确认的事实" not in str(report)


def test_clean_pending_dry_run_then_apply(tmp_path):
    paths = _paths(tmp_path)
    _seed(paths)

    dry = memory_doctor.clean_pending(**paths, apply=False)
    assert "removed" not in dry
    assert memory_doctor.inspect(
        paths["db_path"], paths["daily_dir"], paths["journal_file"], paths["recap_file"]
    )["cards_pending"] == 1

    result = memory_doctor.clean_pending(**paths, apply=True)
    assert result["removed"] == 1
    assert result["after"]["cards_pending"] == 0
    assert result["after"]["cards_active"] == 1
    assert (Path(result["backup"]) / "chat.db").is_file()


def test_clean_derived_keeps_database(tmp_path):
    paths = _paths(tmp_path)
    _seed(paths)

    result = memory_doctor.clean_derived(**paths, apply=True)

    assert result["after"]["messages"] == 1
    assert result["after"]["daily_files"] == 0
    assert result["after"]["journal_chars"] == 0
    assert result["after"]["recap_exists"] is False
    assert paths["db_path"].is_file()
    assert (Path(result["backup"]) / "daily" / "2026-11-10.md").is_file()


def test_reset_all_moves_database_and_memory_files(tmp_path):
    paths = _paths(tmp_path)
    _seed(paths)

    result = memory_doctor.reset_all(**paths, apply=True)

    assert result["after"]["db_exists"] is False
    assert result["after"]["daily_files"] == 0
    assert result["after"]["journal_chars"] == 0
    assert paths["db_path"].exists() is False
    assert (Path(result["backup"]) / "chat.db").is_file()
    assert (Path(result["backup"]) / "removed" / "chat.db").is_file()
