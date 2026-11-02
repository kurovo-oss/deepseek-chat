"""上下文预算：按 token 给每段配额，超了从最不重要的开始压。

背景：之前 system prompt 是字符串一路拼接——人设、记忆、召回、摘要、生活、能力表、
梗表、情绪、对话状态全堆上去，只有历史受字符预算约束，等于固定开销没有上限。
记忆卡片涨到几百张那天，prompt 会悄悄顶到模型的上下文上限。

这里把每一段声明成 `Section`（名字、优先级、配额），由 `fit` 统一分配：
人设永不动；其余按优先级从高到低拿配额，预算不够就先压缩最低的那几段。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# 中日韩字符大致 1 token/字；英文/数字约 3.5 字符/token。够用的估算，不追求精确。
CJK_RATIO = 1.0
ASCII_CHARS_PER_TOKEN = 3.5


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff" or "\u3040" <= char <= "\u30ff")
    other = len(text) - cjk
    return max(1, int(cjk * CJK_RATIO + other / ASCII_CHARS_PER_TOKEN))


@dataclass(frozen=True)
class Section:
    name: str
    text: str
    priority: int  # 越大越先保
    quota: int = 0  # 单段上限（token）；0 表示不单独限制
    fixed: bool = False  # 固定段（人设）不参与压缩


@dataclass
class FitResult:
    text: str
    tokens: int
    trimmed: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.text)


def trim_to_tokens(text: str, max_tokens: int) -> str:
    """把一段话压到大约 max_tokens 以内，尽量切在句子边界。"""
    if max_tokens <= 0:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text
    cut = text[: max(1, int(max_tokens * 1.1))]
    for mark in ("\n", "。", "；", "，", " "):
        pos = cut.rfind(mark)
        if pos > len(cut) * 0.5:
            cut = cut[: pos + 1]
            break
    cut = cut.rstrip()
    # 估算有误差，循环收紧到真的不超
    while cut and estimate_tokens(cut) > max_tokens:
        cut = cut[: max(1, int(len(cut) * 0.9))].rstrip()
    return (cut + "…") if cut else ""


def fit(sections: list[Section], budget: int) -> FitResult:
    """按优先级分配预算。人设不受约束，其余段超了会被压缩或整段丢掉。"""
    budget = max(0, budget)
    fixed_tokens = sum(estimate_tokens(s.text) for s in sections if s.fixed)
    remaining = budget - fixed_tokens

    kept: dict[int, str] = {}
    trimmed: list[str] = []
    dropped: list[str] = []

    for index, section in sorted(enumerate(sections), key=lambda pair: -pair[1].priority):
        if section.fixed:
            kept[index] = section.text
            continue
        if not section.text or not section.text.strip():
            continue
        if remaining <= 30:
            dropped.append(section.name)
            continue
        allowed = min(section.quota, remaining) if section.quota else remaining
        text = trim_to_tokens(section.text, allowed)
        if not text:
            dropped.append(section.name)
            continue
        if text != section.text:
            trimmed.append(section.name)
        kept[index] = text
        remaining -= estimate_tokens(text)

    ordered = [kept[i] for i in sorted(kept)]
    joined = "\n\n".join(ordered)
    return FitResult(
        text=joined,
        tokens=estimate_tokens(joined),
        trimmed=tuple(trimmed),
        dropped=tuple(dropped),
    )


def history_budget(total_budget: int, system_tokens: int) -> int:
    """system 占完之后，历史还能用多少 token。"""
    return max(200, total_budget - system_tokens)
