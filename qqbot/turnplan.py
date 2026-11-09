"""这一轮怎么接：只给她三四条最重要的指令，不堆规则。

模型每轮看到的人设很长，真正决定"像不像人"的往往是当场的姿态：
他是求安慰还是在分享、这轮该不该问、上一句是不是已经问过了。
这里用确定性规则算出来，不额外调模型，也不花钱。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import talkstate

NEGATIVE = (
    "累", "难过", "烦", "气", "哭", "压力", "加班", "失眠", "被骂", "分手",
    "焦虑", "难受", "不开心", "委屈", "疼", "病", "崩溃", "烦死", "不想活",
)
CONFLICT = ("滚", "烦不烦", "闭嘴", "别理我", "讨厌你", "生你气", "不想理你")
PLAYFUL = ("哈哈", "嘿嘿", "逗", "开玩笑", "笑死", "好玩", "乐死")
QUESTION = re.compile(r"[?？]|吗|嘛|呢|什么|啥|怎么|哪|谁|多少|几|干嘛")
SHARE = re.compile(r"我(今天|刚|昨天|刚才|现在)|今天|刚刚|下班|上班|吃了|去了|买了|看了")
REFERENCE = re.compile(r"这个|那个|它|该功能|这功能")


@dataclass(frozen=True)
class Plan:
    intent: str
    stance: str
    avoid: tuple[str, ...]
    bubbles: int
    opening_avoid: str = ""
    reference: str = ""

    def render(self) -> str:
        lines = [
            "【这一轮】",
            f"他在：{self.intent}。",
            f"你想：{self.stance}。",
            "先接他这句话里的一个具体点（人/事/时间/情绪），不要复述。",
            "别：" + "、".join(self.avoid) + "。",
        ]
        if self.reference:
            lines.append(
                f"他上一句说的是「{self.reference}」；这轮的“这个/那个”先接住它，"
                "用他原话说过的名字（他说抖音就说抖音，别换成“短视频”），再答能不能。"
            )
        if self.opening_avoid:
            lines.append(f"这轮别用「{self.opening_avoid}」开头。")
        if self.bubbles == 1:
            lines.append("这轮一条就够，说完停住。")
        return "\n".join(lines)


def _recent_assistant(history: list[dict], limit: int = 8) -> list[str]:
    return [
        str(item.get("content") or "")
        for item in history[-limit:]
        if item.get("role") != "user"
    ]


def _opening_avoid(replies: list[str]) -> str:
    """最近几条开头太像时，点名让她换一个。"""
    openings = [text.strip()[:2] for text in replies if text.strip()]
    if len(openings) < 3:
        return ""
    top = max(set(openings), key=openings.count)
    if openings.count(top) >= 3:
        return top
    return ""


def _previous_user_text(history: list[dict], current: str) -> str:
    """找到他上一句；当前这句通常已经在 history 末尾。"""
    passed_current = False
    for item in reversed(history):
        if item.get("role") != "user":
            continue
        text = str(item.get("content") or "").strip()
        if not text:
            continue
        if not passed_current and text == current.strip():
            passed_current = True
            continue
        return text
    return ""


def make_plan(
    user_text: str,
    history: list[dict] | None = None,
    mood_values: dict | None = None,
) -> Plan:
    text = (user_text or "").strip()
    history = history or []
    values = mood_values or {}

    if any(word in text for word in CONFLICT):
        intent, stance = "在生气", "先安静，别顶回去"
    elif any(word in text for word in NEGATIVE):
        intent, stance = "在难受", "先接住，别讲道理"
    elif any(word in text for word in PLAYFUL):
        intent, stance = "在逗你", "松一点，可以回嘴，但别刻薄"
    elif QUESTION.search(text):
        intent, stance = "在问事", "先答他那件事，别绕"
    elif SHARE.search(text):
        intent, stance = "在分享", "接住细节，说一句自己的"
    else:
        intent, stance = "闲聊", "平常说，不用每句都接得很满"

    if float(values.get("tired") or 0) >= 0.7:
        stance = "你有点累，话短一点，别硬撑"
    elif float(values.get("miss") or 0) >= 0.75:
        stance = "你有点想他，可以软一点，但别黏住不放"

    replies = _recent_assistant(history)
    asked = sum(1 for reply in replies[-6:] if talkstate.asks_question(reply))
    avoid = ["总结", "复述", "讲道理"]
    if asked >= 2:
        avoid.append("反问")
    if intent == "在难受":
        avoid.extend(["追问细节", "给方案"])
    if intent == "在生气":
        avoid.extend(["解释", "顶嘴"])
    if not avoid:
        avoid.append("每句都问")

    bubbles = 2
    if intent == "在难受" or intent == "在生气":
        bubbles = 1
    elif intent == "在分享":
        bubbles = 3

    reference = ""
    if REFERENCE.search(text):
        previous = _previous_user_text(history, text)
        if previous:
            reference = previous[:24]

    return Plan(
        intent=intent,
        stance=stance,
        avoid=tuple(dict.fromkeys(avoid)),
        bubbles=bubbles,
        opening_avoid=_opening_avoid(replies),
        reference=reference,
    )
