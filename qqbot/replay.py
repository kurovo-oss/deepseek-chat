"""发送失败重放（M5）。

规格：回复已入库但发送失败 → `send_state='failed'`，重连后重放（**仅 10 分钟内**）。
主动消息不在此列——那条明说不重试，隔几小时再冒出来是骚扰，不是补发。

为什么必须有上限：

- **时间窗**：半小时后补一句"嗯嗯"不叫补发，叫莫名其妙；
- **次数**：协议端整晚挂着，每隔 60 秒重来一次直到天亮，日志和 QQ 都受不了。

重放不新建消息行——原文早就在库里，成功了只改状态，所以"不丢不重"。
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("qqbot")


class ReplyReplayer:
    def __init__(self, engine, store, settings, clock=time.time) -> None:
        self.engine = engine
        self.store = store
        self.settings = settings
        self._clock = clock

    def tick(self, now: int | None = None) -> int:
        """补发一轮，返回本次成功的条数。"""
        stamp = int(now if now is not None else self._clock())
        since = stamp - int(self.settings.replay_window_minutes * 60)
        replayed = 0

        for row in self.store.list_failed_replies(since):
            attempts = int(row.get("attempts") or 0)
            brief = str(row["content"])[:40]
            if attempts >= self.settings.replay_max_attempts:
                self.store.update_send_state(row["id"], "dead")
                log.warning("放弃重放（已试 %d 次）：%s", attempts, brief)
                continue

            if self.engine.resend(int(row["session_id"]), row["content"]):
                self.store.update_send_state(row["id"], "sent")
                replayed += 1
                log.info("补发成功：%s", brief)
            else:
                tries = self.store.bump_reply_attempt(row["id"])
                log.warning("补发失败（第 %d 次）：%s", tries, brief)

        return replayed

    def run(self, stop_event: threading.Event) -> None:
        interval = self.settings.replay_interval_seconds
        while not stop_event.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - 重放线程不能死
                log.exception("重放线程异常")
            if stop_event.wait(interval):
                break


def start_replayer(engine, store, settings, stop_event: threading.Event) -> threading.Thread:
    replayer = ReplyReplayer(engine, store, settings)
    thread = threading.Thread(
        target=replayer.run, args=(stop_event,), name="reply-replayer", daemon=True
    )
    thread.start()
    return thread
