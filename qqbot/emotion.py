"""从他的这句话里读出情绪。

两条约束决定这里是**词表**而不是再调一次模型：

1. 必须赶在回复之前——要影响这条回复的语气，就不能等回答完再说；
2. 判定要可解释、可测——"她为什么突然这么温柔"应该能追溯到具体几个字。

代价是读不出反讽和长句里拐弯的情绪。所以它只是**微调**：
识别错了顶多语气偏一点，不会让记忆或结论跟着错。
"""

from __future__ import annotations

import re

# 情绪名 -> 关键词。顺序即优先级：负面在前，别让"还行吧"盖过"难受"
KEYWORDS: dict[str, tuple[str, ...]] = {
    "sad": ("难过", "伤心", "想哭", "哭了", "掉眼泪", "委屈", "失落", "低落",
            "心里堵", "不好受", "崩溃", "难受", "郁闷", "烦心"),
    "anxious": ("焦虑", "紧张", "压力", "好怕", "害怕", "担心", "慌", "心里没底",
                "睡不着", "失眠", "面试", "截止", "来不及"),
    "angry": ("气死", "烦死", "火大", "生气", "可恶", "无语", "受不了", "想骂人"),
    "lonely": ("孤单", "寂寞", "一个人", "没人懂", "没人说话", "好空"),
    "tired": ("好累", "太累", "累死", "疲惫", "好困", "熬夜", "加班", "撑不住",
              "没力气", "没睡好"),
    "happy": ("开心", "高兴", "太好了", "好棒", "舒服", "快乐", "哈哈", "爽",
              "顺利", "拿到了", "成了", "搞定了"),
}

# 否定式先匹配：这些整体算负面，不能因为含"开心/舒服"就被判成高兴
NEGATED: dict[str, tuple[str, ...]] = {
    "sad": ("不开心", "不高兴", "不舒服", "没意思", "没劲", "不想说话", "没心情"),
    "tired": ("没睡好", "不想动", "提不起劲"),
}

# 每个情绪对状态的推力：(亲近, 疲惫, 想念)
EFFECTS: dict[str, tuple[float, float, float]] = {
    "sad": (0.12, 0.05, 0.02),
    "anxious": (0.10, 0.03, 0.05),
    "angry": (0.04, 0.06, 0.00),
    "lonely": (0.12, 0.00, 0.10),
    "tired": (0.06, 0.10, 0.00),
    "happy": (0.15, -0.08, -0.05),
}

# 给他看的一句话（进 system prompt，只说这一句，不指导太多）
HINTS: dict[str, str] = {
    "sad": "他这条消息听着不太好受——先陪着，别讲道理、别逗笑、别说「想开点」。",
    "anxious": "他有点慌。说点踏实的，短句，别一起放大焦虑。",
    "angry": "他在气头上。别顶嘴、别急着解释，先让他说完。",
    "lonely": "他有点空。可以直接说一句「我在」。",
    "tired": "他累了。话短一点，别问需要动脑子的问题。",
    "happy": "他心情好。顺着这股高兴聊，别泼冷水。",
}

TONE_LABEL: dict[str, str] = {
    "sad": "难过",
    "anxious": "不安",
    "angry": "生气",
    "lonely": "孤单",
    "tired": "累",
    "happy": "开心",
}

_NEGATION = "不没别"


def _negated(text: str, index: int) -> bool:
    prefix = text[max(0, index - 2) : index]
    return any(ch in prefix for ch in _NEGATION)


def detect(text: str) -> str | None:
    """返回情绪名，读不出来返回 None。"""
    content = (text or "").strip()
    if not content or content.startswith("/"):
        return None

    for tone, words in NEGATED.items():
        if any(word in content for word in words):
            return tone

    for tone, words in KEYWORDS.items():
        for word in words:
            start = content.find(word)
            while start != -1:
                if not _negated(content, start):
                    return tone
                start = content.find(word, start + 1)
    return None


def hint(tone: str | None) -> str:
    return HINTS.get(tone or "", "")


def effect(tone: str | None) -> tuple[float, float, float]:
    return EFFECTS.get(tone or "", (0.0, 0.0, 0.0))
