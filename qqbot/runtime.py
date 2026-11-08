"""控制台的运行时开关：不重启进程也能停掉某一类对外动作。

只活在内存里：重启后恢复成「全部运行」，和 .env 里的启用状态无关。
"""

from __future__ import annotations

import threading
import time


MODULES = ("proactive", "qzone", "douyin")


class Runtime:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._paused: set[str] = set()
        self._emergency = False
        self._proactive_mute_until = 0

    def paused(self, module: str) -> bool:
        with self._lock:
            return self._emergency or module in self._paused

    def enabled(self, module: str) -> bool:
        if module == "proactive" and self._proactive_muted():
            return False
        return not self.paused(module)

    def _proactive_muted(self, now: float | None = None) -> bool:
        with self._lock:
            return int(self._proactive_mute_until) > int(now if now is not None else time.time())

    def set_proactive_mute(self, until: int) -> None:
        with self._lock:
            self._proactive_mute_until = max(0, int(until))

    def proactive_muted(self, now: float | None = None) -> bool:
        return self._proactive_muted(now)

    def proactive_mute_until(self) -> int:
        with self._lock:
            return int(self._proactive_mute_until)

    def set_paused(self, module: str, paused: bool) -> bool:
        if module not in MODULES:
            raise ValueError(f"未知模块：{module}")
        with self._lock:
            if paused:
                self._paused.add(module)
            else:
                self._paused.discard(module)
            return self._emergency or module in self._paused

    def set_emergency(self, on: bool) -> None:
        with self._lock:
            self._emergency = bool(on)

    @property
    def emergency(self) -> bool:
        with self._lock:
            return self._emergency

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "emergency": self._emergency,
                "paused": {name: self._emergency or name in self._paused for name in MODULES},
                "proactive_muted": int(self._proactive_mute_until) > int(time.time()),
                "proactive_muted_until": int(self._proactive_mute_until),
            }

    def resume_all(self) -> None:
        with self._lock:
            self._paused.clear()
            self._emergency = False
