"""文本处理：Markdown 清洗、分段、图片标记提取。

这些都是纯函数，不碰 IO 与网络，方便单测——也正因为如此，
它们是整个项目里最容易被改坏、又最影响观感的部分。
"""

from __future__ import annotations

import re

# {{img:cat}} / {{ img : cat }} 都接受
IMAGE_TAG_RE = re.compile(r"\{\{\s*img\s*:\s*([^}]*?)\s*\}\}", re.IGNORECASE)
VALID_TAG_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

# 句末标点：切分时优先在这些位置断开
SENTENCE_END = "。！？!?；;…\n"

_CODE_FENCE_RE = re.compile(r"^\s*```.*$", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`([^`\n]*)`")
_IMAGE_MD_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_MD_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_QUOTE_RE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_LIST_RE = re.compile(r"^\s{0,3}(?:[-*+]|\d+[.、)])\s+", re.MULTILINE)


def sanitize_tag(raw: str) -> str | None:
    """规范化 tag；非法字符一律拒绝（防路径穿越的第一道闸）。"""
    tag = raw.strip().lower()
    return tag if VALID_TAG_RE.match(tag) else None


def extract_image_tags(text: str) -> tuple[str, list[str]]:
    """取出 `{{img:tag}}` 标记，返回 (去掉标记的文本, 合法 tag 列表)。

    非法 tag 同样会被移除——宁可这张图不发，也不要让标记漏到用户眼前。
    """
    tags: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        tag = sanitize_tag(match.group(1))
        if tag and tag not in tags:
            tags.append(tag)
        return ""

    cleaned = IMAGE_TAG_RE.sub(_replace, text)
    return cleaned.strip(), tags


def strip_markdown(text: str) -> str:
    """把 Markdown 语法的痕迹去掉，留下适合 QQ 显示的纯文本。"""
    out = _CODE_FENCE_RE.sub("", text)
    out = _IMAGE_MD_RE.sub(r"\1", out)
    out = _LINK_MD_RE.sub(r"\1", out)
    out = _INLINE_CODE_RE.sub(r"\1", out)
    out = _BOLD_RE.sub(r"\2", out)
    out = _ITALIC_RE.sub(r"\1", out)
    out = _HEADING_RE.sub("", out)
    out = _QUOTE_RE.sub("", out)
    out = _LIST_RE.sub("", out)
    # 收敛多余空行与行尾空白
    out = "\n".join(line.rstrip() for line in out.splitlines())
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


_DRAW_RE = re.compile(r"\{\{\s*draw\s*:\s*([^{}]{1,200})\}\}", re.IGNORECASE)


def extract_draw_tags(text: str) -> tuple[str, list[str]]:
    """取出 `{{draw:画什么}}`，返回 (清理后的文本, 提示词列表)。

    这是"她自己画一张"的入口：文字照常发，图稍后单独发过来。
    """
    prompts: list[str] = []

    def _take(match: "re.Match[str]") -> str:
        prompt = " ".join(match.group(1).split())
        if prompt:
            prompts.append(prompt)
        return ""

    cleaned = _DRAW_RE.sub(_take, text or "")
    return cleaned.strip(), prompts


def split_message(text: str, limit: int = 300) -> list[str]:
    """按长度切分，优先在句末标点断开。

    规则：
    - 不超过 limit 就整条返回
    - 否则在窗口后半段找最后一个句末标点断开；找不到才硬切
    - 丢弃空片段，避免发出空消息
    """
    text = (text or "").strip()
    if not text:
        return []
    if limit <= 0:
        limit = 300
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    rest = text
    # 断点至少要落在窗口的这个比例之后，避免切出过短的碎片
    floor = max(1, int(limit * 0.5))

    while rest:
        if len(rest) <= limit:
            tail = rest.strip()
            if tail:
                chunks.append(tail)
            break

        window = rest[:limit]
        cut = -1
        for i in range(len(window) - 1, floor - 1, -1):
            if window[i] in SENTENCE_END:
                cut = i + 1
                break
        if cut <= 0:
            cut = limit

        piece = rest[:cut].strip()
        if piece:
            chunks.append(piece)
        rest = rest[cut:].lstrip()

    return [c for c in chunks if c]


def split_bubbles(text: str, limit: int = 300, max_bubbles: int = 5) -> list[str]:
    """按"真人会发几条"切分：先按换行拆成一条条，再按长度兜底。

    QQ 私聊里，一段三行的回复应该发成三条消息（每条隔一两秒），
    而不是一条里带两个换行——后者看着像复制粘贴。
    """
    text = (text or "").strip()
    if not text:
        return []
    if limit <= 0:
        limit = 300
    if max_bubbles <= 0:
        max_bubbles = 5

    bubbles: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) <= limit:
            bubbles.append(line)
        else:
            bubbles.extend(split_message(line, limit))

    # 行数超过上限时并进上一条，宁可少发一条也不丢字
    while len(bubbles) > max_bubbles:
        tail = bubbles.pop()
        bubbles[-1] = bubbles[-1] + "\n" + tail
    return bubbles

def _cut_in_two(text: str, rng) -> tuple[str, str] | None:
    """在逗号附近切一刀，切点别太偏。"""
    middle = len(text) // 2
    candidates = [i for i, ch in enumerate(text) if ch in "，,、…" and 0.2 < i / len(text) < 0.8]
    if not candidates:
        # 没有逗号就不切：硬切会把"不知道"劈成"不知"+"道你家里…"，
        # 发出去像乱码。宁可一条说完。
        return None
    cut = min(candidates, key=lambda i: abs(i - middle))
    head = text[: cut + 1].rstrip("，,、")
    tail = text[cut + 1 :].lstrip("，,、")
    if not head or not tail:
        return None
    return head, tail


def reshape_bubbles(bubbles, rng, max_merge_chars: int = 180) -> list[str]:
    """偶尔改一下发送条数：真人不总是发同样多的消息。

    - 有时把几条并成一条长的（"一口气说完"）
    - 有时把一条切成两条（想到什么发什么）
    - 其余时候不动
    """
    items = [b for b in (bubbles or []) if b]
    if not items:
        return []
    roll = rng.random()
    if roll < 0.30 and len(items) > 1:
        merged = items[0]
        for item in items[1:]:
            # 上一条已经以标点收尾就别再加逗号，否则会出现"诶？，啥功能呀"
            merged += item if merged[-1] in "，。！？…、~～,?!." else "，" + item
        if len(merged) <= max_merge_chars:
            return [merged]
    if roll < 0.50:
        target = max(items, key=len)
        pieces = _cut_in_two(target, rng)
        if pieces:
            index = items.index(target)
            return items[:index] + list(pieces) + items[index + 1 :]
    return items


# 旧人设留下过"爸爸"这种称呼，日摘要/记忆本里带着它，回灌 prompt 后模型会照着叫。
# 注入前统一换掉——这比在 prompt 里解释"以前叫爸爸"有效得多。
_ADDRESS_MAP = {"爸爸": "你", "爹地": "你", "爸": "你"}


def fix_address(text: str) -> str:
    """把旧称呼改成情侣之间的说法。"""
    if not text:
        return text
    for old, new in _ADDRESS_MAP.items():
        text = text.replace(old, new)
    return text

