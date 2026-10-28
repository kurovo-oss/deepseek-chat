"""协议端掉线看门狗。

背景（2026-10-28 实况）：QQ 内核断网后，NapCat 的 `get_status` 仍然返回
`{"online": true, "good": true}`，`get_login_info` 也照常给出账号昵称——
于是机器人一声不响地变成哑巴，用户只能干等。

唯一可靠的信号是走内核的接口：`get_friend_list`。会话断了它会返回空列表
（或直接失败），而一个真正在线的账号不可能一个好友都没有。
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("qqbot")


class AdapterWatchdog:
    def __init__(self, sender, interval: float = 300.0, max_failures: int = 2) -> None:
        self.sender = sender
        self.interval = interval
        self.max_failures = max(1, max_failures)
        self._failures = 0
        self._warned = False

    def tick(self) -> str:
        """探一次。返回 ok / retry / down。"""
        friends = self.sender.friend_list()

        if friends:
            self._failures = 0
            self._warned = False
            return "ok"

        # 探测失败，或拿到了空列表。空列表同样算失败：
        # 一个正常在线的账号不可能一个好友都没有（这个号至少有你），
        # 2026-10-28 的真实故障就是内核断网后 get_friend_list 回 []。
        self._failures += 1
        if self._failures >= self.max_failures:
            reason = "好友列表为空" if friends is not None else "探测不到协议端"
            return self._warn("%s（连续 %d 次）" % (reason, self._failures))
        return "retry"

    def _warn(self, reason: str) -> str:
        if not self._warned:
            log.warning(
                "协议端会话可能已经断开（%s）。QQ 内核掉线时 NapCat 仍会报在线，"
                "请在协议端窗口重新登录或重启 NapCat。",
                reason,
            )
            self._warned = True
        return "down"

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.wait(self.interval):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - 看门狗自己不能死
                log.exception("协议端探测异常")


def start_watchdog(sender, interval: float, stop_event: threading.Event) -> threading.Thread:
    watchdog = AdapterWatchdog(sender, interval=interval)
    thread = threading.Thread(
        target=watchdog.run, args=(stop_event,), name="adapter-watchdog", daemon=True
    )
    thread.start()
    return thread
