"""任务队列：让“稍后告诉你”真的有一个后台任务。

任务只做两件事：
1. 到期后尝试完成（文字类交给模型；现实类诚实说做不到）；
2. 完成后按通知规则主动说，不静默消失。
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("qqbot")

PROMISE_RE = re.compile(
    r"(稍后|晚点|等一下|等我|一会儿|过会儿|完成(?:后)?|做完|弄完|忙完)"
    r"[^。\n]{0,20}(告诉你|跟你说|发你|给你看|通知你|回复你|找你)"
)


def _prompt(settings) -> str:
    path = Path(getattr(settings, "task_prompt_file", "prompts/task_followup.md"))
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return (
            "这是你答应他的事：{{TASK}}\n背景：{{DETAIL}}\n"
            "能完成就完成并告诉他；做不到就诚实说，不要编。"
        )


class TaskService:
    def __init__(self, engine, settings) -> None:
        self.engine = engine
        self.settings = settings
        self.store = engine.store

    def create(
        self,
        title: str,
        description: str = "",
        kind: str = "followup",
        due_at: int | None = None,
        source_message_id: int | None = None,
    ) -> dict:
        task_id = self.store.add_task(
            title=title[:160],
            description=description[:2000],
            kind=kind,
            due_at=due_at,
            source_message_id=source_message_id,
        )
        return self.store.get_task(task_id) or {}

    def note_promise(
        self,
        user_text: str,
        reply: str,
        source_message_id: int | None = None,
        now: int | None = None,
    ) -> dict | None:
        """回复里出现“稍后/做完告诉你”，但没有对应任务时补一个。"""
        if not getattr(self.settings, "tasks_enabled", True):
            return None
        reply = str(reply or "")
        if not PROMISE_RE.search(reply):
            return None
        if source_message_id:
            for task in self.store.list_tasks(limit=100):
                if task.get("source_message_id") == source_message_id:
                    return None
        stamp = int(now if now is not None else time.time())
        due = stamp + max(1, int(getattr(self.settings, "task_promise_minutes", 15))) * 60
        title = " ".join(str(user_text or "").split())[:120] or "刚才答应的事"
        return self.create(
            title=title,
            description=f"他刚才说：{user_text[:800]}\n你当时回复：{reply[:800]}",
            kind="promise",
            due_at=due,
            source_message_id=source_message_id,
        )

    def _compose(self, task: dict) -> str:
        if str(task.get("kind")) == "reminder":
            return f"提醒你一下：{task.get('title', '')}"
        try:
            messages = self.engine._build_messages(
                str(self.settings.allow_user_id), allow_refusal=False, proactive=True
            )
            instruction = (
                _prompt(self.settings)
                .replace("{{TASK}}", str(task.get("title") or ""))
                .replace("{{DETAIL}}", str(task.get("description") or ""))
            )
            messages.append({"role": "system", "content": instruction})
            result = self.engine.llm.chat(
                messages,
                temperature=0.5,
                max_tokens=500,
                purpose="task",
            )
        except Exception:  # noqa: BLE001
            log.exception("任务生成失败")
            return ""
        text = " ".join(str(getattr(result, "text", "") or "").split())
        return text[:600]

    def run_due(self, now: int | None = None) -> list[dict]:
        if not getattr(self.settings, "tasks_enabled", True):
            return []
        stamp = int(now if now is not None else time.time())
        done = []
        for task in self.store.due_tasks(stamp, limit=1):
            self.store.update_task(
                int(task["id"]), status="running", started_at=stamp, attempts=int(task.get("attempts") or 0) + 1
            )
            result = self._compose(task)
            if result:
                self.store.update_task(
                    int(task["id"]),
                    status="done",
                    result=result,
                    finished_at=stamp,
                    notify_at=stamp,
                    notify_status="pending",
                )
                done.append(self.store.get_task(int(task["id"])) or {})
            else:
                self.store.update_task(
                    int(task["id"]),
                    status="failed",
                    error="empty_result",
                    result="这件事我还没弄完，先跟你说一声。",
                    notify_at=stamp,
                    notify_status="pending",
                )
        return done

    def _notification_allowed(self, now: int) -> bool:
        if self.settings.in_quiet_hours(now):
            return False
        start = datetime.fromtimestamp(now)
        midnight = int(start.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        cap = max(1, int(getattr(self.settings, "task_notify_daily_max", 3)))
        return self.store.count_tasks_notified_since(midnight) < cap

    def notify_due(self, now: int | None = None) -> list[dict]:
        stamp = int(now if now is not None else time.time())
        if not self._notification_allowed(stamp):
            # 免打扰/额度满：稍后再试，不丢任务。
            for task in self.store.due_task_notifications(stamp, limit=20):
                self.store.update_task(int(task["id"]), notify_at=stamp + 1800)
            return []
        sent = []
        for task in self.store.due_task_notifications(stamp, limit=1):
            text = str(task.get("result") or "").strip()
            if not text:
                self.store.update_task(
                    int(task["id"]), notify_status="failed", notified_at=stamp
                )
                continue
            ok = self.engine.send_proactive(str(self.settings.allow_user_id), text)
            if ok:
                self.store.update_task(
                    int(task["id"]), notify_status="sent", notified_at=stamp
                )
                sent.append(task)
            else:
                self.store.update_task(
                    int(task["id"]), notify_status="pending", notify_at=stamp + 300
                )
                break
        return sent

    def tick(self, now: int | None = None) -> None:
        self.run_due(now)
        self.notify_due(now)


def start_task_worker(service: TaskService, stop_event: threading.Event) -> threading.Thread:
    def run() -> None:
        interval = max(5, int(getattr(service.settings, "task_poll_seconds", 20)))
        while not stop_event.wait(interval):
            try:
                service.tick()
            except Exception:  # noqa: BLE001 - worker 不能死
                log.exception("任务 worker 异常")

    thread = threading.Thread(target=run, name="tasks", daemon=True)
    thread.start()
    return thread
