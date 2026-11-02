"""数据库每日备份。

他的历史、记忆卡片、情绪状态全在一个 sqlite 文件里。它坏一次，"她记得你"
这件事就归零了——比任何 prompt 问题都严重。

用 `VACUUM INTO` 而不是复制文件：它是 sqlite 官方的一致性快照，
不会拷到写了一半的页（直接 copy 在被写入时可能拿到坏库）。

每天一份，保留最近 N 天。备份失败只记日志，不影响聊天。
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import date
from pathlib import Path
from typing import Any

log = logging.getLogger("qqbot")

DEFAULT_KEEP = 7
NAME = "bot-{day}.db"


def backup_dir(settings: Any) -> Path:
    raw = Path(getattr(settings, "backup_dir", "") or "backups")
    if raw.is_absolute():
        return raw
    # 相对路径按项目目录算：不能因为工作目录不同就写到别处去
    return Path(getattr(settings, "base_dir", ".")) / raw


def backup_path(settings: Any, day: date) -> Path:
    return backup_dir(settings) / NAME.format(day=day.isoformat())


def exists_today(settings: Any, now: float | None = None) -> bool:
    day = date.fromtimestamp(time.time() if now is None else now)
    return backup_path(settings, day).exists()


def prune(settings: Any, keep: int | None = None) -> list[Path]:
    """只留最近的 keep 份，返回删掉的文件。"""
    limit = int(getattr(settings, "backup_keep", DEFAULT_KEEP) or DEFAULT_KEEP)
    folder = backup_dir(settings)
    try:
        files = sorted(folder.glob("bot-*.db"), reverse=True)
    except OSError:
        return []
    removed: list[Path] = []
    for path in files[limit:]:
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            log.warning("删不掉旧备份：%s", path)
    return removed


def run(settings: Any, now: float | None = None) -> Path | None:
    """今天还没备份就存一份。返回新文件路径；已存在/关闭/失败返回 None。"""
    if not getattr(settings, "backup_enabled", True):
        return None
    stamp = time.time() if now is None else now
    day = date.fromtimestamp(stamp)
    target = backup_path(settings, day)
    if target.exists():
        return None

    source = Path(settings.db_path)
    if not source.exists():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        # 先连自己再 VACUUM INTO 一份：读锁和写入互不干扰
        conn = sqlite3.connect(str(source), timeout=10)
        try:
            conn.execute("VACUUM INTO ?", (str(target),))
        finally:
            conn.close()
    except sqlite3.Error as exc:
        log.warning("数据库备份失败：%s", exc)
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        return None

    removed = prune(settings)
    size = target.stat().st_size / 1024 / 1024
    log.info(
        "数据库已备份：%s（%.1f MB）%s",
        target,
        size,
        f"，清理 {len(removed)} 份旧备份" if removed else "",
    )
    return target
