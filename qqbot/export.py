"""把数据导出成一份人看得懂的 Markdown。

为什么要做：数据全在 SQLite 里，等于"存在但拿不出来"。
换机器、想备份、或者哪天想看看这三个月的聊天——都得先有个能读的文件。

导出的东西只读、只写文件，不碰网络、不改数据库。
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from . import digest as digest_mod


def _fmt(stamp: int | float | None) -> str:
    if not stamp:
        return "-"
    return datetime.fromtimestamp(int(stamp)).strftime("%Y-%m-%d %H:%M")


def export_all(store, settings, now: int | None = None) -> tuple[Path, dict]:
    """写一份导出文件，返回 (路径, 统计)。"""
    stamp = int(now if now is not None else time.time())
    directory = Path(settings.export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"export-{datetime.fromtimestamp(stamp):%Y%m%d-%H%M%S}.md"

    session = str(settings.allow_user_id)
    messages = store.recent_messages(session, 200)
    cards = store.list_cards(limit=500)
    events = store.list_events(status="pending", limit=50)

    lines: list[str] = [
        "# 大肥鱼 · 数据导出",
        "",
        f"- 导出时间：{_fmt(stamp)}",
        f"- 消息（最近 200 条）：{len(messages)}",
        f"- 记忆卡片：{len(cards)}",
        f"- 未到的事件：{len(events)}",
        "",
        "## 记忆卡片",
        "",
    ]
    if cards:
        for card in cards:
            mark = "" if card.get("confirmed", True) else "（待确认）"
            lines.append(
                f"- [{card.get('kind')}] {card.get('summary')}{mark}"
                f"　重要度 {card.get('importance', 0):.2f}"
                f"　命中 {card.get('hit_count', 0)}"
            )
    else:
        lines.append("（还没有）")

    lines += ["", "## 未到的事件", ""]
    if events:
        for event in events:
            lines.append(f"- {_fmt(event.get('due_at'))}　{event.get('summary')}")
    else:
        lines.append("（没有）")

    summaries = digest_mod.collect(settings, 14)
    lines += ["", "## 最近两周的摘要", ""]
    if summaries:
        for _, text in summaries:
            lines.append(text)
            lines.append("")
    else:
        lines.append("（还没有）")

    lines += ["", "## 最近 200 条对话", ""]
    for row in messages:
        who = "他" if row.get("role") == "user" else "她"
        body = (row.get("content") or "").replace("\n", " ")
        lines.append(f"- {_fmt(row.get('created_at'))}　{who}：{body}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stats = {
        "messages": len(messages),
        "cards": len(cards),
        "events": len(events),
        "summaries": len(summaries),
    }
    return path, stats
