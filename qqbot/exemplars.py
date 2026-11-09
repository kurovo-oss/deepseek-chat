"""动态范例：按这一轮的场景挑两组"说话的样子"。

静态 8 组永远在讲同一件事；这里按 comfort/tease/share 等标签选，
并且每次轮换，避免她连续几轮用同一个开头。
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path

_CACHE: dict[str, tuple[float, list[dict]]] = {}


def load(base_dir: Path) -> list[dict]:
    path = Path(base_dir) / "prompts" / "exemplars.jsonl"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    cached = _CACHE.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    items: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            user = str(item.get("user") or "").strip()
            assistant = str(item.get("assistant") or "").strip()
            tags = item.get("tags")
            if not user or not assistant or not isinstance(tags, list):
                continue
            items.append(
                {"tags": [str(tag) for tag in tags], "user": user, "assistant": assistant}
            )
    except OSError:
        return []
    _CACHE[str(path)] = (mtime, items)
    return items


def select(
    base_dir: Path, tags: list[str] | tuple[str, ...], limit: int = 2, seed: int = 0
) -> list[dict]:
    items = load(base_dir)
    if not items:
        return []
    wanted = {str(tag) for tag in tags}
    matched = [item for item in items if wanted & set(item["tags"])]
    pool = matched or items
    start = zlib.crc32(str(seed).encode("utf-8")) % len(pool)
    picked = [pool[(start + index) % len(pool)] for index in range(min(limit, len(pool)))]
    return picked


def messages(
    base_dir: Path, tags: list[str] | tuple[str, ...], limit: int = 2, seed: int = 0
) -> list[dict]:
    rows: list[dict] = []
    for item in select(base_dir, tags, limit=limit, seed=seed):
        rows.append({"role": "user", "content": item["user"]})
        rows.append({"role": "assistant", "content": item["assistant"]})
    return rows
