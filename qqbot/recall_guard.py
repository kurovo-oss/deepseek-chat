"""她说"你之前说过…"的时候，得真有个出处。

线上抓到过：他问"你怎么知道"，她顺手编一句"你之前提过要出差呀，我记着呢"，
被追问才改口"可能是我记错了"。

编一次记忆，他以后就得怀疑她记的所有事——这比说"我记不清了"伤人得多。
所以拿这一轮真正喂进去的上下文（卡片、前情、他的原话）对一遍：
提到的具体内容对不上，就把那句软化成"我记不太清了"。
"""

from __future__ import annotations

import re

# 只认"回忆他"的说法；"我跟你说过"是她自己的事，也算
RECALL_RE = re.compile(
    r"(?:你|他)(?:之前|以前|上次|昨天|前天|上回)?(?:说过|提过|讲过|问过)"
    r"|(?:你|他)不是说过"
    r"|你还记得"
    r"|我跟你说过"
)

# 认错要认得像她，不能像系统提示
SOFT_LINES: tuple[str, ...] = (
    "诶，我记不太清了，你再说一遍嘛",
    "我可能记混了",
    "这个我真想不起来了",
)

# 虚词不参与比对：只留实词双字，避免"说过吗"这种碎片撞上
_SKIP = set(
    "的了呀嘛哦呢啊吧我你他她它们是在有和就都也很不没要会能把被给对从到与及或而但却还又再才只更最太挺比像跟向往让使吗这那个时说什么之前过"
)


def _grams(text: str) -> set[str]:
    chars = [c for c in text if "\u4e00" <= c <= "\u9fff" and c not in _SKIP]
    return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}


def backed(claimed: str, evidence: str) -> bool:
    """这句话里的具体内容，上下文里提过吗？"""
    claim = _grams(claimed)
    if not claim:
        return True  # 没说出具体东西，就没什么可编的
    return bool(claim & _grams(evidence))


def soften(text: str, evidence: str) -> str:
    """把没有出处的"你说过…"换成她认账的说法。"""
    if not text or not RECALL_RE.search(text):
        return text
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        match = RECALL_RE.search(line)
        if not match or backed(line[match.end() :], evidence):
            out.append(line)
            continue
        soft = SOFT_LINES[len(out) % len(SOFT_LINES)]
        # 他说过的话被"记错"时，她自己先退一步，不硬撑
        out.append(soft)
    return "\n".join(out)
