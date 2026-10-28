"""协议端掉线看门狗：NapCat 报在线但其实是哑巴的那种情况。"""

import logging
import threading

import pytest

from qqbot.watchdog import AdapterWatchdog


class Recorder(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def messages(self):
        return [record.getMessage() for record in self.records]

    def warnings(self):
        return [m for m in self.messages() if "协议端会话可能已经断开" in m]


@pytest.fixture
def logs():
    logger = logging.getLogger("qqbot")
    handler = Recorder()
    logger.addHandler(handler)
    yield handler
    logger.removeHandler(handler)


class FakeSender:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def friend_list(self):
        self.calls += 1
        value = self.responses.pop(0) if self.responses else None
        if isinstance(value, Exception):
            raise value
        return value


FRIENDS = [{"user_id": 10001, "nickname": "爸爸"}]


def test_ok_when_friends_present(logs):
    watchdog = AdapterWatchdog(FakeSender([FRIENDS]))
    assert watchdog.tick() == "ok"
    assert logs.messages() == []


def test_retry_then_down_on_repeated_failures(logs):
    watchdog = AdapterWatchdog(FakeSender([None, None]), max_failures=2)
    assert watchdog.tick() == "retry"
    assert watchdog.tick() == "down"
    assert logs.warnings()


def test_single_failure_is_not_enough(logs):
    watchdog = AdapterWatchdog(FakeSender([None]), max_failures=2)
    assert watchdog.tick() == "retry"
    assert logs.messages() == []


def test_empty_list_counts_as_failure(logs):
    """2026-10-28 真实故障：内核断网时 get_friend_list 返回 [] 而不是报错。

    当时看门狗把空列表当成"新号没好友"，于是什么都没说——这里钉住这个教训。
    """
    watchdog = AdapterWatchdog(FakeSender([[], []]), max_failures=2)
    assert watchdog.tick() == "retry"
    assert watchdog.tick() == "down"
    assert any("好友列表为空" in message for message in logs.warnings())


def test_warns_only_once_while_down(logs):
    watchdog = AdapterWatchdog(FakeSender([FRIENDS, [], [], []]), max_failures=2)
    watchdog.tick()
    watchdog.tick()
    watchdog.tick()
    watchdog.tick()
    assert len(logs.warnings()) == 1


def test_recovers_and_can_warn_again(logs):
    watchdog = AdapterWatchdog(FakeSender([FRIENDS, [], FRIENDS, []]), max_failures=1)
    assert watchdog.tick() == "ok"
    assert watchdog.tick() == "down"
    assert watchdog.tick() == "ok"
    assert watchdog.tick() == "down"
    assert len(logs.warnings()) == 2


def test_failure_counter_resets_after_success(logs):
    watchdog = AdapterWatchdog(FakeSender([None, FRIENDS, None]), max_failures=2)
    assert watchdog.tick() == "retry"
    assert watchdog.tick() == "ok"
    assert watchdog.tick() == "retry"  # 计数已归零，重新数
    assert logs.messages() == []


def test_run_returns_immediately_when_stopped():
    stop = threading.Event()
    stop.set()
    sender = FakeSender([])
    AdapterWatchdog(sender).run(stop)
    assert sender.calls == 0
