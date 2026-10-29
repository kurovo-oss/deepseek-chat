"""消息调度：上报即应答，处理在单线程队列里串行。

背景：原来是在请求线程里同步处理完才回 204，而一轮处理有两次模型调用，
NapCat 的 baseTimeout 只有 10 秒——超时就重推，同一条消息会回两遍。
"""

import threading
import time

from qqbot.dispatcher import MessageDispatcher


class FakeEngine:
    def __init__(self, delay=0.0):
        self.payloads = []
        self.delay = delay
        self.error = None

    def handle_payload(self, payload):
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        self.payloads.append(payload)


def test_submit_then_drain_processes():
    engine = FakeEngine()
    dispatcher = MessageDispatcher(engine)

    assert dispatcher.submit({"n": 1}) is True
    assert dispatcher.drain_one(timeout=0.1) is True
    assert engine.payloads == [{"n": 1}]
    assert dispatcher.pending == 0


def test_drain_returns_false_when_nothing_queued():
    dispatcher = MessageDispatcher(FakeEngine())
    assert dispatcher.drain_one(timeout=0.01) is False


def test_submit_never_blocks_on_processing():
    """上报线程只入队——这是"不被 10 秒超时判死"的关键。"""
    engine = FakeEngine(delay=0.5)
    dispatcher = MessageDispatcher(engine)

    started = time.monotonic()
    dispatcher.submit({"n": 1})
    assert time.monotonic() - started < 0.1


def test_order_is_preserved():
    engine = FakeEngine()
    dispatcher = MessageDispatcher(engine)
    for index in range(5):
        dispatcher.submit({"n": index})
    for _ in range(5):
        dispatcher.drain_one(timeout=0.1)

    assert [item["n"] for item in engine.payloads] == [0, 1, 2, 3, 4]


def test_full_queue_is_rejected_not_blocked():
    engine = FakeEngine(delay=0.2)
    dispatcher = MessageDispatcher(engine, maxsize=1)

    assert dispatcher.submit({"n": 1}) is True
    assert dispatcher.submit({"n": 2}) is False


def test_failure_does_not_stop_the_queue():
    engine = FakeEngine()
    engine.error = RuntimeError("boom")
    dispatcher = MessageDispatcher(engine)

    dispatcher.submit({"n": 1})
    assert dispatcher.drain_one(timeout=0.1) is True  # 异常被吞在 worker 里

    engine.error = None
    dispatcher.submit({"n": 2})
    assert dispatcher.drain_one(timeout=0.1) is True
    assert engine.payloads == [{"n": 2}]


def test_worker_processes_then_stops():
    engine = FakeEngine()
    dispatcher = MessageDispatcher(engine)
    stop = threading.Event()
    dispatcher.submit({"n": 1})

    thread = dispatcher.start(stop)
    deadline = time.monotonic() + 2
    while not engine.payloads and time.monotonic() < deadline:
        time.sleep(0.01)

    stop.set()
    thread.join(timeout=2)
    assert engine.payloads == [{"n": 1}]
