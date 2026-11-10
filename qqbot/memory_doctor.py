"""记忆体检与安全清理。

只做三件事：报告计数、清掉未确认卡片、清掉派生的摘要/记忆本。
所有写操作默认 dry-run；真正执行前会先备份数据库和文件。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import memory_epoch

MEMORY_FILES = ("journal.md", "recap.json")


def _connect(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> int:
    try:
        row = conn.execute(sql, args).fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0] or 0) if row else 0


def inspect(
    db_path: Path,
    daily_dir: Path,
    journal_file: Path,
    recap_file: Path,
) -> dict[str, Any]:
    """只读体检；不输出任何记忆正文。"""
    db_path = Path(db_path)
    daily_dir = Path(daily_dir)
    journal_file = Path(journal_file)
    recap_file = Path(recap_file)
    report: dict[str, Any] = {
        "db": str(db_path),
        "db_exists": db_path.exists(),
        "messages": 0,
        "cards_active": 0,
        "cards_archived": 0,
        "cards_pending": 0,
        "facts": 0,
        "profiles": 0,
        "daily_files": len(list(daily_dir.glob("*.md"))) if daily_dir.is_dir() else 0,
        "journal_chars": 0,
        "recap_exists": recap_file.exists(),
        "clean_mark": memory_epoch.read(Path(db_path).parent.parent),
    }
    if journal_file.exists():
        try:
            report["journal_chars"] = len(journal_file.read_text(encoding="utf-8").strip())
        except OSError:
            report["journal_chars"] = 0

    conn = _connect(db_path)
    if conn is None:
        return report
    try:
        if _table_exists(conn, "messages"):
            report["messages"] = _count(conn, "SELECT COUNT(*) FROM messages")
        if _table_exists(conn, "cards"):
            report["cards_active"] = _count(
                conn, "SELECT COUNT(*) FROM cards WHERE status = 'active'"
            )
            report["cards_archived"] = _count(
                conn, "SELECT COUNT(*) FROM cards WHERE status = 'archived'"
            )
            report["cards_pending"] = _count(
                conn, "SELECT COUNT(*) FROM cards WHERE confirmed = 0"
            )
        if _table_exists(conn, "facts"):
            report["facts"] = _count(conn, "SELECT COUNT(*) FROM facts")
        if _table_exists(conn, "profiles"):
            report["profiles"] = _count(conn, "SELECT COUNT(*) FROM profiles")
    finally:
        conn.close()
    return report


def _unique_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = root / f"memory-doctor-{stamp}"
    if target.exists():
        for index in range(1, 100):
            candidate = root / f"memory-doctor-{stamp}-{index}"
            if not candidate.exists():
                target = candidate
                break
    target.mkdir(parents=True, exist_ok=False)
    return target


def backup(
    db_path: Path,
    daily_dir: Path,
    journal_file: Path,
    recap_file: Path,
    backup_dir: Path,
) -> Path:
    """用 sqlite backup API 复制数据库，文件也一起放进同一个备份目录。"""
    target = _unique_dir(Path(backup_dir))
    db_path = Path(db_path)
    if db_path.exists():
        source = sqlite3.connect(str(db_path), timeout=10)
        try:
            dest = sqlite3.connect(str(target / "chat.db"))
            try:
                source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()
    for path in (journal_file, recap_file):
        path = Path(path)
        if path.is_file():
            shutil.copy2(path, target / path.name)
    daily_dir = Path(daily_dir)
    if daily_dir.is_dir():
        shutil.copytree(daily_dir, target / daily_dir.name)
    return target


def _move_away(path: Path, target: Path) -> bool:
    path = Path(path)
    if not path.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(target))
    return True


def clean_pending(
    db_path: Path,
    daily_dir: Path,
    journal_file: Path,
    recap_file: Path,
    backup_dir: Path,
    apply: bool = False,
) -> dict[str, Any]:
    report = inspect(db_path, daily_dir, journal_file, recap_file)
    result = {"action": "clean_pending", "apply": apply, "before": report}
    if not apply:
        return result
    result["backup"] = str(backup(db_path, daily_dir, journal_file, recap_file, backup_dir))
    memory_epoch.mark(Path(db_path).parent.parent)
    conn = _connect(Path(db_path))
    if conn is None:
        result["removed"] = 0
        return result
    try:
        if _table_exists(conn, "cards"):
            cur = conn.execute("DELETE FROM cards WHERE confirmed = 0")
            conn.commit()
            result["removed"] = int(cur.rowcount or 0)
        else:
            result["removed"] = 0
    finally:
        conn.close()
    result["after"] = inspect(db_path, daily_dir, journal_file, recap_file)
    return result


def clean_derived(
    db_path: Path,
    daily_dir: Path,
    journal_file: Path,
    recap_file: Path,
    backup_dir: Path,
    apply: bool = False,
) -> dict[str, Any]:
    report = inspect(db_path, daily_dir, journal_file, recap_file)
    result = {"action": "clean_derived", "apply": apply, "before": report}
    if not apply:
        return result
    target = backup(db_path, daily_dir, journal_file, recap_file, backup_dir)
    memory_epoch.mark(Path(db_path).parent.parent)
    result["backup"] = str(target)
    removed = []
    for path in (Path(journal_file), Path(recap_file), Path(daily_dir)):
        if _move_away(path, target / "removed" / path.name):
            removed.append(str(path))
    result["removed"] = removed
    result["after"] = inspect(db_path, daily_dir, journal_file, recap_file)
    return result


def reset_all(
    db_path: Path,
    daily_dir: Path,
    journal_file: Path,
    recap_file: Path,
    backup_dir: Path,
    apply: bool = False,
) -> dict[str, Any]:
    report = inspect(db_path, daily_dir, journal_file, recap_file)
    result = {"action": "reset_all", "apply": apply, "before": report}
    if not apply:
        return result
    target = backup(db_path, daily_dir, journal_file, recap_file, backup_dir)
    result["backup"] = str(target)
    removed = []
    db_path = Path(db_path)
    for path in (
        db_path,
        db_path.with_name(db_path.name + "-wal"),
        db_path.with_name(db_path.name + "-shm"),
        Path(journal_file),
        Path(recap_file),
        Path(daily_dir),
    ):
        if _move_away(path, target / "removed" / path.name):
            removed.append(str(path))
    result["removed"] = removed
    result["after"] = inspect(db_path, daily_dir, journal_file, recap_file)
    return result
