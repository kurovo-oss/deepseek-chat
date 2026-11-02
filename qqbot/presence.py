"""她此刻在干嘛：真人不会每条消息都秒回。

状态存在 `data/presence.json`，一次管 20–50 分钟，不会每条消息都变。
这里只回答一个问题："她现在忙不忙、在干嘛"，不决定别的。
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

# (在干嘛, 权重)
ACTIVITIES: tuple[tuple[str, float], ...] = (
    # "在画画"被撤掉了：她不能只剩这一个标签，权重分给别的事
    ("在打游戏", 0.16),
    ("在看剧", 0.16),
    ("在刷抖音", 0.13),
    ("在收拾屋子", 0.13),
    ("在学做菜", 0.12),
    ("在改简历", 0.09),
    ("在拼拼图", 0.09),
    ("在跟朋友打电话", 0.07),
    ("在吃零食", 0.05),
)


def busy_chance(hour: int) -> float:
    """几点更容易没空理人：半夜最高，白天其次。"""
    if hour < 7:
        return 0.9
    if 8 <= hour < 18:
        return 0.45
    return 0.5


def pick_activity(roll: float, hour: int) -> str:
    if hour < 7:
        return "在睡觉"
    total = 0.0
    for name, weight in ACTIVITIES:
        total += weight
        if roll <= total:
            return name
    return ACTIVITIES[-1][0]


def _fresh(now: float, rng: random.Random) -> dict:
    hour = time.localtime(now).tm_hour
    return {
        "activity": pick_activity(rng.random(), hour),
        "busy": rng.random() < busy_chance(hour),
        "until": now + rng.uniform(1200, 3000),
    }


def read(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def current(path: Path, now: float, rng: random.Random | None = None) -> dict:
    """读当前状态；过期或没有就重新生成一份并落盘。"""
    state = read(path)
    if state.get("until", 0) > now and state.get("activity"):
        return state
    state = _fresh(now, rng or random.Random())
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return state


def describe(state: dict) -> str:
    return str(state.get("activity") or "在忙")
