"""受控目标调度：只把到期的进行中目标放进待确认队列。

不直接发送。真正的发送由主人点“确认发送”触发，仍然经过 ProactiveScheduler。
"""

from __future__ import annotations

import logging
import time

from .goal_actions import GoalActionService

log = logging.getLogger("qqbot")


def _midnight(now: int) -> int:
    local = time.localtime(now)
    return int(time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)))


def tick(engine, store, settings, now: int | None = None) -> list[dict]:
    if not getattr(settings, "autonomy_enabled", False):
        return []
    if str(getattr(settings, "autonomy_mode", "confirm")) != "confirm":
        return []
    stamp = int(now if now is not None else time.time())
    cap = max(1, int(getattr(settings, "autonomy_max_actions_per_day", 1)))
    if store.count_goal_actions_since(_midnight(stamp)) >= cap:
        return []
    service = GoalActionService(engine)
    prepared: list[dict] = []
    for goal in store.list_goals(status="active", limit=40):
        if not goal.get("due_at") or int(goal["due_at"]) > stamp:
            continue
        if goal.get("autonomy") not in (None, "", "confirm"):
            continue
        try:
            action = service.prepare(int(goal["id"]), now=stamp)
            prepared.append(action)
        except Exception as exc:  # noqa: BLE001 - 单条目标失败不影响其他目标
            log.info("目标动作进入队列失败：%s", exc)
        if prepared:
            break
    return prepared
