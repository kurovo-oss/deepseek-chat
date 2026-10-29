"""消息调度：收到上报立刻回 204，真正的处理排进单线程队列。

为什么必须这样（2026-10-29 代码审查的结论）：

- NapCat 的 `baseTimeout` 是 10 秒，超时就会重推同一条消息；
- 而一轮处理包含两次模型调用（对话 + 记忆抽取），模型失败还会重试 2 次、
  每次超时 60 秒——最坏能拖到几分钟。

原来是在请求线程里同步处理完才回 204，两者叠加就会出现
"NapCat 已经重推了，我们还在处理上一条"，于是同一条消息回两遍。

单线程队列还有个副作用是好的：同一会话天然严格按到达顺序处理，
不会两条并发、回复乱序、重复建卡、连发两张图。
"""

from __future__ import annotations

import logging
import queue
import threading

log = logging.getLogger("qqbot")


class MessageDispatcher:
    def __init__(self, engine, maxsize: int = 100) -> None:
        self.engine = engine
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def submit(self, payload) -> bool:
        """入队。队列满时宁可丢掉也不阻塞上报线程——回 204 才有意义。"""
        try:
            self._queue.put_nowait(payload)
            return True
        except queue.Full:
            log.error("处理队列已满（%d），丢弃一条上报", self._queue.maxsize)
            return False

    def drain_one(self, timeout: float = 0.5) -> bool:
        """取一条并处理。返回 False 表示这段时间没有新消息。"""
        try:
            payload = self._queue.get(timeout=timeout)
        except queue.Empty:
            return False
        try:
            self.engine.handle_payload(payload)
        except Exception:  # noqa: BLE001 - 单条失败不能让队列停摆
            log.exception("处理事件失败")
        finally:
            self._queue.task_done()
        return True

    def run(self, stop_event: threading.Event, poll: float = 0.5) -> None:
        while not stop_event.is_set():
            self.drain_one(poll)

    def start(self, stop_event: threading.Event, name: str = "dispatcher") -> threading.Thread:
        thread = threading.Thread(
            target=self.run, args=(stop_event,), name=name, daemon=True
        )
        thread.start()
        return thread
