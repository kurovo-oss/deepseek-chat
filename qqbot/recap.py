"""前情摘要：窗口外的话不能就这么没了。

上下文有 token 上限，聊得久了最老的那些消息会被挤出窗口。直接丢掉，
过两天她说"我不记得了"——不是没记忆，是那段话根本没进过 prompt。

这里在丢掉之前先压成两三句留着：只留他提过的事、约好的时间、当时的情绪，
不带评价、不写成日记。摘要会作为【前面聊过的】拼进 system prompt。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("qqbot")

RECENT_KEEP = 40  # 最近这么多条永远不摘要（大致就是上下文窗口大小）
MIN_PENDING = 12  # 少于这么多条不值得专门调一次模型
MAX_CHARS = 160  # 摘要本身别长，它只是提词
STALE_SECONDS = 3 * 86400  # 三天没更新就不注入了，那是过期的前情


@dataclass
class Recap:
    upto_id: int = 0
    text: str = ""
    at: float = 0.0


def _prompt(settings: Any) -> str:
    path = Path(settings.base_dir) / "prompts" / "recap.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return "把下面的对话压成两三句：只留他提过的事、约好的时间、当时的情绪。"


def _path(settings: Any) -> Path:
    return Path(settings.base_dir) / "data" / "recap.json"


def _load(settings: Any) -> dict[str, dict]:
    path = _path(settings)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save(settings: Any, data: dict[str, dict]) -> None:
    path = _path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def get(settings: Any, session: str) -> Recap:
    item = _load(settings).get(str(session))
    if not isinstance(item, dict):
        return Recap()
    try:
        return Recap(
            upto_id=int(item.get("upto_id") or 0),
            text=str(item.get("text") or ""),
            at=float(item.get("at") or 0.0),
        )
    except (TypeError, ValueError):
        return Recap()


def put(settings: Any, session: str, recap: Recap) -> None:
    data = _load(settings)
    data[str(session)] = asdict(recap)
    _save(settings, data)


def note(settings: Any, session: str, now: float | None = None) -> str:
    """给 prompt 用的那段。太久没更新就不提了。"""
    recap = get(settings, session)
    if not recap.text.strip():
        return ""
    stamp = now if now is not None else time.time()
    if recap.at and stamp - recap.at > STALE_SECONDS:
        return ""
    return f"【前面聊过的】{recap.text.strip()}"


def pending(rows: list[dict], upto_id: int, keep: int = RECENT_KEEP) -> list[dict]:
    """窗口外的、还没摘要过的消息（从旧到新）。"""
    older = rows[:-keep] if keep > 0 and len(rows) > keep else []
    return [row for row in older if int(row.get("id") or 0) > upto_id]


def transcript(rows: list[dict], limit: int = 1600) -> str:
    """把若干条消息拼成给模型看的对话稿，太长就留最近的。"""
    lines = []
    for row in rows:
        who = "他" if row.get("role") == "user" else "你"
        text = str(row.get("content") or "").strip().replace("\n", " / ")
        if text:
            lines.append(f"{who}：{text}")
    text = "\n".join(lines)
    if len(text) > limit:
        text = "…" + text[-limit:]
    return text


def trim(text: str, limit: int = MAX_CHARS) -> str:
    flat = " ".join(str(text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def tick(engine: Any, session: str, now: float | None = None) -> str | None:
    """如果窗口外积了足够多的旧话，压成一段前情。返回新摘要（没更新就是 None）。

    调模型，只放在后台线程里跑，别挡回复。
    """
    settings = engine.settings
    rows = engine.store.recent_messages(session, RECENT_KEEP + 120)
    recap = get(settings, session)
    todo = pending(rows, recap.upto_id)
    if len(todo) < MIN_PENDING:
        return None
    text = transcript(todo)
    if not text:
        return None
    try:
        # 给的输出额度要比摘要本身大得多：这个模型会先花 token 想，想完才写
        result = engine.llm.chat(
            [
                {"role": "system", "content": _prompt(settings)},
                {"role": "user", "content": text},
            ],
            temperature=0.3,
            max_tokens=2000,
        )
    except Exception as exc:  # noqa: BLE001 - 摘要失败不能影响别的事
        log.warning("前情摘要失败：%s", exc)
        return None
    summary = trim(" ".join(str(getattr(result, "text", result) or "").split()))
    if not summary:
        # 模型一个字没写出来（想太多把额度用完了）——别记成空，下次再试
        log.warning("前情摘要为空，先不记")
        return None
    stamp = now if now is not None else time.time()
    put(settings, session, Recap(upto_id=int(todo[-1].get("id") or 0), text=summary, at=stamp))
    log.info("前情摘要更新：%d 条 → %d 字", len(todo), len(summary))
    return summary
