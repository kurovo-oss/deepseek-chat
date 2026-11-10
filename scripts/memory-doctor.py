# -*- coding: utf-8 -*-
"""记忆体检与清理。默认只读；加 --apply 才会改数据，并且先备份。

用法：
    tools\\python\\python.exe scripts\\memory-doctor.py
    tools\\python\\python.exe scripts\\memory-doctor.py --clean-pending --apply
    tools\\python\\python.exe scripts\\memory-doctor.py --clean-derived --apply
    tools\\python\\python.exe scripts\\memory-doctor.py --reset-all --apply

--reset-all 会连消息、卡片、日摘要、前情、记忆本一起清空，保留人格和配置。
执行前请先停止机器人，避免 SQLite/WAL 被占用。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qqbot import memory_doctor  # noqa: E402


def _paths(args) -> dict[str, Path]:
    base = Path(args.base_dir).resolve()
    return {
        "db_path": (base / args.db).resolve(),
        "daily_dir": (base / args.daily).resolve(),
        "journal_file": (base / args.journal).resolve(),
        "recap_file": (base / args.recap).resolve(),
        "backup_dir": (base / args.backup_dir).resolve(),
    }


def _print(report: dict) -> None:
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="大肥鱼记忆体检与安全清理")
    parser.add_argument("--base-dir", default=".", help="项目根目录，默认当前目录")
    parser.add_argument("--db", default="data/chat.db", help="数据库相对路径")
    parser.add_argument("--daily", default="data/daily", help="日摘要目录")
    parser.add_argument("--journal", default="data/journal.md", help="记忆本文件")
    parser.add_argument("--recap", default="data/recap.json", help="前情摘要文件")
    parser.add_argument("--backup-dir", default="backups", help="备份目录")
    parser.add_argument("--apply", action="store_true", help="真正执行；不加只做 dry-run")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--clean-pending", action="store_true", help="删除未确认的推测卡片")
    group.add_argument("--clean-derived", action="store_true", help="移走日摘要/前情/记忆本")
    group.add_argument("--reset-all", action="store_true", help="清空数据库、消息和全部记忆")
    args = parser.parse_args(argv)
    paths = _paths(args)

    if args.reset_all:
        result = memory_doctor.reset_all(**paths, apply=args.apply)
    elif args.clean_derived:
        result = memory_doctor.clean_derived(**paths, apply=args.apply)
    elif args.clean_pending:
        result = memory_doctor.clean_pending(**paths, apply=args.apply)
    else:
        result = memory_doctor.inspect(
            paths["db_path"], paths["daily_dir"], paths["journal_file"], paths["recap_file"]
        )
        _print(result)
        return 0

    _print(result)
    if not args.apply:
        print("\nDRY-RUN: nothing changed. Re-run with --apply to execute.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
