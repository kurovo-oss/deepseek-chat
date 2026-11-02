"""对话状态：她得知道自己上一句问了什么、他答没答、连着问了几句。

为什么需要：模型每次都只看"历史 + 这一句"，没有"我还有个问题悬着"的概念。
于是会出现——他答了，她还问一遍；或者一口气连问三句，像查户口。
这里不猜语义，只把**事实**摆给她：她上一条问的是什么、他回的是什么、最近在聊什么。
判断交给她自己做。
"""

from __future__ import annotations

import re

# 2 字词里没信息量的，别当"老提的话题"
STOP_GRAMS = frozenset(
    """
    他你 你我 我的 你的 他的 我们 你们 他们 自己 什么 怎么 这个 那个 就是 不是 没有
    可以 觉得 知道 现在 今天 刚才 真的 有点 一个 还是 已经 然后 这么 那么 因为 所以
    但是 如果 时候 东西 事情 也是 就是 不会 不要 是不 有一 好像 应该 可能 其实 而且
    """.split()
)


# 虚词：含这些字的 2 字片段基本是"这个""时候"这类碎片，不是话题
FUNCTION_CHARS = frozenset("这那个时候的是在有和就都也很我你他她它们不没要会能把被给对从到与及或而但却还又再才只更最太挺比像跟向往让使吗呢吧啊呀嘛")


def repeated_topic(messages: list[dict], window: int = 6, min_count: int = 3) -> str:
    """她最近反复提的那个词。真人不会每句都绕回同一件事。"""
    replies = [
        str(m.get("content") or "")
        for m in messages
        if m.get("role") != "user"
    ][-window:]
    if len(replies) < min_count:
        return ""
    counts: dict[str, int] = {}
    for text in replies:
        seen: set[str] = set()
        for index in range(len(text) - 1):
            gram = text[index : index + 2]
            if not all("\u4e00" <= char <= "\u9fff" for char in gram):
                continue
            if gram in STOP_GRAMS:
                continue
            # 含虚词的碎片（"个时""候真"）不是话题
            if any(char in FUNCTION_CHARS for char in gram):
                continue
            seen.add(gram)
        for gram in seen:
            counts[gram] = counts.get(gram, 0) + 1
    best = ""
    best_count = 0
    for gram, count in counts.items():
        if count < min_count:
            continue
        # 被更长的词覆盖就不单独报（"画画"优先于"画了"这种残缺片段）
        if any(gram != other and gram in other and n >= count for other, n in counts.items()):
            continue
        if count > best_count or (count == best_count and len(gram) > len(best)):
            best, best_count = gram, count
    return best


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

    repeat = repeated_topic(messages)
    if repeat:
        lines.append(f"- 你最近老是提「{repeat}」，这几轮别再说它，换个话题。")

    topics = [phrase(str(m.get("content") or ""), limit=10) for m in messages[-4:] if m.get("role") == "user"]
    # "嗯""哦"不是话题，别塞进提示里
    topics = [item for item in topics if item and len(item) >= 2 and not is_filler(item)]
    if topics:
        lines.append("- 刚才在聊：" + "、".join(topics[-2:]))

    if not lines:
        return ""
    return "【对话状态】\n" + "\n".join(lines)
