"""人格的"这一轮"：场景 overlay + 关系阶段。

静态人设负责"她是谁"；这里只贴当场最相关的一小段，避免每轮把
 twenty 多条规则全塞进去，模型反而只记住开头。
"""

from __future__ import annotations

import time
from pathlib import Path

OVERLAYS = {
    "comfort": "comfort",
    "tease": "tease",
    "quiet": "quiet",
    "share": "share",
    "answer": "answer",
    "tired": "tired",
    "clingy": "clingy",
    "daily": "daily",
}

STAGES = (
    # (最短认识天数, 最少消息数, 阶段)
    (60, 1200, "稳"),
    (21, 400, "黏"),
    (3, 20, "熟"),
)


def pick_overlay(intent: str, mood_values: dict | None = None) -> str:
    """按他这句话和她的状态选一小段。"""
    values = mood_values or {}
    if float(values.get("tired") or 0) >= 0.75:
        return "tired"
    if float(values.get("miss") or 0) >= 0.8:
        return "clingy"
    return {
        "在难受": "comfort",
        "在生气": "quiet",
        "在逗你": "tease",
        "在分享": "share",
        "在问事": "answer",
    }.get(intent, "daily")


def overlay_text(name: str, base_dir: Path) -> str:
    path = Path(base_dir) / "prompts" / "persona" / "overlays" / f"{name}.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def block(intent: str, mood_values: dict | None, base_dir: Path) -> str:
    name = pick_overlay(intent, mood_values)
    text = overlay_text(name, base_dir)
    if not text:
        return ""
    return "【这轮的样子】\n" + text


def stage(store, session: str, now: int | None = None) -> str:
    """认识多久、聊了多少——只影响语气，不编故事。"""
    stamp = int(now if now is not None else time.time())
    first = store.first_message_at(session)
    if first is None:
        return "初识"
    days = max(0, (stamp - first) // 86400)
    count = store.count_messages(session)
    for min_days, min_count, label in STAGES:
        if days >= min_days and count >= min_count:
            return label
    return "初识"


def relation_block(store, session: str, now: int | None = None) -> str:
    label = stage(store, session, now)
    return {
        "初识": "你们刚认识没多久，你还不太敢要东西；他说忙，你就乖乖等。",
        "熟": "你们已经聊熟了；你会主动说自己的事，也敢抱怨两句。",
        "黏": "你现在很黏他，但别拿这个绑他；想他就说一句。",
        "稳": "你们已经很稳了；敢闹也敢认错，不用拿离开试探。",
    }.get(label, "")
