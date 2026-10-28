"""主动消息：她先开口。

闸门按 MVP §6.2，缺一不可：
- 每天 ≤ 3 条
- 只在 09:00–23:00
- 两条之间 ≥ 4 小时
- 用户最近 30 分钟说过话 → 不发
- followup 每周 ≤ 1 次

失败不重试（§6.3）——重试等于骚扰。

`followup` 的触发是"72 小时没说话"，内容**只能取记忆里的真实条目**，
不允许模型自由编话题：一个记岔了的话题比不开口更糟。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from .llm import LLMError

log = logging.getLogger("qqbot")

SKIP_TOKEN = "SKIP"

FOLLOWUP = "followup"

DEFAULT_INSTRUCTION = (
    "现在是 {time}，你们已经 {hours} 小时没说话了。\n"
    "如果此刻开口合适，就写一句自然的话；否则只输出 SKIP。"
)


class ProactiveScheduler:
    def __init__(self, engine, store, settings, clock=time.time, prompt: str = "") -> None:
        self.engine = engine
        self.store = store
        self.settings = settings
        self._clock = clock
        self.prompt = prompt

    @property
    def session(self) -> str:
        return str(self.settings.allow_user_id)

    # ---------------- 闸门 ----------------
    @staticmethod
    def midnight(now: int) -> int:
        start = datetime.fromtimestamp(now).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return int(start.timestamp())

    def gate(self, now: int) -> str | None:
        """现在能不能主动开口？能就返回 kind，不能返回 None。"""
        settings = self.settings
        if not settings.proactive_enabled:
            return None

        hour = datetime.fromtimestamp(now).hour
        if not settings.proactive_start_hour <= hour < settings.proactive_end_hour:
            return None

        if (
            self.store.count_proactive_since(self.midnight(now))
            >= settings.proactive_max_per_day
        ):
            return None

        last_sent = self.store.latest_proactive(status="sent")
        # 间隔对"任何一次尝试"生效：失败后立刻重来一遍，本质就是重试
        last_attempt = self.store.latest_proactive()
        if last_attempt is not None:
            gap = settings.proactive_min_gap_hours * 3600
            if now - int(last_attempt["created_at"]) < gap:
                return None

        last_user = self.store.last_message_time(self.session, role="user")
        if last_user is None:
            return None  # 一句话都还没说过，谈不上"很久没聊"
        if now - last_user < settings.proactive_quiet_minutes * 60:
            return None  # 人就在，别插嘴

        # 她上次主动说完，用户还没回 —— 不能连着追
        if last_sent is not None and int(last_sent["created_at"]) > last_user:
            return None

        if now - last_user < settings.proactive_idle_hours * 3600:
            return None

        week_ago = now - 7 * 86400
        if (
            self.store.count_proactive_since(week_ago, kind=FOLLOWUP)
            >= settings.proactive_weekly_max
        ):
            return None

        return FOLLOWUP

    # ---------------- 生成 ----------------
    def build_messages(self, kind: str, now: int) -> list[dict]:
        messages = self.engine._build_messages(self.session)
        last_user = self.store.last_message_time(self.session, role="user") or now
        hours = max(1, int((now - last_user) / 3600))
        stamp = datetime.fromtimestamp(now).strftime("%H:%M")
        template = self.prompt or DEFAULT_INSTRUCTION
        instruction = template.replace("{hours}", str(hours)).replace("{time}", stamp)
        messages.append({"role": "system", "content": instruction})
        return messages

    def compose(self, kind: str, now: int) -> str | None:
        try:
            result = self.engine.llm.chat(
                self.build_messages(kind, now), temperature=0.9, max_tokens=160
            )
        except LLMError as exc:
            log.warning("主动消息生成失败：%s", exc)
            return None

        self.store.log_usage(
            result.model, result.tokens_in, result.tokens_out, "proactive", True
        )
        text = result.text.strip()
        first_line = text.splitlines()[0].strip() if text else ""
        if not first_line or first_line.upper().startswith(SKIP_TOKEN):
            return None
        return text

    # ---------------- 调度 ----------------
    def tick(self, now: int | None = None) -> str:
        stamp = int(now if now is not None else self._clock())
        kind = self.gate(stamp)
        if kind is None:
            return "blocked"

        text = self.compose(kind, stamp)
        if text is None:
            log.info("主动消息：这次决定不说")
            return "skipped"

        ok = self.engine.send_proactive(self.session, text)
        self.store.add_proactive_event(
            kind, text, trigger_at=stamp, status="sent" if ok else "failed"
        )
        if not ok:
            log.warning("主动消息发送失败，不重试")
        return "sent" if ok else "failed"

    def force(self, now: int | None = None) -> str:
        """手动试一次（/proactive 用）：不走闸门，只走生成和发送。"""
        stamp = int(now if now is not None else self._clock())
        text = self.compose(FOLLOWUP, stamp)
        if text is None:
            return "skipped"
        ok = self.engine.send_proactive(self.session, text)
        self.store.add_proactive_event(
            "manual", text, trigger_at=stamp, status="sent" if ok else "failed"
        )
        return "sent" if ok else "failed"

    def run(self, stop_event: threading.Event) -> None:
        interval = self.settings.proactive_check_seconds
        while not stop_event.wait(interval):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - 调度线程不能死
                log.exception("主动消息调度异常")


def start_scheduler(
    engine, store, settings, stop_event: threading.Event, prompt: str = ""
) -> ProactiveScheduler:
    scheduler = ProactiveScheduler(engine, store, settings, prompt=prompt)
    thread = threading.Thread(
        target=scheduler.run, args=(stop_event,), name="proactive", daemon=True
    )
    thread.start()
    return scheduler
