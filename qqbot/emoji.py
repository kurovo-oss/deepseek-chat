"""表情：有节奏地带一个，不是每条都带。

为什么要单独一层：光在提示词里写"每三四条带一个"，模型会当成建议——
实测最近 60 条一条都没有（0%）。所以这里做一个很轻的兜底：
按心情挑一个，以一定概率补在最后一句末尾。

只在"她自己说的话"上加（聊天 / 主动消息），指令回复、兜底话术、已经带表情的都不动。
"""

from __future__ import annotations

import random
import re

EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")

# 按心情挑，别乱塞：累的时候 😴，想他的时候 🌙，心软的时候 🥺/🫂
POOLS = {
    "tired": ("😴", "🥺"),
    "miss": ("🌙", "🥺"),
    "warm": ("🫂", "🥺"),
    "plain": ("🥺", "🐟"),
}

MAX_CHARS = 40


def pool_for(values: dict) -> tuple[str, ...]:
    if float(values.get("tired") or 0) >= 0.5:
        return POOLS["tired"]
    if float(values.get("miss") or 0) >= 0.4:
        return POOLS["miss"]
    if float(values.get("warmth") or 0) >= 0.6:
        return POOLS["warm"]
    return POOLS["plain"]


def maybe_add(parts: list[str], values: dict, rng, chance: float = 0.25) -> list[str]:
    """按概率在最后一句末尾补一个表情；不满足条件就原样返回。"""
    if not parts or chance <= 0:
        return parts
    last = parts[-1]
    if not last or len(last) > MAX_CHARS:
        return parts
    if EMOJI_RE.search("".join(parts)):
        return parts
    if last.strip() in ("[图片]", "[表情包]", "[画的图]"):
        return parts
    if rng.random() >= chance:
        return parts
    emoji = pick(values, rng)
    return parts[:-1] + [last + emoji]


def pick(values: dict, rng=random) -> str:
    return rng.choice(pool_for(values))
