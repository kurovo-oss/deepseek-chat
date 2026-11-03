"""她自己的那些事实：跑在什么模型上、这次跑了多久、用了多少 token。

模型对"自己"的了解来自训练数据，不来自这台机器——你问"你用了多少 token"，
它只能编一个。所以把真实数字直接写进上下文，问起来就有话说。

数字都是**这台机器上的真账**（usage_log / messages / cards），不是估算。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from . import growth, stickers

log = logging.getLogger("qqbot")

_BOOT = time.time()
_CACHE_TTL = 180.0
_cache: dict[str, tuple[float, str]] = {}

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _num(value: int) -> str:
    value = int(value or 0)
    if value >= 100000:
        return f"{value / 10000:.0f} 万"
    if value >= 10000:
        return f"{value / 10000:.1f} 万"
    return str(value)


def _day_start(now: int) -> int:
    local = time.localtime(now)
    return int(time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)))


def _uptime(now: int) -> str:
    seconds = max(0, now - int(_BOOT))
    if seconds < 3600:
        return f"{seconds // 60} 分钟"
    hours, rest = divmod(seconds, 3600)
    return f"{hours} 小时" + (f" {rest // 60} 分" if rest >= 600 else "")


def _count_stickers(settings) -> int:
    try:
        return len(stickers.load_index(settings).get("items") or [])
    except Exception:  # noqa: BLE001 - 数不出来就不写这一句
        return 0


def _count_voices(settings) -> int:
    folder = getattr(settings, "voice_dir", None)
    if folder is None:
        return 0
    try:
        return sum(1 for _ in folder.glob("*.mp3"))
    except OSError:
        return 0


def facts(store, settings, now: int | None = None) -> dict:
    stamp = int(now if now is not None else time.time())
    start = _day_start(stamp)
    today_messages = store.count_messages_between(start, stamp + 1)
    today_in, today_out = store.usage_between(start, stamp + 1)
    total_in, total_out = store.usage_total()
    cards = store.count_cards()
    events = growth.count(store)
    return {
        "model": getattr(settings, "model_id", ""),
        "today_messages": today_messages,
        "today_in": today_in,
        "today_out": today_out,
        "total_in": total_in,
        "total_out": total_out,
        "cards": cards,
        "events": events,
        "stickers": _count_stickers(settings),
        "voices": _count_voices(settings),
        "uptime": _uptime(stamp),
        "now": datetime.fromtimestamp(stamp),
        "weekday": WEEKDAYS[datetime.fromtimestamp(stamp).weekday()],
    }


def block(store, settings, now: int | None = None, budget: int = 260) -> str:
    """进上下文的那一小段：问到自己时不至于瞎编。"""
    if store is None:
        return ""
    stamp = int(now if now is not None else time.time())
    key = f"{id(store)}:{stamp // 120}"
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    try:
        data = facts(store, settings, stamp)
    except Exception:  # noqa: BLE001 - 问自己也不该拖垮对话
        log.exception("读自己的数据失败")
        return ""

    lines = ["【关于你自己（系统报的真实数字）】"]
    lines.append(
        f"模型 {data['model']}；这次启动到现在 {data['uptime']}。"
    )
    lines.append(
        f"今天聊了 {data['today_messages']} 条，用掉 {_num(data['today_in'] + data['today_out'])} token"
        f"（进 {_num(data['today_in'])} / 出 {_num(data['today_out'])}）；"
        f"从开始到现在一共 {_num(data['total_in'] + data['total_out'])}。"
    )
    bits = [f"记得 {data['cards']} 件事", f"事件流 {data['events']} 条"]
    if data["stickers"]:
        bits.append(f"表情库 {data['stickers']} 张")
    if data["voices"]:
        bits.append(f"语音发过 {data['voices']} 条")
    lines.append("；".join(bits) + "。")
    lines.append(f"现在 {data['now']:%m-%d} {data['weekday']} {data['now']:%H:%M}。")
    lines.append("问到这些就照实说；没列到的（花了多少钱、还剩多少额度）就说算不出来。")
    text = "\n".join(lines)[:budget]
    _cache[key] = (time.time(), text)
    return text
