"""语义记忆：他是什么样的人、我认定过什么。

和 `cards`（发生过什么）互补：cards 是 episode，这里是 fact。
规矩和 fish-river 一致：**只追加**，更正/遗忘都是标记，不物理删。

一条事实必须带证据（`quote`），而且证据要能在当天的消息里找到——
模型编出来的"我记得你说过"一律丢弃。
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from difflib import SequenceMatcher

log = logging.getLogger("qqbot")

MAX_OBJECT = 60
MAX_QUOTE = 80
MAX_BLOCK_CHARS = 600
MAX_FACTS_IN_BLOCK = 6
CURSOR_KEY = "facts:last"
INTERVAL = 20 * 3600

# 这些谓词同一时间只留一条 active：新的把旧的顶掉
EXCLUSIVE = ("喜欢", "讨厌", "住在", "在做", "答应过", "怕", "想")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  subject          TEXT NOT NULL,
  predicate        TEXT NOT NULL,
  object           TEXT NOT NULL,
  quote            TEXT NOT NULL DEFAULT '',
  source_msg_id    TEXT,
  source_at        INTEGER NOT NULL,
  confidence       REAL NOT NULL DEFAULT 0.6,
  importance       REAL NOT NULL DEFAULT 0.5,
  status           TEXT NOT NULL DEFAULT 'active',
  superseded_by    INTEGER,
  created_at       INTEGER NOT NULL,
  updated_at       INTEGER NOT NULL,
  last_recalled_at INTEGER NOT NULL DEFAULT 0,
  recall_count     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject, status);
CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(status, importance DESC);

CREATE TABLE IF NOT EXISTS fact_events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  fact_id    INTEGER NOT NULL,
  action     TEXT NOT NULL,
  note       TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
  who        TEXT PRIMARY KEY,
  text       TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);
"""

EXTRACT_PROMPT = """你在帮「大肥鱼」整理对话里关于他的事实。

只记**长期有效**的事实：他的喜好、习惯、工作/学习、身体、关系、答应过的事。
不要记一次性的闲聊、情绪、玩笑、她说的话。

每条都要给出他原话里的证据（quote），必须是对话原文里的片段，不能改写、不能编。
没有就输出空数组。

只输出 JSON：
{{"facts": [{{"subject": "他", "predicate": "讨厌", "object": "香菜",
             "quote": "别放香菜", "confidence": 0.9, "importance": 0.6}}]}}

谓词尽量用这些：喜欢、讨厌、住在、在做、答应过、怕、想、养的、有。
object 不超过 {object_max} 字，quote 不超过 {quote_max} 字。

【最近的对话】
{dialog}
"""


def ensure(store) -> None:
    if getattr(store, "_facts_ready", False):
        return
    with store._lock:  # noqa: SLF001
        store._conn.executescript(_SCHEMA)  # noqa: SLF001
        store._conn.commit()  # noqa: SLF001
    store._facts_ready = True


def _now(now: int | None = None) -> int:
    return int(now if now is not None else time.time())


def due(store, settings, now: int | None = None) -> bool:
    if not getattr(settings, "memory_facts_enabled", False):
        return False
    stamp = _now(now)
    last = int(store.get_cursor(CURSOR_KEY, "0") or 0)
    return stamp - last >= INTERVAL


def mark(store, now: int | None = None) -> None:
    store.set_cursor(CURSOR_KEY, str(_now(now)))


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _bigrams(text: str) -> set[str]:
    body = _norm(text).lower()
    if len(body) < 2:
        return {body} if body else set()
    return {body[i : i + 2] for i in range(len(body) - 1)}


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _ago(seconds: int) -> str:
    days = max(0, seconds) // 86400
    if days == 0:
        return "今天"
    if days == 1:
        return "昨天"
    if days < 30:
        return f"{days} 天前"
    return f"{days // 30} 个月前"


def _row(row) -> dict:
    return dict(row) if row is not None else {}


def record(
    store,
    subject: str,
    predicate: str,
    obj: str,
    quote: str = "",
    source_msg_id: str | None = None,
    source_at: int | None = None,
    confidence: float = 0.6,
    importance: float | None = None,
    now: int | None = None,
) -> int:
    """写一条事实，返回 id。重复的只加置信度，矛盾的把旧的顶掉。"""
    ensure(store)
    subject = (subject or "他").strip()[:20]
    predicate = (predicate or "有").strip()[:10]
    obj = (obj or "").strip()[:MAX_OBJECT]
    if not obj:
        return 0
    quote = (quote or "").strip()[:MAX_QUOTE]
    stamp = _now(now)
    evidence_at = int(source_at if source_at is not None else stamp)
    importance = float(importance if importance is not None else 0.5)

    with store._lock:  # noqa: SLF001
        rows = store._conn.execute(  # noqa: SLF001
            "SELECT * FROM facts WHERE subject = ? AND predicate = ? AND status = 'active'",
            (subject, predicate),
        ).fetchall()
        for row in rows:
            old = _row(row)
            if _similar(old["object"], obj) >= 0.85:
                # 同一件事又说了一遍：只加置信度
                store._conn.execute(  # noqa: SLF001
                    "UPDATE facts SET confidence = MIN(1.0, confidence + 0.1), updated_at = ? WHERE id = ?",
                    (stamp, old["id"]),
                )
                store._conn.execute(  # noqa: SLF001
                    "INSERT INTO fact_events (fact_id, action, note, created_at) VALUES (?,?,?,?)",
                    (old["id"], "duplicate", "", stamp),
                )
                store._conn.commit()  # noqa: SLF001
                return int(old["id"])

        superseded = None
        if predicate in EXCLUSIVE:
            superseded = rows[0] if rows else None
        cur = store._conn.execute(  # noqa: SLF001
            "INSERT INTO facts (subject, predicate, object, quote, source_msg_id, source_at,"
            " confidence, importance, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (subject, predicate, obj, quote, source_msg_id, evidence_at,
             min(1.0, max(0.0, confidence)), min(1.0, max(0.0, importance)), stamp, stamp),
        )
        fact_id = int(cur.lastrowid or 0)
        store._conn.execute(  # noqa: SLF001
            "INSERT INTO fact_events (fact_id, action, note, created_at) VALUES (?,?,?,?)",
            (fact_id, "add", quote[:60], stamp),
        )
        if superseded is not None:
            store._conn.execute(  # noqa: SLF001
                "UPDATE facts SET status = 'superseded', superseded_by = ?, updated_at = ? WHERE id = ?",
                (fact_id, stamp, superseded["id"]),
            )
            store._conn.execute(  # noqa: SLF001
                "INSERT INTO fact_events (fact_id, action, note, created_at) VALUES (?,?,?,?)",
                (superseded["id"], "supersede", f"被 #{fact_id} 顶掉", stamp),
            )
        store._conn.commit()  # noqa: SLF001
    return fact_id


def correct(store, fact_id: int, new_object: str, quote: str = "", now: int | None = None) -> int:
    """更正一条事实：旧的标 superseded，新的接着写。"""
    ensure(store)
    with store._lock:  # noqa: SLF001
        row = store._conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    if row is None:
        return 0
    old = _row(row)
    return record(
        store,
        old["subject"],
        old["predicate"],
        new_object,
        quote=quote or old.get("quote") or "",
        source_msg_id=old.get("source_msg_id"),
        source_at=_now(now),
        confidence=0.9,
        importance=old.get("importance") or 0.5,
        now=now,
    )


def forget(store, fact_id: int, now: int | None = None) -> bool:
    ensure(store)
    stamp = _now(now)
    with store._lock:  # noqa: SLF001
        cur = store._conn.execute(  # noqa: SLF001
            "UPDATE facts SET status = 'forgotten', updated_at = ? WHERE id = ? AND status != 'forgotten'",
            (stamp, fact_id),
        )
        store._conn.execute(  # noqa: SLF001
            "INSERT INTO fact_events (fact_id, action, note, created_at) VALUES (?,?,?,?)",
            (fact_id, "forget", "", stamp),
        )
        store._conn.commit()  # noqa: SLF001
    return bool(cur.rowcount)


def get(store, fact_id: int) -> dict:
    ensure(store)
    with store._lock:  # noqa: SLF001
        row = store._conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    return _row(row)


def events(store, fact_id: int) -> list[dict]:
    ensure(store)
    with store._lock:  # noqa: SLF001
        rows = store._conn.execute(
            "SELECT * FROM fact_events WHERE fact_id = ? ORDER BY id", (fact_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def _score(row: dict, query: str, now: int) -> float:
    query_grams = _bigrams(query)
    fact_grams = _bigrams(f"{row['subject']}{row['predicate']}{row['object']}{row.get('quote') or ''}")
    overlap = len(query_grams & fact_grams) / max(1, len(query_grams))
    age = max(0, now - int(row.get("source_at") or now)) / 86400
    score = 1.2 * overlap
    score += 0.6 * float(row.get("importance") or 0.5)
    score += 0.5 * math.exp(-age / 90)
    score += 0.3 if int(row.get("recall_count") or 0) > 0 else 0.0
    if row.get("predicate") in EXCLUSIVE:
        score += 0.1
    return score


def recall(store, query: str = "", limit: int = MAX_FACTS_IN_BLOCK, now: int | None = None) -> list[dict]:
    ensure(store)
    stamp = _now(now)
    with store._lock:  # noqa: SLF001
        rows = store._conn.execute(
            "SELECT * FROM facts WHERE status = 'active' ORDER BY id DESC LIMIT 300"
        ).fetchall()
    scored = sorted(((_score(_row(row), query, stamp), _row(row)) for row in rows),
                    key=lambda item: item[0], reverse=True)
    picked = [row for _, row in scored[:limit]]
    if picked:
        with store._lock:  # noqa: SLF001
            for row in picked:
                store._conn.execute(  # noqa: SLF001
                    "UPDATE facts SET recall_count = recall_count + 1, last_recalled_at = ? WHERE id = ?",
                    (stamp, row["id"]),
                )
            store._conn.commit()  # noqa: SLF001
    return picked


def block(store, query: str = "", limit: int = MAX_FACTS_IN_BLOCK, max_chars: int = MAX_BLOCK_CHARS,
          now: int | None = None) -> str:
    """进 system prompt 的那段：带 #id、时间和原话。"""
    ensure(store)
    stamp = _now(now)
    rows = recall(store, query, limit=limit, now=stamp)
    if not rows:
        return ""
    lines = ["【我知道的事】（每条都能追到原话）"]
    for row in rows:
        when = _ago(stamp - int(row.get("source_at") or stamp))
        quote = (row.get("quote") or "").strip()
        tail = f"，他说“{quote}”" if quote else ""
        lines.append(f"- {row['subject']}{row['predicate']}{row['object']}（{when}{tail}）#f{row['id']}")
    text = "\n".join(lines)
    return text[:max_chars]


def profile_text(store, who: str = "他", limit: int = 8, max_chars: int = 300) -> str:
    """一个人物小档案：把关于他的事实压成一段，不调模型。"""
    ensure(store)
    with store._lock:  # noqa: SLF001
        rows = store._conn.execute(
            "SELECT * FROM facts WHERE subject = ? AND status = 'active' "
            "ORDER BY importance DESC, id DESC LIMIT ?",
            (who, limit),
        ).fetchall()
    if not rows:
        return ""
    parts = [f"{row['predicate']}{row['object']}" for row in rows]
    return (f"【{who}】" + "；".join(parts))[:max_chars]


def save_profile(store, who: str, text: str, now: int | None = None) -> None:
    ensure(store)
    with store._lock:  # noqa: SLF001
        store._conn.execute(  # noqa: SLF001
            "INSERT INTO profiles (who, text, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(who) DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at",
            (who, text[:600], _now(now)),
        )
        store._conn.commit()  # noqa: SLF001


def profile(store, who: str = "他") -> str:
    ensure(store)
    with store._lock:  # noqa: SLF001
        row = store._conn.execute("SELECT text FROM profiles WHERE who = ?", (who,)).fetchone()
    return str(row["text"]) if row is not None else ""


def stats(store) -> dict:
    ensure(store)
    with store._lock:  # noqa: SLF001
        rows = store._conn.execute(
            "SELECT status, COUNT(*) AS n FROM facts GROUP BY status"
        ).fetchall()
        subjects = store._conn.execute(
            "SELECT subject, COUNT(*) AS n FROM facts WHERE status = 'active' GROUP BY subject"
        ).fetchall()
    return {
        "by_status": {row["status"]: int(row["n"]) for row in rows},
        "by_subject": {row["subject"]: int(row["n"]) for row in subjects},
    }


def maintain(store, now: int | None = None, stale_days: int = 90, min_confidence: float = 0.4) -> int:
    """夜间维护：低置信度过期事实标 forgotten；重写人物档案。"""
    ensure(store)
    stamp = _now(now)
    stale_before = stamp - stale_days * 86400
    with store._lock:  # noqa: SLF001
        cur = store._conn.execute(  # noqa: SLF001
            "UPDATE facts SET status = 'forgotten', updated_at = ? "
            "WHERE status = 'active' AND confidence < ? AND source_at < ?",
            (stamp, min_confidence, stale_before),
        )
        store._conn.commit()  # noqa: SLF001
    with store._lock:  # noqa: SLF001
        subjects = store._conn.execute(
            "SELECT DISTINCT subject FROM facts WHERE status = 'active'"
        ).fetchall()
    for row in subjects:
        who = row["subject"]
        text = profile_text(store, who)
        if text:
            save_profile(store, who, text, now=stamp)
    return int(cur.rowcount)


def _parse(raw: str) -> list[dict]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```$", "", text).strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except (ValueError, TypeError):
            return []
    items = data.get("facts") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        obj = str(item.get("object") or "").strip()
        if not obj:
            continue
        result.append(
            {
                "subject": str(item.get("subject") or "他")[:20],
                "predicate": str(item.get("predicate") or "有")[:10],
                "object": obj[:MAX_OBJECT],
                "quote": str(item.get("quote") or "").strip()[:MAX_QUOTE],
                "confidence": float(item.get("confidence") or 0.6),
                "importance": float(item.get("importance") or 0.5),
            }
        )
    return result


def extract(store, llm, settings, now: int | None = None, hours: int = 24) -> list[int]:
    """从最近一天的对话里抽事实；证据找不到就丢。"""
    ensure(store)
    if not getattr(settings, "memory_facts_enabled", False):
        return []
    stamp = _now(now)
    rows = store.messages_between(stamp - hours * 3600, stamp + 1, limit=80)
    dialog = []
    evidence = []
    for row in rows:
        text = str(row.get("content") or "").strip()
        if not text or text.startswith("/"):
            continue
        role = "他" if row.get("role") == "user" else "我"
        dialog.append(f"{role}：{text[:120]}")
        if row.get("role") == "user":
            evidence.append((_norm(text), str(row.get("id") or ""), int(row.get("created_at") or stamp)))
    if not dialog:
        return []
    prompt = EXTRACT_PROMPT.format(
        object_max=MAX_OBJECT,
        quote_max=MAX_QUOTE,
        dialog="\n".join(dialog[-60:]),
    )
    result = llm.chat(
        [{"role": "system", "content": prompt}, {"role": "user", "content": "开始整理。"}],
        temperature=0.0,
        max_tokens=1200,
        purpose="facts",
    )
    written = []
    for fact in _parse(getattr(result, "text", "") or ""):
        quote = _norm(fact["quote"])
        source = None
        if quote:
            for text, msg_id, created in reversed(evidence):
                if quote in text:
                    source = (msg_id, created)
                    break
            if source is None:
                # 证据对不上原文：模型编的，不要
                log.debug("事实证据找不到，丢弃：%s", fact["quote"][:30])
                continue
        fact_id = record(
            store,
            fact["subject"],
            fact["predicate"],
            fact["object"],
            quote=fact["quote"],
            source_msg_id=source[0] if source else None,
            source_at=source[1] if source else stamp,
            confidence=fact["confidence"],
            importance=fact["importance"],
            now=stamp,
        )
        if fact_id:
            written.append(fact_id)
    mark(store, stamp)
    return written
