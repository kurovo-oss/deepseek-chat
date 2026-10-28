"""开机自启时等网络就绪的逻辑。"""

import socket

from qqbot.main import model_host, reachable, wait_for_network


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_model_host():
    assert model_host("https://api.deepseek.com") == "api.deepseek.com"
    assert model_host("https://api.deepseek.com/v1") == "api.deepseek.com"
    assert model_host("") is None
    assert model_host("这不是地址") is None


def test_returns_zero_when_already_up():
    clock = FakeClock()
    assert wait_for_network(lambda: True, clock=clock, sleep=clock.sleep) == 0.0


def test_counts_waited_seconds():
    clock = FakeClock()
    answers = [False, False, True]
    waited = wait_for_network(
        lambda: answers.pop(0), timeout=60, interval=3, clock=clock, sleep=clock.sleep
    )
    assert waited == 6.0


def test_gives_up_after_timeout():
    clock = FakeClock()
    assert (
        wait_for_network(
            lambda: False, timeout=9, interval=3, clock=clock, sleep=clock.sleep
        )
        is None
    )


def test_probe_errors_count_as_down():
    clock = FakeClock()

    def probe():
        raise OSError("dns 挂了")

    assert (
        wait_for_network(
            probe, timeout=6, interval=3, clock=clock, sleep=clock.sleep
        )
        is None
    )


def test_reachable_on_listening_port():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert reachable("127.0.0.1", port=port, timeout=1.0) is True


def test_reachable_on_closed_port():
    # 1 号端口基本不会有人监听；只连本机，不出网
    assert reachable("127.0.0.1", port=1, timeout=0.2) is False
