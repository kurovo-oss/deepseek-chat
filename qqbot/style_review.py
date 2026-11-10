"""相处方式建议：把反馈和偏好整理成最多三条可执行的规则。

只生成建议，不自动改人格；主人点“采用”才落成一张已确认的偏好卡。
"""

from __future__ import annotations

import logging
import time
from difflib import SequenceMatcher
from pathlib import Path

from . import memory_epoch
from .memory import load_json_object

log = logging.getLogger("qqbot")

CURSOR_KEY = "style:last_review"
MAX_RULES = 3
MAX_CHARS = 60
DEDUPE_RATIO = 0.82


def due(store, settings, now: int | None = None) -> bool:
    if not getattr(settings, "style_review_enabled", False):
        return False
    stamp = int(now if now is not None else time.time())
    try:
        last = int(store.get_cursor(CURSOR_KEY, "0") or 0)
    except (TypeError, ValueError):
        last = 0
    days = max(1, int(getattr(settings, "style_review_days", 7)))
    return stamp - last >= days * 86400


def _prompt(settings) -> str:
    path = Path(getattr(settings, "style_review_prompt_file", "prompts/style_review.md"))
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return '只输出 JSON：{"rules":[]}'


def _format_feedback(rows: list[dict]) -> str:
    lines = []
    for row in rows:
        note = str(row.get("note") or "").strip()
        if note:
            lines.append(f"{row.get('rating')}：{note[:120]}")
    return "\n".join(lines) or "（还没有具体反馈）"


def _format_preferences(cards: list[dict]) -> str:
    return "\n".join(
        f"- {card.get('summary', '')}" for card in cards if card.get("summary")
    ) or "（还没有偏好卡）"


def _format_messages(rows: list[dict]) -> str:
    lines = []
    for row in rows:
        text = str(row.get("content") or "").replace("\n", " ")[:120]
        if not text or text.startswith("/"):
            continue
        who = "他" if row.get("role") == "user" else "我"
        lines.append(f"{who}：{text}")
    return "\n".join(lines) or "（最近没有聊天）"


def _rules(raw: str, existing: list[str]) -> list[str]:
    data = load_json_object(raw)
    items = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    result: list[str] = []
    seen = [str(text or "") for text in existing]
    for item in items:
        text = " ".join(str(item or "").split())
        if not text or len(text) > MAX_CHARS:
            continue
        if any(SequenceMatcher(None, text, old).ratio() >= DEDUPE_RATIO for old in seen):
            continue
        result.append(text)
        seen.append(text)
        if len(result) >= MAX_RULES:
            break
    return result


def review(store, llm, settings, now: int | None = None) -> list[dict]:
    stamp = int(now if now is not None else time.time())
    cutoff = memory_epoch.read(settings.base_dir)
    feedback = store.list_feedback(40)
    preferences = [
        card
        for card in store.list_cards(status="active", limit=80)
        if card.get("confirmed", True) and card.get("kind") == "preference"
    ]
    messages = store.recent_messages(str(settings.allow_user_id), 30)
    if cutoff:
        messages = [row for row in messages if int(row.get("created_at") or 0) >= cutoff]
    materials = [row for row in feedback if str(row.get("note") or "").strip()]
    if not materials and not preferences:
        store.set_cursor(CURSOR_KEY, str(stamp))
        return []

    prompt = (
        _prompt(settings)
        .replace("{{FEEDBACK}}", _format_feedback(feedback))
        .replace("{{PREFERENCES}}", _format_preferences(preferences))
        .replace("{{MESSAGES}}", _format_messages(messages))
    )
    try:
        result = llm.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=1000,
            purpose="style_review",
        )
        raw = getattr(result, "text", "") or ""
    except Exception:  # noqa: BLE001 - 复盘失败不影响聊天
        log.exception("相处方式整理失败")
        store.set_cursor(CURSOR_KEY, str(stamp))
        return []

    existing = [str(card.get("summary") or "") for card in preferences]
    evidence = [int(row["id"]) for row in feedback if row.get("id")]
    created = []
    for text in _rules(raw, existing):
        suggestion_id = store.add_style_suggestion(text, evidence=evidence[:6], created_at=stamp)
        item = store.get_style_suggestion(suggestion_id)
        if item:
            created.append(item)
    store.set_cursor(CURSOR_KEY, str(stamp))
    if created:
        log.info("相处方式：%d 条建议", len(created))
    return created


def apply(store, suggestion_id: int) -> dict:
    item = store.get_style_suggestion(int(suggestion_id))
    if item is None:
        raise ValueError("这条建议已经不在了")
    if item.get("status") != "suggested":
        raise ValueError("这条建议已经处理过了")
    card_id = store.add_card(
        kind="preference",
        summary=item["text"],
        detail="来自相处方式整理",
        importance=0.85,
        confirmed=True,
    )
    store.update_style_suggestion(int(suggestion_id), "applied")
    return {"ok": True, "card_id": card_id, "item": store.get_style_suggestion(int(suggestion_id))}


def dismiss(store, suggestion_id: int) -> dict:
    item = store.get_style_suggestion(int(suggestion_id))
    if item is None:
        raise ValueError("这条建议已经不在了")
    if item.get("status") != "suggested":
        raise ValueError("这条建议已经处理过了")
    store.update_style_suggestion(int(suggestion_id), "dismissed")
    return {"ok": True, "item": store.get_style_suggestion(int(suggestion_id))}


def tick(store, llm, settings, now: int | None = None) -> list[dict]:
    if not due(store, settings, now):
        return []
    return review(store, llm, settings, now)
