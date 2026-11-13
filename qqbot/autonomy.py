"""受控自主：confirm 队列、shadow 影子运行、bounded_auto 有限自动。

阶段 6 的底线：
- 只允许已确认目标上的 send_message；
- shadow 只记录，不发送；
- bounded_auto 每天最多 1 次，且仍经过主动消息闸门；
- 熔断后不回 confirm，必须手动解除。
"""

from __future__ import annotations

import json
import logging
import time

from . import autonomy_policy
from .goal_actions import GoalActionService

log = logging.getLogger("qqbot")

BREAKER_KEY = "autonomy:breaker"
SHADOW_KEY = "autonomy:shadow_started"
PAUSE_KEY = "autonomy:paused"


def _midnight(now: int) -> int:
    local = time.localtime(now)
    return int(time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)))


def breaker(store) -> dict:
    try:
        data = json.loads(store.get_cursor(BREAKER_KEY, "") or "{}")
    except (TypeError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def trip(store, reason: str, now: int | None = None, goal_id: int | None = None) -> dict:
    stamp = int(now if now is not None else time.time())
    state = breaker(store)
    if state.get("tripped_at"):
        return state
    state = {"tripped_at": stamp, "reason": str(reason)[:120]}
    if goal_id:
        state["goal_id"] = int(goal_id)
    store.set_cursor(BREAKER_KEY, json.dumps(state, ensure_ascii=False))
    log.warning("自主熔断：%s", reason)
    return state


def clear_breaker(store) -> None:
    store.set_cursor(BREAKER_KEY, "")


def paused(store) -> bool:
    return bool(store.get_cursor(PAUSE_KEY, ""))


def set_paused(store, value: bool) -> bool:
    store.set_cursor(PAUSE_KEY, "1" if value else "")
    return bool(value)


def shadow_started(store) -> int:
    try:
        return int(store.get_cursor(SHADOW_KEY, "0") or 0)
    except (TypeError, ValueError):
        return 0


def _ensure_shadow_started(store, now: int) -> int:
    started = shadow_started(store)
    if not started:
        store.set_cursor(SHADOW_KEY, str(int(now)))
        return int(now)
    return started


def shadow_ready(store, settings, now: int) -> bool:
    started = shadow_started(store)
    days = max(0, int(getattr(settings, "autonomy_shadow_days", 7)))
    return bool(started) and now - started >= days * 86400


def _audit(engine, action: str, target: str, result: str, error: str = "") -> None:
    guard = getattr(engine, "guard", None)
    if guard is None:
        return
    try:
        guard.record("autonomy", action, target=target, result=result, error=error)
    except Exception:  # noqa: BLE001 - 审计不能拖垮动作
        log.info("自主审计写入失败", exc_info=True)


def _actions_today(store, since: int, approval: str) -> int:
    rows = store.list_goal_actions(limit=300)
    statuses = ("shadow",) if approval == "shadow" else ("sending", "done", "failed")
    return sum(
        1
        for row in rows
        if int(row.get("created_at") or 0) >= since
        and row.get("approval") == approval
        and row.get("action") == "send_message"
        and row.get("status") in statuses
    )


def _pending_for_goal(store, goal_id: int) -> bool:
    return any(
        int(row.get("goal_id") or 0) == int(goal_id)
        for row in store.list_goal_actions(status="waiting_approval", limit=100)
    )


def _record_decision(store, goal: dict, text: str, decision: dict, now: int, status: str) -> int:
    payload = {
        "text": text,
        "score": decision.get("score", 0),
        "hard_rules": decision.get("hard_rules", []),
        "reasons": decision.get("reasons", []),
        "shadow": status == "shadow",
    }
    return store.add_goal_action(
        int(goal["id"]),
        "send_message",
        payload=payload,
        run_at=now,
        status=status,
        approval="shadow" if status == "shadow" else "auto",
        created_at=now,
    )


def _check_ignored(store, settings, now: int) -> bool:
    """同一目标连续两次自动消息没有 replied → 熔断。"""
    cutoff = now - max(1, int(getattr(settings, "autonomy_ignore_hours", 24))) * 3600
    by_goal: dict[int, list[dict]] = {}
    for row in store.list_goal_actions(status="done", limit=200):
        if row.get("action") != "send_message" or row.get("approval") != "auto":
            continue
        if row.get("result") != "sent" or int(row.get("created_at") or 0) > cutoff:
            continue
        by_goal.setdefault(int(row.get("goal_id") or 0), []).append(row)
    for goal_id, actions in by_goal.items():
        missed = 0
        for action in sorted(actions, key=lambda x: int(x.get("created_at") or 0)):
            outcomes = store.list_outcomes(int(action["id"]), limit=20)
            if any(row.get("signal") == "replied" for row in outcomes):
                missed = 0
            else:
                missed += 1
            if missed >= 2:
                trip(store, "same_goal_ignored_twice", now=now, goal_id=goal_id)
                goal = store.get_goal(goal_id)
                if goal is not None:
                    store.update_goal(goal_id, autonomy="confirm")
                return True
    return False


def _shadow_tick(engine, store, settings, now: int) -> list[dict]:
    _ensure_shadow_started(store, now)
    cap = max(1, int(getattr(settings, "autonomy_shadow_max_per_day", 1)))
    if _actions_today(store, _midnight(now), "shadow") >= cap:
        return []
    service = GoalActionService(engine)
    retry = max(1, int(getattr(settings, "autonomy_retry_hours", 4))) * 3600
    for goal in store.list_goals(status="active", limit=40):
        if goal.get("autonomy") != "auto" or not goal.get("due_at"):
            continue
        if int(goal["due_at"]) > now:
            continue
        if goal.get("review_at") and now - int(goal["review_at"]) < retry:
            continue
        if _pending_for_goal(store, int(goal["id"])):
            continue
        text = service.compose_candidate(goal)
        if not text:
            store.update_goal(int(goal["id"]), review_at=now)
            continue
        decision = autonomy_policy.evaluate(
            goal, text, settings, store, getattr(engine, "proactive_scheduler", None), now
        )
        action_id = _record_decision(store, goal, text, decision, now, "shadow")
        store.update_goal(int(goal["id"]), review_at=now)
        _audit(engine, "shadow", str(goal["id"]), "shadow", "")
        if decision.get("trip"):
            trip(store, "unsafe_text", now=now, goal_id=int(goal["id"]))
        return [store.get_goal_action(action_id) or {}]
    return []


def _auto_tick(engine, store, settings, now: int) -> list[dict]:
    if breaker(store).get("tripped_at"):
        return []
    if not shadow_ready(store, settings, now):
        log.info("bounded_auto 未执行：影子运行未满 %s 天", settings.autonomy_shadow_days)
        return []
    if _check_ignored(store, settings, now):
        return []
    cap = max(1, int(getattr(settings, "autonomy_max_actions_per_day", 1)))
    if _actions_today(store, _midnight(now), "auto") >= cap:
        return []
    service = GoalActionService(engine)
    retry = max(1, int(getattr(settings, "autonomy_retry_hours", 4))) * 3600
    for goal in store.list_goals(status="active", limit=40):
        if goal.get("autonomy") != "auto" or not goal.get("due_at"):
            continue
        if int(goal["due_at"]) > now:
            continue
        if goal.get("review_at") and now - int(goal["review_at"]) < retry:
            continue
        if _pending_for_goal(store, int(goal["id"])):
            continue
        text = service.compose_candidate(goal)
        if not text:
            store.update_goal(int(goal["id"]), review_at=now)
            continue
        decision = autonomy_policy.evaluate(
            goal, text, settings, store, getattr(engine, "proactive_scheduler", None), now
        )
        if decision.get("trip"):
            trip(store, "unsafe_text", now=now, goal_id=int(goal["id"]))
        action_id = _record_decision(store, goal, text, decision, now, "blocked" if not decision["passed"] else "sending")
        if not decision["passed"]:
            store.update_goal(int(goal["id"]), review_at=now)
            _audit(engine, "auto_blocked", str(goal["id"]), "blocked", ",".join(decision["hard_rules"]))
            return []
        scheduler = getattr(engine, "proactive_scheduler", None)
        if scheduler is None:
            store.update_goal_action(action_id, status="blocked", result="no_scheduler")
            store.update_goal(int(goal["id"]), review_at=now)
            return []
        ok, reason = scheduler.send_approved(text, now)
        status = "done" if ok else "failed"
        store.update_goal_action(action_id, status=status, result="sent" if ok else reason[:200])
        store.add_outcome(action_id, "sent" if ok else "failed", created_at=now)
        store.update_goal(int(goal["id"]), review_at=now)
        _audit(engine, "auto_send", str(goal["id"]), status, "" if ok else reason)
        return [store.get_goal_action(action_id) or {}]
    return []


def tick(engine, store, settings, now: int | None = None) -> list[dict]:
    if not getattr(settings, "autonomy_enabled", False):
        return []
    stamp = int(now if now is not None else time.time())
    if paused(store):
        return []
    mode = str(getattr(settings, "autonomy_mode", "confirm") or "confirm")
    if mode == "shadow":
        return _shadow_tick(engine, store, settings, stamp)
    if mode == "bounded_auto":
        return _auto_tick(engine, store, settings, stamp)
    if mode != "confirm":
        return []
    if breaker(store).get("tripped_at"):
        return []
    cap = max(1, int(getattr(settings, "autonomy_max_actions_per_day", 1)))
    if store.count_goal_actions_since(_midnight(stamp)) >= cap:
        return []
    service = GoalActionService(engine)
    prepared: list[dict] = []
    retry = max(1, int(getattr(settings, "autonomy_retry_hours", 4))) * 3600
    for goal in store.list_goals(status="active", limit=40):
        if not goal.get("due_at") or int(goal["due_at"]) > stamp:
            continue
        if goal.get("autonomy") not in (None, "", "confirm"):
            continue
        if goal.get("review_at") and stamp - int(goal["review_at"]) < retry:
            continue
        try:
            action = service.prepare(int(goal["id"]), now=stamp)
            prepared.append(action)
            store.update_goal(int(goal["id"]), review_at=stamp)
        except Exception as exc:  # noqa: BLE001
            log.info("目标动作进入队列失败：%s", exc)
            store.update_goal(int(goal["id"]), review_at=stamp)
        if prepared:
            break
    return prepared
