"""“今天别找我了”：把静默状态落到 data/proactive_mute.json。

只按自然日生效，跨到第二天自动失效；不碰 .env，也不改 PROACTIVE_ENABLED。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

MARKER_NAME = "proactive_mute.json"


def _path(settings) -> Path:
    return Path(settings.base_dir) / "data" / MARKER_NAME


def _day(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now if now is not None else time.time()))


def until_next_midnight(now: float | None = None) -> int:
    stamp = now if now is not None else time.time()
    today = datetime.fromtimestamp(stamp)
    tomorrow = (today + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(tomorrow.timestamp())


def is_muted(settings, now: float | None = None) -> bool:
    try:
        data = json.loads(_path(settings).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return str(data.get("date") or "") == _day(now)


def set_today(settings, now: float | None = None) -> dict:
    path = _path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"date": _day(now), "until": until_next_midnight(now)}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return {"ok": True, **payload}


def clear(settings) -> dict:
    try:
        _path(settings).unlink()
    except FileNotFoundError:
        pass
    return {"ok": True}


def restore(settings, runtime, now: float | None = None) -> bool:
    """启动时把今天的静默状态装回 Runtime；过期标记顺手清掉。"""
    if not is_muted(settings, now):
        clear(settings)
        return False
    runtime.set_proactive_mute(until_next_midnight(now))
    return True
