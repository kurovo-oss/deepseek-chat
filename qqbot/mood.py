"""情绪 / 驱力状态：让她有自己的"此刻"。

三个值，都在 0~1：

- `miss` 想念：他不来就慢慢涨，一说话就落回去
- `warmth` 亲近：聊得暖就涨，安静地慢慢退
- `tired` 疲惫：夜里涨、白天退，聊久了也涨

每个值都朝"目标"做指数逼近（半衰期），目标由时间和事件算出来。
所以**不需要心跳线程**：读的时候按 elapsed 补算就行，进程重启也不丢，
因为没有"背景运转"，只有"时间流逝"。

这套东西和记忆权重是同构的（数值 + 衰减 + 注入），所以复用同一套直觉。
"""

from __future__ import annotations

import time
from datetime import datetime

DRIVES = ("miss", "warmth", "tired")

# 半衰期（小时）：想念变得快，亲近和疲惫慢一些
HALF_LIFE = {"miss": 3.0, "warmth": 24.0, "tired": 3.0}

# 多久没说话，想念涨到顶
MISS_IDLE_FULL_HOURS = 12.0
NIGHT_HOURS = (22, 6)  # 22 点到次日 6 点算夜里

TITLE = "# 此刻的你"
TIRED_LIMIT = 0.7
MISS_LIMIT = 0.75


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def drift(value: float, target: float, half_life_hours: float, elapsed: float) -> float:
    """朝 target 走 elapsed 秒，走掉多少由半衰期决定。"""
    if elapsed <= 0:
        return _clamp01(value)
    if half_life_hours <= 0:
        return _clamp01(target)
    factor = 0.5 ** (elapsed / (half_life_hours * 3600))
    return _clamp01(target + (float(value) - float(target)) * factor)


def is_night(now: int) -> bool:
    start, end = NIGHT_HOURS
    hour = datetime.fromtimestamp(int(now)).hour
    return hour >= start or hour < end


def targets(now: int, last_talk: int | None) -> dict[str, float]:
    """此刻三个值各自想去哪。"""
    idle_hours = 0.0
    if last_talk is not None:
        idle_hours = max(0.0, (int(now) - int(last_talk)) / 3600)
    miss = _clamp01(0.15 + 0.85 * min(1.0, idle_hours / MISS_IDLE_FULL_HOURS))
    tired = 0.65 if is_night(now) else 0.2
    return {"miss": miss, "warmth": 0.35, "tired": tired}


def mood_line(values: dict[str, float]) -> str:
    """一句人话，给 /mood 和日志用。"""
    miss = values.get("miss", 0.0)
    warmth = values.get("warmth", 0.0)
    tired = values.get("tired", 0.0)

    parts = []
    if miss >= MISS_LIMIT:
        parts.append("很想他")
    elif miss >= 0.45:
        parts.append("有点想他")
    else:
        parts.append("刚聊过，不惦记")

    if warmth >= 0.6:
        parts.append("心里暖暖的")
    elif warmth <= 0.25:
        parts.append("有点安静")
    else:
        parts.append("心情平平稳稳")

    if tired >= TIRED_LIMIT:
        parts.append("有点累了")
    elif tired >= 0.45:
        parts.append("有点困")
    else:
        parts.append("精神还不错")

    return "、".join(parts)


class Mood:
    """把状态接到 Store 上：读时补算，事件时落库。"""

    def __init__(self, store, settings, clock=time.time) -> None:
        self.store = store
        self.settings = settings
        self._clock = clock

    @property
    def session(self) -> str:
        return str(self.settings.allow_user_id)

    def snapshot(self, now: int | None = None) -> dict[str, float]:
        """当前值：按时间补算，不写库（只在事件发生时才落库）。"""
        stamp = int(now if now is not None else self._clock())
        stored = self.store.load_state()
        want = targets(stamp, self.store.last_message_time(self.session, role="user"))

        values: dict[str, float] = {}
        for name in DRIVES:
            current, updated = stored.get(name, (want[name], stamp))
            elapsed = max(0, stamp - updated)
            values[name] = drift(current, want[name], HALF_LIFE[name], elapsed)
        return values

    def save(self, values: dict[str, float], now: int | None = None) -> None:
        stamp = int(now if now is not None else self._clock())
        self.store.save_state(
            {name: values[name] for name in DRIVES if name in values}, stamp
        )

    def note_talk(self, now: int | None = None) -> dict[str, float]:
        """他说话了：想念落地，亲近涨一点。"""
        stamp = int(now if now is not None else self._clock())
        values = self.snapshot(stamp)
        values["miss"] = min(values["miss"], 0.15)
        values["warmth"] = _clamp01(values["warmth"] + 0.08)
        self.save(values, stamp)
        return values

    def describe(self, now: int | None = None) -> str:
        """进 system prompt 的那一段；关闭或没有状态时返回空串。"""
        if not getattr(self.settings, "mood_enabled", True):
            return ""
        values = self.snapshot(now)
        head = (
            f"{TITLE}\n"
            f"想念 {values['miss']:.2f}、亲近 {values['warmth']:.2f}、"
            f"疲惫 {values['tired']:.2f} —— {mood_line(values)}"
        )
        hints = []
        if values["miss"] >= MISS_LIMIT:
            hints.append("惦记他，可以主动问一句，但别腻。")
        if values["tired"] >= TIRED_LIMIT:
            hints.append("今天累了，句子短一点，别硬撑热闹。")
        if values["warmth"] >= 0.6 and not hints:
            hints.append("心情好，可以稍微活泼一点。")
        return head if not hints else head + "\n" + "".join(hints)

    def line(self, now: int | None = None) -> str:
        values = self.snapshot(now)
        return (
            f"我现在：想念 {values['miss']:.2f}、亲近 {values['warmth']:.2f}、"
            f"疲惫 {values['tired']:.2f}\n{mood_line(values)}。"
        )
