"""主动消息：她先开口。

两种来源：
- `scheduled`：记忆里登记的未来事件到点了（"下周三面试"）
- `followup`：72 小时没说话，从记忆里找话题

闸门按 MVP §6.2，缺一不可：
- 每天 ≤ 3 条
- 只在 09:00–23:00
- 两条之间 ≥ 4 小时
- 用户最近 30 分钟说过话 → 不发
- followup 每周 ≤ 1 次

失败不重试（§6.3）——重试等于骚扰。

`followup` 的内容**只能取记忆里的真实条目**，不允许模型自由编话题：
一个记岔了的话题比不开口更糟。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from .llm import LLMError
from .mood import MISS_LIMIT, TIRED_LIMIT

log = logging.getLogger("qqbot")

SKIP_TOKEN = "SKIP"

FOLLOWUP = "followup"
SCHEDULED = "scheduled"

DEFAULT_INSTRUCTION = (
    "现在是 {time}，你们已经 {hours} 小时没说话了。\n"
    "如果此刻开口合适，就写一句自然的话；否则只输出 SKIP。"
)

DEFAULT_SCHEDULED = (
    "现在是 {time}。你记得他之前说过：{event}。就是今天。\n"
    "如果现在开口问候合适，就写一句自然的话；否则只输出 SKIP。"
)


class ProactiveScheduler:
    # 一件未来事件最多尝试几次（模型说 SKIP 或发送失败都算一次）
    MAX_EVENT_ATTEMPTS = 2

    def __init__(
        self,
        engine,
        store,
        settings,
        clock=time.time,
        prompt: str = "",
        scheduled_prompt: str = "",
    ) -> None:
        self.engine = engine
        self.store = store
        self.settings = settings
        self._clock = clock
        self.prompt = prompt
        self.scheduled_prompt = scheduled_prompt
        self.last_reason = ""  # /proactive 排查用

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
        return self._decide(now)[0]

    def _decide(self, now: int) -> tuple[str | None, str]:
        """返回 (能不能发, 为什么)。原因只写进日志，不给他看。"""
        settings = self.settings
        if not settings.proactive_enabled:
            return None, "关闭"

        hour = datetime.fromtimestamp(now).hour
        if not settings.proactive_start_hour <= hour < settings.proactive_end_hour:
            return None, "不在时段"

        if (
            self.store.count_proactive_since(self.midnight(now))
            >= settings.proactive_max_per_day
        ):
            return None, "今天说够了"

        last_sent = self.store.latest_proactive(status="sent")
        # 间隔对"任何一次尝试"生效：失败后立刻重来一遍，本质就是重试
        last_attempt = self.store.latest_attempt()
        if last_attempt is not None:
            gap = settings.proactive_min_gap_hours * 3600
            if now - int(last_attempt["created_at"]) < gap:
                return None, "刚找过他"

        last_user = self.store.last_message_time(self.session, role="user")
        if last_user is not None and now - last_user < settings.proactive_quiet_minutes * 60:
            return None, "他刚说话"

        # 到点的未来事件优先——那是他自己说过的事，最该被记住
        if self.store.next_due_event(now) is not None:
            return SCHEDULED, "到点的事"

        if last_user is None:
            return None, "还没聊过"

        # 她上次主动说完，用户还没回 —— 不能连着追
        if last_sent is not None and int(last_sent["created_at"]) > last_user:
            return None, "上次他还没回"

        idle_required = settings.proactive_idle_hours * 3600
        reason = "想他了"
        mood = getattr(self.engine, "mood", None)
        if mood is not None:
            values = mood.snapshot(now)
            if values["tired"] >= TIRED_LIMIT:
                # 累了就不主动找人说话。约定好的事（scheduled）已经在上一步放行了，
                # 那句照说——是答应过他的，不能因为累就不提。
                return None, "累了"
            if values["miss"] >= MISS_LIMIT:
                idle_required *= 0.6  # 很想他的时候，早一点开口
                reason = "很想他"
            elif values["warmth"] >= 0.6:
                idle_required *= 0.8  # 心情好，想跟他分享
                reason = "心情好"
        if now - last_user < idle_required:
            return None, f"还没静默够（要 {idle_required / 3600:.1f} 小时）"

        week_ago = now - 7 * 86400
        if (
            self.store.count_proactive_since(week_ago, kind=FOLLOWUP)
            >= settings.proactive_weekly_max
        ):
            return None, "这周说够了"

        return FOLLOWUP, reason

    # ---------------- 生成 ----------------
    def build_messages(self, kind: str, now: int, event: str = "") -> list[dict]:
        messages = self.engine._build_messages(self.session, allow_refusal=False)
        stamp = datetime.fromtimestamp(now).strftime("%H:%M")

        if event:
            template = self.scheduled_prompt or DEFAULT_SCHEDULED
            instruction = template.replace("{event}", event).replace("{time}", stamp)
        else:
            last_user = self.store.last_message_time(self.session, role="user") or now
            hours = max(1, int((now - last_user) / 3600))
            template = self.prompt or DEFAULT_INSTRUCTION
            instruction = template.replace("{hours}", str(hours)).replace(
                "{time}", stamp
            )

        mood_hint = self._mood_hint(now)
        if mood_hint:
            # 系统提示里已经有一段"此刻的你"，但那是背景；这句贴在指令后面，
            # 直接决定她开口的语气
            instruction = instruction + "\n" + mood_hint

        messages.append({"role": "system", "content": instruction})
        return messages

    def _mood_hint(self, now: int) -> str:
        """主动开口时的语气提示：她现在是什么心情，就怎么开这个头。"""
        mood = getattr(self.engine, "mood", None)
        if mood is None:
            return ""
        values = mood.snapshot(now)
        if values["tired"] >= TIRED_LIMIT:
            return "你这会儿很累，别撑着说漂亮话。一句话就够，说完就算。"
        if values["miss"] >= MISS_LIMIT:
            return "你很想他。可以直说想他了，别绕弯子。"
        if values["warmth"] >= 0.6:
            return "你现在心情不错，说话可以松快点。"
        return ""

    def compose(self, kind: str, now: int, event: str = "") -> str | None:
        try:
            result = self.engine.llm.chat(
                self.build_messages(kind, now, event), temperature=0.9, max_tokens=160
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

    # ---------------- 发送 ----------------
    def _fire_scheduled(self, now: int) -> str:
        event = self.store.next_due_event(now)
        if event is None:
            return "blocked"

        attempts = int(event.get("attempts") or 0)
        text = self.compose(SCHEDULED, now, event=event["content"])
        if text is None:
            return self._postpone(event, attempts, now, "skipped")

        ok = self.engine.send_proactive(self.session, text)
        if ok:
            self.store.update_proactive_event(
                event["id"], content=text, status="sent", created_at=now
            )
            log.info("主动消息（scheduled）：%s", event["content"])
            return "sent"
        return self._postpone(event, attempts, now, "failed")

    def _postpone(self, event: dict, attempts: int, now: int, outcome: str) -> str:
        """这次没发出去：往后推再试，别把事弄丢。

        "下周三面试"不该因为模型某一刻不想说话、或一次网络抖动就消失。
        但也不能无限重试——那是骚扰，所以最多 MAX_EVENT_ATTEMPTS 次。
        """
        if attempts + 1 >= self.MAX_EVENT_ATTEMPTS:
            self.store.update_proactive_event(event["id"], status=outcome)
            log.warning("未来事件最终没发出去（%s）：%s", outcome, event["content"])
            return outcome

        retry_hours = self.settings.proactive_event_retry_hours
        next_at = now + int(retry_hours * 3600)
        self.store.reschedule_event(event["id"], next_at)
        log.info(
            "未来事件推迟 %.0f 小时再试（第 %d 次）：%s",
            retry_hours,
            attempts + 1,
            event["content"],
        )
        return outcome

    def _fire_followup(self, now: int, manual: bool = False) -> str:
        text = self.compose(FOLLOWUP, now)
        if text is None:
            log.info("主动消息：这次决定不说")
            return "skipped"

        ok = self.engine.send_proactive(self.session, text)
        self.store.add_proactive_event(
            "manual" if manual else FOLLOWUP,
            text,
            trigger_at=now,
            status="sent" if ok else "failed",
        )
        if not ok:
            log.warning("主动消息发送失败，不重试")
        return "sent" if ok else "failed"

    def expire_stale(self, now: int) -> int:
        """过期作废：事件过去太久就别再提了，翻旧账很怪。"""
        limit = self.settings.proactive_event_expire_days * 86400
        expired = 0
        for event in self.store.list_events(status="pending", limit=50):
            if now - int(event["trigger_at"]) > limit:
                self.store.update_proactive_event(event["id"], status="expired")
                log.info("未来事件过期作废：%s", event["content"])
                expired += 1
        return expired

    # ---------------- 调度 ----------------
    def tick(self, now: int | None = None) -> str:
        stamp = int(now if now is not None else self._clock())
        self.expire_stale(stamp)
        kind, reason = self._decide(stamp)
        self.last_reason = reason
        if kind is None:
            log.debug("主动消息没发：%s", reason)
            return "blocked"
        if kind == SCHEDULED:
            return self._fire_scheduled(stamp)
        return self._fire_followup(stamp)

    def force(self, now: int | None = None) -> str:
        """手动试一次（/proactive）：有到点事件就发事件，否则发 followup。"""
        stamp = int(now if now is not None else self._clock())
        if self.store.next_due_event(stamp) is not None:
            return self._fire_scheduled(stamp)
        return self._fire_followup(stamp, manual=True)

    def run(self, stop_event: threading.Event) -> None:
        interval = self.settings.proactive_check_seconds
        # 立刻查一次：等满一个周期的话，开机后到点的事件要白等十分钟
        while not stop_event.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - 调度线程不能死
                log.exception("主动消息调度异常")
            if stop_event.wait(interval):
                break


def start_scheduler(
    engine,
    store,
    settings,
    stop_event: threading.Event,
    prompt: str = "",
    scheduled_prompt: str = "",
) -> ProactiveScheduler:
    scheduler = ProactiveScheduler(
        engine, store, settings, prompt=prompt, scheduled_prompt=scheduled_prompt
    )
    thread = threading.Thread(
        target=scheduler.run, args=(stop_event,), name="proactive", daemon=True
    )
    thread.start()
    return scheduler
