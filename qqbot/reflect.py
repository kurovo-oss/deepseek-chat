"""会反省：睡前把今天的事过一遍，写下一两条想通的。

fish-river 的"五步循环"（表态 → 撞上 → 落差 → 改写 → 回顾）落到这里：
白天的事件流和拒绝记录是原料，晚上用模型写一到两条 `self` 事件，
第二天开始进上下文——人格就是这么一点点长出来的。

可持续的两条：一天只跑一次；一次最多两条、每条很短，不让她越反省越啰嗦。
"""

from __future__ import annotations

import json
import logging
import re
import time

from . import growth

log = logging.getLogger("qqbot")

CURSOR_KEY = "reflect:last"
MAX_THOUGHTS = 2
MAX_CHARS = 60

PROMPT = """你是「大肥鱼」，18 岁，在跟他谈恋爱。

下面是今天发生在你身上的事（事件流）和他今天说过的话。睡前你躺着想了一会儿。

写 1 到 2 条你今晚**真想明白**的事，要求：
- 第一人称，像自言自语，不超过 {chars} 字
- 只写你的想法，不要复述聊天、不要总结、不要"我学到了很多"这种空话
- 可以是：对他的新认识、对自己某个反应的后悔、想通的某件小事
- 没有真想明白的就输出空数组，别硬编

只输出 JSON：{{"thoughts": ["……", "……"]}}

【今天的事件】
{events}

【他今天说的】
{said}
"""


def due(store, settings, now: int | None = None) -> bool:
    if not getattr(settings, "reflect_enabled", False):
        return False
    stamp = int(now if now is not None else time.time())
    last = int(store.get_cursor(CURSOR_KEY, "0") or 0)
    return stamp - last >= 20 * 3600


def _parse(raw: str) -> list[str]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```$", "", text).strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except (ValueError, TypeError):
            return []
    items = data.get("thoughts") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    result: list[str] = []
    for item in items:
        text = str(item or "").strip().replace("\n", " ")
        if len(text) < 2:
            continue
        result.append(text[:MAX_CHARS])
        if len(result) >= MAX_THOUGHTS:
            break
    return result


def reflect(store, llm, settings, now: int | None = None) -> list[str]:
    """跑一次反省，返回写下的那几条（失败返回空）。"""
    if not getattr(settings, "reflect_enabled", False):
        return []
    stamp = int(now if now is not None else time.time())
    events = growth.recent(store, since=stamp - 86400, limit=40)

    event_lines = []
    for row in events:
        who = {"said": "他", "self": "我", "turn": "我改口", "refuse": "我拒绝", "reflect": "我反省"}.get(
            str(row.get("kind")), "我"
        )
        event_lines.append(f"{who}：{str(row.get('text') or '')[:80]}")

    said_rows = store.messages_between(stamp - 86400, stamp + 1, limit=60)
    said = []
    for row in said_rows:
        text = str(row.get("content") or "").replace("\n", " ")[:60]
        if row.get("role") != "user" or not text or text.startswith("/"):
            continue
        said.append(text)

    if not events and not said:
        return []

    prompt = PROMPT.format(
        chars=MAX_CHARS,
        events="\n".join(event_lines) or "（今天没发生什么特别的事）",
        said="\n".join(f"- {line}" for line in said[-20:]) or "（他今天话不多）",
    )
    try:
        result = llm.chat(
            [{"role": "user", "content": prompt}], temperature=0.6, max_tokens=2000
        )
        thoughts = _parse(getattr(result, "text", "") or "")
    except Exception:  # noqa: BLE001 - 反省失败不影响她睡觉
        log.exception("反省失败")
        return []

    for thought in thoughts:
        growth.record(store, "self", thought, salience=0.6, source="her", now=stamp)
    store.set_cursor(CURSOR_KEY, str(stamp))
    if thoughts:
        log.info("反省：写下 %d 条", len(thoughts))
    return thoughts
