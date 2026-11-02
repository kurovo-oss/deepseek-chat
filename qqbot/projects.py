"""她手上正在做的事。

以前“生活线”是我给她排的菜单（`BUSY_THINGS` 按日期轮换）：今天是哪件，我定。
她只负责把指定的那件事写成一句日记——那不算过日子，算填表。

现在改成项目：一件她自己在推进的事，有目标、有进度、有卡住的地方，
可以放着、可以放弃、也可以开新的。每天她挑一件推进，并说出为什么。

状态存 `data/projects.json`，人看得懂、改得动，坏了也不影响聊天。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("qqbot")

STATUSES = ("active", "paused", "dropped", "done")
MAX_ACTIVE = 3


def _path(settings: Any) -> Path:
    custom = getattr(settings, "projects_file", None)
    if custom:
        return Path(custom)
    return Path(getattr(settings, "base_dir", ".")) / "data" / "projects.json"


def load(settings: Any) -> list[dict]:
    try:
        data = json.loads(_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("projects") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("title")]


def save(settings: Any, projects: Iterable[dict]) -> None:
    path = _path(settings)
    payload = {"version": 1, "projects": list(projects)}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # 半截文件比没文件更糟


def active(projects: list[dict]) -> list[dict]:
    return [item for item in projects if item.get("status", "active") == "active"]


def _find(projects: list[dict], title: str) -> dict | None:
    title = (title or "").strip()
    if not title:
        return None
    for item in projects:
        if item["title"] == title:
            return item
    for item in projects:
        if title in item["title"] or item["title"] in title:
            return item
    return None


def apply(projects: list[dict], decision: dict, day: str) -> list[dict]:
    """把模型的选择落到项目表上。action 不认识就原样返回。"""
    items = [dict(item) for item in projects]
    action = str(decision.get("action") or "none").strip().lower()
    title = str(decision.get("project") or "").strip()
    note = str(decision.get("note") or "").strip()[:60]
    reason = str(decision.get("reason") or "").strip()[:60]

    if action == "none":
        return items

    if action == "start":
        if not title:
            return items
        existing = _find(items, title)
        if existing is not None:  # 她说"开新的"，其实是旧事重提
            existing["status"] = "active"
            existing["touched"] = day
            existing["note"] = note or existing.get("note", "")
            return items
        items.append(
            {
                "title": title,
                "status": "active",
                "note": note,
                "reason": reason,
                "attempts": 1,
                "started": day,
                "touched": day,
            }
        )
        _trim(items)
        return items

    target = _find(items, title)
    if target is None:
        return items

    if action == "advance":
        target["status"] = "active"
        target["attempts"] = int(target.get("attempts") or 0) + 1
        target["note"] = note or target.get("note", "")
        target["reason"] = reason or target.get("reason", "")
        target["touched"] = day
    elif action in ("pause", "drop", "done"):
        target["status"] = {"pause": "paused", "drop": "dropped", "done": "done"}[action]
        target["note"] = note or target.get("note", "")
        target["touched"] = day
    return items


def _trim(items: list[dict]) -> None:
    """同时进行的最多 3 件，多了就把最久没碰的放下——她也不会同时忙四件事。"""
    live = [item for item in items if item.get("status") == "active"]
    if len(live) <= MAX_ACTIVE:
        return
    live.sort(key=lambda item: str(item.get("touched") or ""))
    for stale in live[: len(live) - MAX_ACTIVE]:
        stale["status"] = "paused"


def block(projects: list[dict], seeds: tuple[str, ...] = ()) -> str:
    """喂给模型的那段：手上的事 + 允许开新的。"""
    live = active(projects)
    lines: list[str] = []
    for item in live[:MAX_ACTIVE]:
        note = item.get("note") or "刚开始"
        lines.append(f"- {item['title']}：{note}（已经试了 {item.get('attempts', 1)} 次）")
    if not lines:
        lines.append("- （现在手上没有正在进行的事）")
    text = "你手上正在做的事：\n" + "\n".join(lines)
    if seeds:
        text += "\n也可以开一件新的（只是参考，不一定非得是这些）：" + "、".join(seeds[:5])
    return text


def describe(projects: list[dict]) -> str:
    """给 /生活 这类命令看的一行摘要。"""
    live = active(projects)
    if not live:
        return "手上没有正在进行的事。"
    parts = [f"{item['title']}（{item.get('attempts', 1)} 次）" for item in live]
    return "手上：" + "、".join(parts)
