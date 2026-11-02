"""对话状态：她得知道自己上一句问了什么、他答没答、连着问了几句。

为什么需要：模型每次都只看"历史 + 这一句"，没有"我还有个问题悬着"的概念。
于是会出现——他答了，她还问一遍；或者一口气连问三句，像查户口。
这里不猜语义，只把**事实**摆给她：她上一条问的是什么、他回的是什么、最近在聊什么。
判断交给她自己做。
"""

from __future__ import annotations

import re

QUESTION_RE = re.compile(r"[?？]|吗|呢|什么|啥|怎么|哪|几|谁|多少")
# "嗯""哦""好"这类不像回答，别当成他答完了
FILLER_RE = re.compile(r"^[\s，。.!！?？~～]*(嗯+|哦+|噢+|啊+|好+|行+|对|是|哈+|呵+|…+|\.+)[\s，。.!！?？~～]*$")
PUNCT = "，。！？、；：,.!?;:…~～ \n\t"


def asks_question(text: str) -> bool:
    return bool(QUESTION_RE.search(text or ""))


def is_filler(text: str) -> bool:
    return bool(FILLER_RE.match((text or "").strip()))


def phrase(text: str, limit: int = 16) -> str:
    """取一句短的说法：去掉标点、压成一行、超长截断。"""
    flat = re.sub(r"\s+", " ", (text or "").strip())
    flat = flat.strip(PUNCT)
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def note(messages: list[dict]) -> str:
    """根据最近几条消息，给她一段"你现在的处境"。没有内容就返回空串。"""
    if not messages:
        return ""
    her = next((m for m in reversed(messages) if m.get("role") != "user"), None)
    him = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    lines: list[str] = []

    if her is not None and asks_question(str(her.get("content") or "")):
        asked = phrase(str(her.get("content") or ""))
        heard = phrase(str((him or {}).get("content") or ""))
        lines.append(f'- 你上一条问了他："{asked}"；他这次说的是："{heard}"。')
        if heard and not is_filler(heard):
            lines.append("  他大概已经答了：接着聊，别再问同一个问题。")
        else:
            lines.append("  他好像没正面答：别再重复原话，换个问法，或者先说自己。")

    asked_times = sum(
        1
        for m in messages[-6:]
        if m.get("role") != "user" and asks_question(str(m.get("content") or ""))
    )
    if asked_times >= 2:
        lines.append(f"- 你最近连着问了 {asked_times} 句，这一轮先别问，说点自己的。")

    topics = [phrase(str(m.get("content") or ""), limit=10) for m in messages[-4:] if m.get("role") == "user"]
    # "嗯""哦"不是话题，别塞进提示里
    topics = [item for item in topics if item and len(item) >= 2 and not is_filler(item)]
    if topics:
        lines.append("- 刚才在聊：" + "、".join(topics[-2:]))

    if not lines:
        return ""
    return "【对话状态】\n" + "\n".join(lines)
