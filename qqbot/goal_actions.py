"""目标动作：准备一条主动消息，等主人确认后才发送。

第一阶段只做“预览 + 确认”；没有确认就不会产生任何 QQ 消息。
发送仍然复用 ProactiveScheduler 的全部闸门（时段、额度、间隔、免打扰）。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

log = logging.getLogger("qqbot")

SKIP_TOKEN = "SKIP"
MAX_TEXT = 240


def _prompt(settings) -> str:
    path = Path(getattr(settings, "base_dir", ".")) / "prompts" / "goals_action.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return (
            "你在执行自己的计划：{{TITLE}}。原因：{{WHY}}。"
            "现在主动说一句自然的话；不适合就只输出 SKIP。"
        )


class GoalActionService:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.store = engine.store
        self.settings = engine.settings

    def _compose(self, goal: dict) -> str | None:
        messages = self.engine._build_messages(
            str(self.settings.allow_user_id), allow_refusal=False, proactive=True
        )
        instruction = (
            _prompt(self.settings)
            .replace("{{TITLE}}", str(goal.get("title") or ""))
            .replace("{{WHY}}", str(goal.get("why") or ""))
        )
        messages.append({"role": "system", "content": instruction})
        try:
            result = self.engine.llm.chat(
                messages,
                temperature=0.8,
                max_tokens=180,
                purpose="goal_action",
            )
        except Exception:  # noqa: BLE001 - 生成失败不影响计划本身
            log.exception("目标动作生成失败")
            return None
        text = str(getattr(result, "text", "") or "").strip()
        first = text.splitlines()[0].strip() if text else ""
        if not first or first.upper().startswith(SKIP_TOKEN):
            return None
        return first[:MAX_TEXT]

    def prepare(self, goal_id: int, now: int | None = None) -> dict:
        goal = self.store.get_goal(int(goal_id))
        if goal is None:
            raise ValueError("这个目标已经不在了")
        if goal.get("status") != "active":
            raise ValueError("先把目标确认成“进行中”")
        pending = [
            item
            for item in self.store.list_goal_actions(status="waiting_approval", limit=50)
            if int(item.get("goal_id") or 0) == int(goal_id)
        ]
        if pending:
            return pending[0]
        text = self._compose(goal)
        if not text:
            raise ValueError("她这次不想主动开口")
        stamp = int(now if now is not None else time.time())
        action_id = self.store.add_goal_action(
            int(goal_id),
            "send_message",
            payload={"text": text},
            run_at=stamp,
            status="waiting_approval",
            approval="confirm",
            created_at=stamp,
        )
        action = self.store.get_goal_action(action_id)
        return action or {}

    def approve(self, action_id: int, now: int | None = None) -> dict:
        action = self.store.get_goal_action(int(action_id))
        if action is None:
            raise ValueError("这个动作已经不在了")
        if action.get("status") != "waiting_approval":
            raise ValueError("这个动作已经处理过了")
        goal = self.store.get_goal(int(action.get("goal_id") or 0))
        if goal is None or goal.get("status") != "active":
            raise ValueError("目标不在进行中，先恢复目标")
        text = str((action.get("payload") or {}).get("text") or "").strip()
        if not text:
            raise ValueError("动作内容为空")
        scheduler = getattr(self.engine, "proactive_scheduler", None)
        if scheduler is None:
            raise ValueError("主动消息没有启用，暂时不能发送")
        ok, reason = scheduler.send_approved(text, now=now)
        if not ok:
            self.store.update_goal_action(int(action_id), result=reason)
            raise ValueError(f"现在不能发：{reason}")
        stamp = int(now if now is not None else time.time())
        self.store.update_goal_action(int(action_id), status="done", result="sent")
        self.store.add_outcome(int(action_id), "sent", created_at=stamp)
        self.store.update_goal(int(goal["id"]), review_at=stamp)
        return self.store.get_goal_action(int(action_id)) or {}

    def cancel(self, action_id: int) -> dict:
        action = self.store.get_goal_action(int(action_id))
        if action is None:
            raise ValueError("这个动作已经不在了")
        if action.get("status") != "waiting_approval":
            raise ValueError("这个动作已经处理过了")
        self.store.update_goal_action(int(action_id), status="cancelled", result="cancelled")
        return self.store.get_goal_action(int(action_id)) or {}
