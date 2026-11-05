"""她的事件流：只追加，人格从这里长出来。

借 fish-river 的两条硬规矩：
1. **历史只能追加。** 已经发生的事不改，只能被后来的事推翻（`turn`）。
2. **记忆有重量。** 普通的会淡忘，反复验证过的才固化成人格（`beliefs`）。

这里不存聊天流水——流水在 `messages` 表里，滚动的，不进人格。
"""

from __future__ import annotations

import logging
import re
import time
from difflib import SequenceMatcher

log = logging.getLogger("qqbot")

KINDS = ("said", "self", "refuse", "reflect", "turn")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS growth (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at  INTEGER NOT NULL,
  kind        TEXT NOT NULL,
  text        TEXT NOT NULL,
  salience    REAL NOT NULL DEFAULT 0.5,
  source      TEXT NOT NULL DEFAULT 'owner',
  ref         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_growth_kind ON growth(kind, id);
CREATE TABLE IF NOT EXISTS refusals (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at  INTEGER NOT NULL,
  ask         TEXT NOT NULL,
  outcome     TEXT NOT NULL DEFAULT '',
  note        TEXT NOT NULL DEFAULT '',
  settled_at  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_refusals_open ON refusals(outcome, id);
"""

def ensure(store) -> None:
    """建表（幂等）。记在 store 自己身上：按 id 缓存会撞上回收后的新对象。"""
    if getattr(store, "_growth_ready", False):
        return
    with store._lock:  # noqa: SLF001 - 同包内部，共用一把锁
        store._conn.executescript(_SCHEMA)  # noqa: SLF001
        store._conn.commit()  # noqa: SLF001
    try:
        setattr(store, "_growth_ready", True)
    except AttributeError:
        pass


def _now(now: int | None) -> int:
    return int(now if now is not None else time.time())


def record(
    store,
    kind: str,
    text: str,
    salience: float = 0.5,
    source: str = "owner",
    ref: str = "",
    now: int | None = None,
) -> int:
    """追加一条。只加不改——这是她能"长"的唯一入口。"""
    ensure(store)
    text = (text or "").strip()[:400]
    if not text:
        return 0
    kind = kind if kind in KINDS else "said"
    salience = max(0.0, min(1.0, float(salience)))
    with store._lock:  # noqa: SLF001
        cur = store._conn.execute(  # noqa: SLF001
            "INSERT INTO growth (created_at, kind, text, salience, source, ref)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (_now(now), kind, text, salience, source, ref),
        )
        store._conn.commit()  # noqa: SLF001
        return int(cur.lastrowid or 0)


def recent(
    store, kind: str | None = None, limit: int = 20, since: int | None = None
) -> list[dict]:
    ensure(store)
    sql = "SELECT * FROM growth WHERE 1 = 1"
    args: list = []
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    if since:
        sql += " AND created_at >= ?"
        args.append(int(since))
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, int(limit)))
    with store._lock:  # noqa: SLF001
        rows = store._conn.execute(sql, args).fetchall()  # noqa: SLF001
    return [dict(row) for row in rows]


def _norm(text: str) -> str:
    return re.sub(r"[\s，。！？、…,.!?~～]+", "", (text or "").lower())


def _similar(left: str, right: str) -> float:
    return SequenceMatcher(None, _norm(left), _norm(right)).ratio()


def self_view(store, limit: int = 4, max_chars: int = 100) -> list[str]:
    """她最近想通的、或者被推翻后改口的那些话。"""
    rows = recent(store, limit=60)
    picked: list[str] = []
    for row in rows:
        if row["kind"] not in ("self", "turn") or row["salience"] < 0.55:
            continue
        text = str(row["text"]).strip()
        if not text:
            continue
        if any(_similar(text, other) >= 0.7 for other in picked):
            continue
        picked.append(text[:max_chars])
        if len(picked) >= limit:
            break
    return list(reversed(picked))


def beliefs(store, min_count: int = 2, limit: int = 3, max_chars: int = 90) -> list[str]:
    """被自己说过两次以上的，才算固化下来的认知。"""
    rows = recent(store, limit=200)
    texts: list[tuple[str, str]] = []
    for row in rows:
        if row["kind"] not in ("self", "turn"):
            continue
        text = str(row["text"]).strip()
        if text:
            texts.append((text, _norm(text)))
    result: list[str] = []
    seen: list[str] = []
    for text, norm in texts:
        count = sum(1 for other, other_norm in texts if _similar(norm, other_norm) >= 0.8)
        if count < min_count:
            continue
        if any(_similar(text, other) >= 0.8 for other in seen):
            continue
        seen.append(text)
        result.append(text[:max_chars])
        if len(result) >= limit:
            break
    return result


def persona_block(store, budget: int = 300) -> str:
    """进上下文的那一小段：她"是谁"里靠经历长出来的部分。"""
    lines: list[str] = []
    fixed = beliefs(store)
    fresh = self_view(store)
    if fixed:
        lines.append("【你已经认定的事】")
        # 90 字和 100 字截断后字符串不等，得按相似度去重
        lines.extend(
            f"- {item}"
            for item in fixed
            if not any(_similar(item, other) >= 0.8 for other in fresh)
        )
    if fresh:
        lines.append("【你最近想通的】")
        lines.extend(f"- {item}" for item in fresh)
    if not lines:
        return ""
    text = "\n".join(lines)
    return text[:budget]


def maintain(
    store, now: int | None = None, keep_days: int = 120, max_events: int = 5000
) -> int:
    """淡忘：老的低显著事件删掉；`self`/`turn` 是认知，留着。"""
    ensure(store)
    stamp = _now(now)
    cutoff = stamp - keep_days * 86400
    removed = 0
    with store._lock:  # noqa: SLF001
        cur = store._conn.execute(  # noqa: SLF001
            "DELETE FROM growth WHERE created_at < ? AND salience < 0.7"
            " AND kind NOT IN ('self', 'turn')",
            (cutoff,),
        )
        removed += int(cur.rowcount or 0)
        total = store._conn.execute("SELECT COUNT(*) FROM growth").fetchone()[0]  # noqa: SLF001
        if total > max_events:
            extra = total - max_events
            cur = store._conn.execute(  # noqa: SLF001
                "DELETE FROM growth WHERE id IN ("
                " SELECT id FROM growth WHERE kind NOT IN ('self', 'turn')"
                " ORDER BY salience ASC, id ASC LIMIT ?)",
                (extra,),
            )
            removed += int(cur.rowcount or 0)
        store._conn.commit()  # noqa: SLF001
    if removed:
        log.info("事件流淡忘：清掉 %d 条旧事件", removed)
    return removed


# ---------------- 拒绝记录 ----------------

def open_refusal(store, ask: str, now: int | None = None) -> int:
    """记下"我拒绝了他这件事"，等着看他反应。"""
    ensure(store)
    stamp = _now(now)
    with store._lock:  # noqa: SLF001
        cur = store._conn.execute(  # noqa: SLF001
            "INSERT INTO refusals (created_at, ask) VALUES (?, ?)",
            (stamp, (ask or "")[:120]),
        )
        store._conn.commit()  # noqa: SLF001
        refusal_id = int(cur.lastrowid or 0)
    if refusal_id:
        # 事件流里也留一笔：夜间反省读得到"我今天推掉了他一件事"
        record(
            store,
            "refuse",
            f"我拒绝了他：{(ask or '').strip()[:80]}",
            salience=0.6,
            source="her",
            ref=f"refusal:{refusal_id}",
            now=stamp,
        )
    return refusal_id


def last_open_refusal(store) -> dict | None:
    ensure(store)
    with store._lock:  # noqa: SLF001
        row = store._conn.execute(  # noqa: SLF001
            "SELECT * FROM refusals WHERE outcome = '' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def close_refusal(store, refusal_id: int, outcome: str, note: str = "", now: int | None = None) -> None:
    ensure(store)
    with store._lock:  # noqa: SLF001
        store._conn.execute(  # noqa: SLF001
            "UPDATE refusals SET outcome = ?, note = ?, settled_at = ? WHERE id = ?",
            (outcome[:20], (note or "")[:80], _now(now), int(refusal_id)),
        )
        store._conn.commit()  # noqa: SLF001


def recent_refusal_outcomes(store, limit: int = 5) -> list[str]:
    ensure(store)
    with store._lock:  # noqa: SLF001
        rows = store._conn.execute(  # noqa: SLF001
            "SELECT outcome FROM refusals WHERE outcome != '' ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
    return [str(row["outcome"]) for row in rows]


def recent_refusals(store, limit: int = 5) -> list[dict]:
    ensure(store)
    with store._lock:  # noqa: SLF001
        rows = store._conn.execute(  # noqa: SLF001
            "SELECT * FROM refusals ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)
        ).fetchall()
    return [dict(row) for row in rows]


def count(store) -> int:
    ensure(store)
    with store._lock:  # noqa: SLF001
        row = store._conn.execute("SELECT COUNT(*) FROM growth").fetchone()  # noqa: SLF001
    return int(row[0] or 0)
