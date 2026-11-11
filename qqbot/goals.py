"""候选目标：她提出“接下来想做什么”，但第一阶段只建议、不执行。

目标必须来自已确认的记忆/原话，不能凭空写“我想去旅行”。每天最多复盘
一次，最多写两条；真正的执行由后续阶段的 policy 和 executor 接管。
"""

from __future__ import annotations

import logging
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

from . import memory_epoch
from .memory import load_json_object

log = logging.getLogger("qqbot")

CURSOR_KEY = "goals:last_review"
KINDS = {"relationship", "self", "curiosity", "routine", "shared"}
HORIZONS = {"day", "week", "month"}
MAX_TITLE = 40
MAX_WHY = 80
DEDUPE_RATIO = 0.82


def _title_norm(text: str) -> str:
    return re.sub(r"[\s，。！？、…,.!?~～]+", "", str(text or "").lower())


def _prompt(settings) -> str:
    path = Path(getattr(settings, "goals_prompt_file", "prompts/goals.md"))
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return "只输出 JSON：{{\"goals\":[]}}"


def due(store, settings, now: int | None = None) -> bool:
    if not getattr(settings, "goals_enabled", False):
        return False
    stamp = int(now if now is not None else time.time())
    try:
        last = int(store.get_cursor(CURSOR_KEY, "0") or 0)
    except (TypeError, ValueError):
        last = 0
    return stamp - last >= max(1, int(getattr(settings, "goals_review_hours", 20))) * 3600


def _format_cards(cards: list[dict]) -> str:
    return "\n".join(
        f"{card['id']}. [{card.get('kind', 'fact')}] {card.get('summary', '')}"
        for card in cards
    ) or "（还没有已确认的卡片）"


def _format_goals(goals: list[dict]) -> str:
    return "\n".join(
        f"{goal['id']}. [{goal.get('status')}] {goal.get('title', '')} — {goal.get('why', '')}"
        for goal in goals
    ) or "（还没有目标）"


def _format_messages(rows: list[dict]) -> str:
    lines = []
    for row in rows:
        text = str(row.get("content") or "").replace("\n", " ")[:160]
        if not text or text.startswith("/"):
            continue
        who = "他" if row.get("role") == "user" else "我"
        lines.append(f"{who}：{text}")
    return "\n".join(lines) or "（最近没有聊天）"


def _format_feedback(rows: list[dict]) -> str:
    lines = []
    for row in rows:
        note = str(row.get("note") or "").strip()
        if note:
            lines.append(f"{row.get('rating')}：{note[:120]}")
    return "\n".join(lines) or "（还没有具体反馈）"


def _evidence(value, valid_ids: set[int]) -> list[int]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number in valid_ids and number not in result:
            result.append(number)
        if len(result) >= 6:
            break
    return result


def _parse(raw: str, valid_ids: set[int], existing: list[dict]) -> list[dict]:
    data = load_json_object(raw)
    if not data:
        return []
    items = data.get("goals")
    if not isinstance(items, list):
        return []
    result: list[dict] = []
    seen_titles = [_title_norm(goal.get("title", "")) for goal in existing]
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        why = str(item.get("why") or "").strip()
        kind = str(item.get("kind") or "").strip()
        horizon = str(item.get("horizon") or "week").strip()
        evidence = _evidence(item.get("evidence"), valid_ids)
        if not title or len(title) > MAX_TITLE or len(why) > MAX_WHY:
            continue
        if kind not in KINDS or horizon not in HORIZONS or not evidence:
            continue
        try:
            priority = max(0.0, min(1.0, float(item.get("priority", 0.5))))
        except (TypeError, ValueError):
            priority = 0.5
        try:
            due_hours = int(item.get("due_hours"))
            if not 1 <= due_hours <= 168:
                due_hours = None
        except (TypeError, ValueError):
            due_hours = None
        norm = _title_norm(title)
        if any(SequenceMatcher(None, norm, old).ratio() >= DEDUPE_RATIO for old in seen_titles):
            continue
        seen_titles.append(norm)
        result.append(
            {
                "kind": kind,
                "title": title,
                "why": why,
                "horizon": horizon,
                "priority": priority,
                "evidence": evidence,
                "due_hours": due_hours,
            }
        )
        if len(result) >= 2:
            break
    return result


def review(store, llm, settings, now: int | None = None) -> list[dict]:
    """跑一次目标复盘，写入候选目标，返回新增的卡片化目标。只建议，不执行。"""
    stamp = int(now if now is not None else time.time())
    cutoff = memory_epoch.read(settings.base_dir)
    cards = [
        card
        for card in store.list_cards(status="active", limit=60)
        if card.get("confirmed", True)
    ][:40]
    messages = store.recent_messages(str(settings.allow_user_id), 24)
    if cutoff:
        messages = [row for row in messages if int(row.get("created_at") or 0) >= cutoff]
    feedback = store.list_feedback(12)
    existing = store.list_goals(limit=30)

    if not cards and not messages:
        store.set_cursor(CURSOR_KEY, str(stamp))
        return []

    prompt = (
        _prompt(settings)
        .replace("{{GOALS}}", _format_goals(existing))
        .replace("{{CARDS}}", _format_cards(cards))
        .replace("{{MESSAGES}}", _format_messages(messages))
        .replace("{{FEEDBACK}}", _format_feedback(feedback))
    )
    try:
        result = llm.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=1200,
            purpose="goals",
        )
        raw = getattr(result, "text", "") or ""
    except Exception:  # noqa: BLE001 - 目标复盘失败不影响聊天
        log.exception("目标复盘失败")
        store.set_cursor(CURSOR_KEY, str(stamp))
        return []

    valid_ids = {int(card["id"]) for card in cards}
    parsed = _parse(raw, valid_ids, existing)[: max(0, int(getattr(settings, "goals_max_candidates", 2)))]
    created: list[dict] = []
    for item in parsed:
        goal_id = store.add_goal(
            kind=item["kind"],
            title=item["title"],
            why=item["why"],
            horizon=item["horizon"],
            priority=item["priority"],
            status="candidate",
            source="reflection",
            evidence=item["evidence"],
            confidence=0.6,
            due_at=stamp + item["due_hours"] * 3600 if item.get("due_hours") else None,
            created_at=stamp,
        )
        goal = store.get_goal(goal_id)
        if goal:
            created.append(goal)
    store.set_cursor(CURSOR_KEY, str(stamp))
    if created:
        log.info("目标复盘：新增 %d 个候选目标", len(created))
    return created


def _recent_sent_action(store, now: int, hours: int = 48) -> dict | None:
    """最近一次真正发出去的目标动作。"""
    for action in store.list_goal_actions(status="done", limit=40):
        if action.get("action") != "send_message" or action.get("result") != "sent":
            continue
        if int(action.get("created_at") or 0) < now - hours * 3600:
            continue
        return action
    return None


def record_user_reply(store, now: int | None = None) -> dict | None:
    """他回话后给最近一次目标主动记一个 replied，不猜情绪。"""
    stamp = int(now if now is not None else time.time())
    action = _recent_sent_action(store, stamp)
    if action is None:
        return None
    if any(row.get("signal") == "replied" for row in store.list_outcomes(action["id"], limit=10)):
        return None
    store.add_outcome(int(action["id"]), "replied", created_at=stamp)
    return store.goal_progress_delta(int(action.get("goal_id") or 0), 0.1, now=stamp)


def record_feedback(store, rating: str, now: int | None = None) -> dict | None:
    """把「合拍/不准确」也记到最近一次目标主动上；只作为弱信号。"""
    if rating not in ("good", "bad"):
        return None
    stamp = int(now if now is not None else time.time())
    action = _recent_sent_action(store, stamp, hours=6)
    if action is None:
        return None
    signal = "feedback_good" if rating == "good" else "feedback_bad"
    if any(row.get("signal") == signal for row in store.list_outcomes(action["id"], limit=10)):
        return None
    store.add_outcome(int(action["id"]), signal, created_at=stamp)
    return store.goal_progress_delta(
        int(action.get("goal_id") or 0), 0.1 if rating == "good" else -0.1, now=stamp
    )


def context_block(store, limit: int = 4, max_chars: int = 280) -> str:
    """给正常对话用的目标摘要；只带进行中的目标，不带候选/已删除。"""
    goals = store.list_goals(status="active", limit=max(1, int(limit)))
    if not goals:
        return ""
    lines = ["【你现在在做的事】"]
    for goal in goals:
        progress = int(round(float(goal.get("progress") or 0) * 100))
        why = str(goal.get("why") or "").strip()
        line = f"- {goal.get('title', '')}"
        if why:
            line += f"（{why}）"
        line += f" 进度 {progress}%"
        lines.append(line)
    text = "\n".join(lines)
    return text[:max_chars]


def tick(store, llm, settings, now: int | None = None) -> list[dict]:
    if not due(store, settings, now):
        return []
    return review(store, llm, settings, now)
