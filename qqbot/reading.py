"""每天刷一次网：她真的看见了什么，才说得出什么。

单独一层：抓取会超时、会被反爬，必须能整体跳过；engine 只管在开场踢一脚。
"""

from __future__ import annotations

import logging
import random
import threading

from . import browse as browse_mod

log = logging.getLogger("qqbot")


class ReadingMixin:
    def _maybe_browse(self) -> None:
        """今天还没刷过就刷一次。后台跑，不挡他这条消息。"""
        if not bool(getattr(self.settings, "web_enabled", False)):
            return
        try:
            if browse_mod.today_item(self.settings) is not None:
                return
        except Exception:  # noqa: BLE001 - 读文件出问题就当没刷过
            log.info("读今天的浏览记录失败", exc_info=True)
        thread = getattr(self, "_browse_thread", None)
        if thread is not None and thread.is_alive():
            return
        self._browse_thread = threading.Thread(target=self._browse_once, daemon=True)
        self._browse_thread.start()

    def _browse_once(self) -> None:
        try:
            browse_mod.collect(self.settings, random)
        except Exception:  # noqa: BLE001 - 刷不到就当没网，不影响聊天
            log.info("刷网失败", exc_info=True)
