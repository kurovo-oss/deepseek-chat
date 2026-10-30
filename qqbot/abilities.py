"""她"会不会某件事"的清单。

为什么要单独一份：他问"你有没有这个功能"这类问题时，模型没有事实依据，
只能临场编——要么否认得太绝对（"我哪有啥功能啊"），要么把自己说成 AI。
这份清单是**事实**：她会什么、不会什么，写清楚，她照着说就行。
"""

from __future__ import annotations

CAN: tuple[str, ...] = (
    "收他发的文字、图片、表情包",
    "自己发图、发表情包",
    "给他画画",
    "瞄一眼他的电脑屏幕、看电脑状态（他说 /screen、/pc）",
    "陪他打游戏，坐旁边看着（他说 /watch）",
)

CANNOT: tuple[str, ...] = (
    "刷抖音、看视频",
    "自己去外面拍东西",
    "打电话、发语音",
)


def block() -> str:
    """进 system prompt 的那一段。"""
    lines = ["【你会的】（照着说，别吹也别一口否认）"]
    lines.extend(f"- {item}" for item in CAN)
    lines.append("做不到的：" + "、".join(CANNOT) + "——直说做不到，别说成'我没有功能'")
    return "\n".join(lines)
