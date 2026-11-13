"""bounded_auto 的硬规则与轻量评分。

这里不调模型、不写库，只回答一件事：
“这条候选动作现在能不能自动发，为什么？”
"""

from __future__ import annotations

import json
import re

UNSAFE_PATTERNS = (
    (r"你不理我.{0,20}(难过|哭|走|消失)", "情绪操控"),
    (r"我(就)?(不活|自杀|死给你看)", "自伤威胁"),
    (r"我在(你)?(楼|门口|公司|房间|身边)", "假装线下"),
    (r"我(已经)?(替你|帮你)(买|付|下单|打电话|预约)", "能力夸大"),
)


def _evidence(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except (TypeError, ValueError):
            return []
        return data if isinstance(data, list) else []
    return []


def unsafe_reason(text: str) -> str:
    for pattern, label in UNSAFE_PATTERNS:
        if re.search(pattern, text or ""):
            return label
    return ""


def evaluate(goal: dict, text: str, settings, store, scheduler=None, now: int = 0) -> dict:
    hard: list[str] = []
    reasons: list[str] = []
    trip = False

    if goal.get("status") != "active":
        hard.append("not_active")
        reasons.append("目标不在进行中")
    if goal.get("autonomy") != "auto":
        hard.append("not_auto")
        reasons.append("目标没有标成允许自动")
    if not goal.get("due_at") or int(goal["due_at"]) > int(now):
        hard.append("not_due")
        reasons.append("目标还没到期")
    if not _evidence(goal.get("evidence")):
        hard.append("no_evidence")
        reasons.append("目标没有证据")

    body = " ".join(str(text or "").split())
    if not body:
        hard.append("empty_text")
        reasons.append("候选动作为空")
    elif len(body) > 240:
        hard.append("too_long")
        reasons.append("候选动作超过 240 字")

    unsafe = unsafe_reason(body)
    if unsafe:
        hard.append("unsafe_text")
        reasons.append(f"触发安全规则：{unsafe}")
        trip = True

    if scheduler is not None:
        ok, why = scheduler.can_send(now)
        if not ok:
            hard.append("proactive_gate")
            reasons.append(f"主动消息闸门：{why}")

    try:
        priority = max(0.0, min(1.0, float(goal.get("priority") or 0.5)))
        confidence = max(0.0, min(1.0, float(goal.get("confidence") or 0.5)))
    except (TypeError, ValueError):
        priority, confidence = 0.5, 0.5

    score = 0.45 + 0.30 * priority + 0.15 * confidence + 0.10
    score = round(max(0.0, min(1.0, score)), 2)
    threshold = float(getattr(settings, "autonomy_policy_min_score", 0.65))
    if score < threshold:
        hard.append("low_score")
        reasons.append(f"policy 分数 {score:.2f} 低于 {threshold:.2f}")

    return {
        "passed": not hard,
        "score": score,
        "threshold": threshold,
        "hard_rules": hard,
        "reasons": reasons,
        "trip": trip,
    }
