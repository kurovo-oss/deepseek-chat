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

from . import emotion

DRIVES = ("miss", "warmth", "tired")

# 半衰期（小时）：想念变得快，亲近和疲惫慢一些
HALF_LIFE = {"miss": 3.0, "warmth": 24.0, "tired": 3.0}

# 多久没说话，想念涨到顶
MISS_IDLE_FULL_HOURS = 12.0
NIGHT_HOURS = (22, 6)  # 22 点到次日 6 点算夜里

TITLE = "# 此刻的你"

# 累的时候少说两句：这是"情绪影响表达"里唯一能拿代码保证的部分，
# 其余的交给提示句和样品——规定太多会把她写成一个只会短句的机器人。
TIRED_MAX_BUBBLES = 2
CALM_MAX_BUBBLES = 3

LOW_SAMPLES: tuple[tuple[str, str], ...] = (("在吗", "在呀\n…刚躺下，有点不想动"),)
MISS_SAMPLES: tuple[tuple[str, str], ...] = (
    ("今天忙不忙", "忙你的吧\n我就说说，你别管我"),
)
WARM_SAMPLES: tuple[tuple[str, str], ...] = (("今天运气不错", "真的呀\n那我也跟着高兴"),)
TIRED_LIMIT = 0.7
MISS_LIMIT = 0.75
# 他这条消息的情绪，最多"新鲜"半小时：再久就不该左右她现在的语气
TONE_FRESH_SECONDS = 1800


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
    # 夜里目标必须高过 TIRED_LIMIT：值只会"逼近"目标，目标定 0.65 就永远
    # 到不了 0.7 的闸门，"累了不开口"会变成死代码（真机验证时发现的）。
    tired = 0.8 if is_night(now) else 0.2
    return {"miss": miss, "warmth": 0.35, "tired": tired}


def style(values: dict[str, float]) -> dict[str, object]:
    """此刻该怎么说：几条、能不能问、要不要换语气。

    只把"能拿代码保证"的部分留在这里（条数、问句），
    语气细节交给 describe() 的提示句和 style_samples() 的样品。
    """
    tired = float(values.get("tired", 0.0))
    miss = float(values.get("miss", 0.0))
    warmth = float(values.get("warmth", 0.0))
    if tired >= TIRED_LIMIT:
        return {"max_bubbles": TIRED_MAX_BUBBLES, "ask_ok": False, "label": "累"}
    if miss >= MISS_LIMIT:
        return {"max_bubbles": CALM_MAX_BUBBLES, "ask_ok": True, "label": "想他"}
    if warmth >= 0.6:
        return {"max_bubbles": CALM_MAX_BUBBLES, "ask_ok": True, "label": "暖"}
    return {"max_bubbles": CALM_MAX_BUBBLES, "ask_ok": True, "label": "平"}


def style_samples(values: dict[str, float]) -> tuple[tuple[str, str], ...]:
    """按此刻的情绪多给一组样品：学语气比听规矩快。"""
    if float(values.get("tired", 0.0)) >= TIRED_LIMIT:
        return LOW_SAMPLES
    if float(values.get("miss", 0.0)) >= MISS_LIMIT:
        return MISS_SAMPLES
    if float(values.get("warmth", 0.0)) >= 0.6:
        return WARM_SAMPLES
    return ()


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
        # 他上一条消息读出来的情绪：只活在内存里，重启就忘（本来就该忘）
        self.tone: str | None = None
        self._tone_at: int = 0

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

    def note_message(self, text: str, now: int | None = None) -> dict[str, float]:
        """他这句带着情绪：先做 note_talk，再按读出来的语气推一把。"""
        stamp = int(now if now is not None else self._clock())
        values = self.note_talk(stamp)

        tone = emotion.detect(text)
        if tone is None:
            return values

        warmth, tired, miss = emotion.effect(tone)
        values["warmth"] = _clamp01(values["warmth"] + warmth)
        values["tired"] = _clamp01(values["tired"] + tired)
        values["miss"] = _clamp01(values["miss"] + miss)
        self.save(values, stamp)
        self.tone = tone
        self._tone_at = stamp
        return values

    def current_tone(self, now: int | None = None) -> str | None:
        """还在"新鲜期"内的情绪，过半小时就不算了。"""
        stamp = int(now if now is not None else self._clock())
        if not self.tone:
            return None
        if stamp - self._tone_at > TONE_FRESH_SECONDS:
            return None
        return self.tone

    def describe(self, now: int | None = None) -> str:
        """进 system prompt 的那一段；关闭或没有状态时返回空串。"""
        if not getattr(self.settings, "mood_enabled", True):
            return ""
        stamp = int(now if now is not None else self._clock())
        values = self.snapshot(stamp)
        # 数字对她没用：0.42 和 0.48 读起来一样。换成三档人话 + 一句具体怎么调
        head = f"{TITLE}\n{mood_line(values)}。"
        hints = []
        tone_hint = emotion.hint(self.current_tone(stamp))
        if tone_hint:
            hints.append(tone_hint)  # 他这条的情绪最优先，别的都让位
        else:
            if values["tired"] >= TIRED_LIMIT:
                hints.append(
                    "累了。句子比平时更短，一两条就够，也别问他问题；"
                    "这时候你不想照顾谁，说说你自己就行。"
                )
            elif values["miss"] >= MISS_LIMIT:
                hints.append("想他想得厉害。可以自己先开口，直接说一句想他，别绕。")
            elif values["warmth"] >= 0.6:
                hints.append("心情好，可以稍微闹一点，多用点语气词。")
        return head if not hints else head + "\n" + "".join(hints)

    def line(self, now: int | None = None) -> str:
        stamp = int(now if now is not None else self._clock())
        values = self.snapshot(stamp)
        text = (
            f"我现在：想念 {values['miss']:.2f}、亲近 {values['warmth']:.2f}、"
            f"疲惫 {values['tired']:.2f}\n{mood_line(values)}。"
        )
        tone = self.current_tone(stamp)
        if tone:
            text += f"\n（你刚才听起来有点{emotion.TONE_LABEL.get(tone, tone)}）"
        return text
