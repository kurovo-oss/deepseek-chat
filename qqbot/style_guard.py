"""风格守卫：拦掉最像客服/AI 的几句，不追求大而全。

模型偶尔会冒出"听起来你…""我能理解你的感受""希望这些建议"。
这些句子一出现，前面攒的"像人"就白费了。这里只删高精度命中，
宁可漏，不误伤——删完是空就保留原文，不把她的话弄没。
"""

from __future__ import annotations

import re

HITS = (
    "听起来你",
    "我能理解",
    "我理解你的感受",
    "作为AI",
    "作为人工智能",
    "希望这些建议",
    "希望对你有所帮助",
    "首先",
    "其次",
    "总而言之",
    "总的来说",
    "你可以尝试",
    "建议你",
    "深呼吸",
    "我在这里陪着你",
    "你的感受是",
    "我们之间",
    "我们的关系",
    "根据我的记忆",
    "系统提示",
    "数据库",
    "记得好好吃饭",
    "今天也要开心",
)

_SPLIT = re.compile(r"(?<=[。！？!?…\n])")


def hits(text: str) -> list[str]:
    return [phrase for phrase in HITS if phrase in (text or "")]


def soften(text: str) -> str:
    """删掉命中句；如果删空了就保留原文，不制造空回复。"""
    original = (text or "").strip()
    if not original:
        return original
    pieces = [piece for piece in _SPLIT.split(original) if piece]
    kept = [piece for piece in pieces if not hits(piece)]
    if not kept:
        return original
    result = "".join(kept).strip()
    # 只删了标点/空白的情况也算没删
    return result or original
