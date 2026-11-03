"""她到底"真能做什么"：一张登记表，管住嘴。

为什么要它：模型很爱编"我刚刷到个视频""我给你剪了个视频"——
她明明没有这个能力，说多了，他说什么都像在跟一个假人聊。

规矩只有一条：**只有系统真替她做过的事，她才能说做过。**
- 现在系统能做的：发图、发表情包、画画、看他的屏幕、记事情
- 现在做不到的：刷抖音、上网、自己拍/剪视频、拍照、发朋友圈、发语音

`刷抖音` 和 `自主上网` 后面会真做（拿到真实内容再让她说）。到时候把对应开关
（`DOUYIN_ENABLED` / `WEB_ENABLED`）打开，这张表会自动把她能说的事挪过去，
下面的拦截规则也跟着失效——不用再改 prompt。

拦截是句子级的：她编的那一句删掉，其余保留；全删光了就换成一句老实话。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    key: str
    flag: str  # settings 上的开关名；打开＝系统真能做
    label: str  # 写进提示词的说法
    claims: tuple[str, ...]  # 她"编自己做过"时可能的说法
    honest: str  # 被拦下时换成的话


# 顺序就是提示词里的顺序
CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        key="douyin",
        flag="douyin_enabled",
        label="刷抖音、看短视频",
        claims=(
            r"(刷|翻)(到|了)[^。！？!?\n]{0,12}(视频|短视频|抖音)",
            r"抖音上[^。！？\n]{0,6}(看到|刷到|说)",
            r"刷了[^。！？\n]{0,4}抖音",
        ),
        honest="我这边刷不了抖音诶",
    ),
    Capability(
        key="web",
        flag="web_enabled",
        label="自己上网查东西",
        claims=(
            r"网上[^。！？\n]{0,8}(看到|查到|搜到|找的)",
            r"我(刚|刚才|今天)?(上网|搜了|查了)一下",
            r"(百度|微博|知乎|小红书)上[^。！？\n]{0,6}(看到|说|查|搜)",
        ),
        honest="我上不了网诶",
    ),
    Capability(
        key="video",
        flag="video_enabled",
        label="自己拍、自己剪视频",
        claims=(
            r"(剪|拍)(了|了个|好)[^。！？\n]{0,8}(视频|片子)",
            r"(我|刚)(给|帮)你(剪|拍)了?[^。！？\n]{0,6}视频",
            r"视频我(剪|做)好了",
        ),
        honest="我还不会剪视频",
    ),
    Capability(
        key="moments",
        flag="moments_enabled",
        label="发朋友圈",
        claims=(r"我(刚)?发(了)?(个|一条)?(朋友圈|说说|动态)",),
        honest="我不发朋友圈的",
    ),
    Capability(
        key="camera",
        flag="camera_enabled",
        label="拍照",
        claims=(
            r"我(刚|刚才|今天|昨天)?拍了[^。！？\n]{0,6}(照片|照)",
            r"(给|帮)你拍[^。！？\n]{0,4}照",
            r"我给你拍[^。！？\n]{0,6}(画|照片)",
        ),
        honest="我没法拍照呀",
    ),
)

# 一直做不到的（没有对应开关，也不打算做）
ALWAYS_NO: tuple[str, ...] = ("打电话、发语音",)


def enabled(cap: Capability, settings) -> bool:
    return bool(getattr(settings, cap.flag, False)) if settings is not None else False


def can(settings) -> tuple[str, ...]:
    return tuple(cap.label for cap in CAPABILITIES if enabled(cap, settings))


def cannot(settings) -> tuple[str, ...]:
    return tuple(cap.label for cap in CAPABILITIES if not enabled(cap, settings))


def block(settings=None) -> str:
    """进提示词的那一段：能做 / 做不到。"""
    lines = ["做不到的：" + "、".join(cannot(settings) + ALWAYS_NO)]
    lines.append("——直说做不到（\"我刷不了\"），别说成\"我没有功能\"，更别说成做过。")
    return "\n".join(lines)


def _sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[。！？!?…\n])", text or "") if part]


def claims_disabled(text: str, settings=None) -> list[Capability]:
    """这句话里有没有"编自己做过"的内容（只算系统现在做不到的）。"""
    hits: list[Capability] = []
    for cap in CAPABILITIES:
        if enabled(cap, settings):
            continue
        if any(re.search(pattern, text) for pattern in cap.claims):
            hits.append(cap)
    return hits


def soften(text: str, settings=None) -> str:
    """删掉她编的那句；全删光了就换成老实话。"""
    hits = claims_disabled(text, settings)
    if not hits:
        return text
    kept: list[str] = []
    for sentence in _sentences(text):
        if claims_disabled(sentence, settings):
            continue
        kept.append(sentence)
    out = "".join(kept).strip()
    if out:
        return out
    return hits[0].honest
