"""SQLite 存储层。

表结构见 docs/MVP.md §5.1。这里一次性建好全部表，避免后续迁移；
只实现当前里程碑需要的读写方法。
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id          INTEGER PRIMARY KEY,
  session_id  TEXT NOT NULL,
  qq_msg_id   TEXT UNIQUE,
  role        TEXT NOT NULL,
  content     TEXT NOT NULL,
  send_state  TEXT NOT NULL DEFAULT 'sent',
  image_tags  TEXT NOT NULL DEFAULT '[]',
  origin      TEXT NOT NULL DEFAULT 'reply',
  attempts    INTEGER NOT NULL DEFAULT 0,
  parts_json  TEXT NOT NULL DEFAULT '[]',
  sent_parts  INTEGER NOT NULL DEFAULT 0,
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS memories (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,
  content     TEXT NOT NULL,
  source_from INTEGER,
  source_to   INTEGER,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
  id           INTEGER PRIMARY KEY,
  kind         TEXT NOT NULL,
  summary      TEXT NOT NULL,
  detail       TEXT NOT NULL DEFAULT '',
  entities     TEXT NOT NULL DEFAULT '[]',
  occurred_at  INTEGER,
  importance   REAL NOT NULL DEFAULT 0.5,
  pinned       INTEGER NOT NULL DEFAULT 0,
  hit_count    INTEGER NOT NULL DEFAULT 0,
  last_hit_at  INTEGER,
  source_from  INTEGER,
  source_to    INTEGER,
  status       TEXT NOT NULL DEFAULT 'active',
  confirmed    INTEGER NOT NULL DEFAULT 1,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cards_kind   ON cards(kind, status);
CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status);
CREATE INDEX IF NOT EXISTS idx_cards_source ON cards(source_from, source_to);

CREATE TABLE IF NOT EXISTS feedback (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id  INTEGER,
  rating      TEXT NOT NULL,
  note        TEXT NOT NULL DEFAULT '',
  card_id     INTEGER,
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_message ON feedback(message_id);
CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at);

CREATE TABLE IF NOT EXISTS goals (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,
  title       TEXT NOT NULL,
  why         TEXT NOT NULL DEFAULT '',
  horizon     TEXT NOT NULL DEFAULT 'week',
  priority    REAL NOT NULL DEFAULT 0.5,
  status      TEXT NOT NULL DEFAULT 'candidate',
  source      TEXT NOT NULL DEFAULT 'reflection',
  evidence    TEXT NOT NULL DEFAULT '[]',
  confidence  REAL NOT NULL DEFAULT 0.5,
  due_at      INTEGER,
  review_at   INTEGER,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status, priority);
CREATE INDEX IF NOT EXISTS idx_goals_due ON goals(due_at);

CREATE TABLE IF NOT EXISTS goal_actions (
  id          INTEGER PRIMARY KEY,
  goal_id     INTEGER,
  action      TEXT NOT NULL,
  payload     TEXT NOT NULL DEFAULT '{}',
  run_at      INTEGER,
  status      TEXT NOT NULL DEFAULT 'pending',
  approval    TEXT NOT NULL DEFAULT 'auto',
  attempts    INTEGER NOT NULL DEFAULT 0,
  result      TEXT NOT NULL DEFAULT '',
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_goal_actions_status ON goal_actions(status, run_at);

CREATE TABLE IF NOT EXISTS outcomes (
  id          INTEGER PRIMARY KEY,
  action_id   INTEGER,
  signal      TEXT NOT NULL,
  note        TEXT NOT NULL DEFAULT '',
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outcomes_action ON outcomes(action_id);

CREATE TABLE IF NOT EXISTS proactive_events (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,
  content     TEXT NOT NULL,
  trigger_at  INTEGER NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',
  attempts    INTEGER NOT NULL DEFAULT 0,
  created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_state (
  key         TEXT PRIMARY KEY,
  value       REAL NOT NULL,
  updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS dream_log (
  id             INTEGER PRIMARY KEY,
  started_at     INTEGER NOT NULL,
  finished_at    INTEGER,
  messages_seen  INTEGER NOT NULL DEFAULT 0,
  cards_added    INTEGER NOT NULL DEFAULT 0,
  cards_merged   INTEGER NOT NULL DEFAULT 0,
  cards_reweight INTEGER NOT NULL DEFAULT 0,
  journal_chars  INTEGER NOT NULL DEFAULT 0,
  ok             INTEGER NOT NULL DEFAULT 1,
  note           TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS image_notes (
  sha256      TEXT PRIMARY KEY,
  note        TEXT NOT NULL,
  created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS images (
  id            INTEGER PRIMARY KEY,
  sha256        TEXT NOT NULL UNIQUE,
  rel_path      TEXT NOT NULL,
  mime          TEXT NOT NULL,
  byte_size     INTEGER NOT NULL,
  tags          TEXT NOT NULL DEFAULT '[]',
  manual        INTEGER NOT NULL DEFAULT 0,
  source        TEXT NOT NULL,
  source_msg_id TEXT,
  status        TEXT NOT NULL DEFAULT 'active',
  use_count     INTEGER NOT NULL DEFAULT 0,
  last_used_at  INTEGER,
  created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS summaries (
  id          INTEGER PRIMARY KEY,
  covers_from INTEGER NOT NULL,
  covers_to   INTEGER NOT NULL,
  content     TEXT NOT NULL,
  created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_log (
  id         INTEGER PRIMARY KEY,
  model      TEXT NOT NULL,
  tokens_in  INTEGER NOT NULL,
  tokens_out INTEGER NOT NULL,
  purpose    TEXT NOT NULL,
  ok         INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cursors (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  content, content='messages', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
  INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
"""

_FTS_SPECIAL = re.compile(r'["*():^-]')
_SPLIT = re.compile(r"[\s,，。！？、；：'\"“”‘’（）()\[\]【】{}<>]+")
_WORD_RUN = re.compile(r"[0-9A-Za-z_]+|[\u4e00-\u9fff]+")


def trigrams(text: str, limit: int = 12) -> list[str]:
    """切出 FTS5 trigram 分词器能匹配的片段。

    trigram 只认 3 个字以上的子串，2 个字的词召不回来——宁可少召回，
    也不要为了两个字的词去全表 LIKE（历史表会越滚越大）。
    """
    terms: list[str] = []
    seen: set[str] = set()
    for run in _WORD_RUN.findall(text or ""):
        if len(run) < 3:
            continue
        pieces = [run] if len(run) == 3 else [run[i : i + 3] for i in range(len(run) - 2)]
        for piece in pieces:
            if piece in seen:
                continue
            seen.add(piece)
            terms.append(piece)
            if len(terms) >= limit:
                return terms
    return terms
_LEGACY_KIND = {"fact": "fact", "preference": "preference", "event": "event"}
_CARD_UPDATABLE = frozenset(
    {"kind", "summary", "detail", "entities", "occurred_at", "importance", "status", "updated_at"}
)
_GOAL_UPDATABLE = frozenset(
    {"kind", "title", "why", "horizon", "priority", "status", "evidence", "confidence", "due_at", "review_at", "updated_at"}
)


def bigrams(text: str, limit: int = 12) -> list[str]:
    """两字片段，给 FTS 兜底用：trigram 索引不到 2 个字的词。

    "香菜""火锅""加班" 这类短词是日常最常出现的，只靠 trigram 会全部漏掉。
    """
    terms: list[str] = []
    seen: set[str] = set()
    for run in _WORD_RUN.findall(text or ""):
        if len(run) < 2:
            continue
        pieces = [run] if len(run) == 2 else [run[i : i + 2] for i in range(len(run) - 1)]
        for piece in pieces:
            if piece in seen:
                continue
            seen.add(piece)
            terms.append(piece)
            if len(terms) >= limit:
                return terms
    return terms


def sanitize_fts_query(query: str) -> str:
    """把用户原句变成安全的 FTS5 查询串。

    FTS5 对 `- * ( ) "` 等字符敏感，直接拼会抛 syntax error；
    这里按标点切词、逐词加引号、OR 连接，天然规避注入。
    """
    cleaned = _FTS_SPECIAL.sub(" ", query or "")
    words = [w for w in _SPLIT.split(cleaned) if len(w) >= 2]
    if not words:
        return ""
    return " OR ".join(f'"{w}"' for w in words)


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self.fts_enabled = self._try_enable_fts()
            self._conn.commit()

    def _migrate(self) -> None:
        """给老库补列，避免升级后直接报错。"""
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        if "image_tags" not in columns:
            self._conn.execute(
                "ALTER TABLE messages ADD COLUMN image_tags TEXT NOT NULL DEFAULT '[]'"
            )
        if "origin" not in columns:
            # reply=回复用户，proactive=她先开口（失败不重放，重放等于骚扰）
            self._conn.execute(
                "ALTER TABLE messages ADD COLUMN origin TEXT NOT NULL DEFAULT 'reply'"
            )
        if "attempts" not in columns:
            self._conn.execute(
                "ALTER TABLE messages ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
            )
        if "parts_json" not in columns:
            # 重放要按原样补，不能拿整段 content 重切：前半段已经发出去会重复
            self._conn.execute(
                "ALTER TABLE messages ADD COLUMN parts_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "sent_parts" not in columns:
            self._conn.execute(
                "ALTER TABLE messages ADD COLUMN sent_parts INTEGER NOT NULL DEFAULT 0"
            )
        image_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(images)").fetchall()
        }
        if "manual" not in image_columns:
            self._conn.execute(
                "ALTER TABLE images ADD COLUMN manual INTEGER NOT NULL DEFAULT 0"
            )
        event_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(proactive_events)").fetchall()
        }
        if "attempts" not in event_columns:
            self._conn.execute(
                "ALTER TABLE proactive_events ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
            )
        card_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(cards)").fetchall()
        }
        if "confirmed" not in card_columns:
            # 老库里的卡都是"用过一阵的"，视为已确认；新抽取的才进待确认
            self._conn.execute(
                "ALTER TABLE cards ADD COLUMN confirmed INTEGER NOT NULL DEFAULT 1"
            )
        self._migrate_memories_to_cards()

    def _migrate_memories_to_cards(self) -> None:
        """把旧 memories 表一次性搬进 cards。幂等：cards 非空就跳过。"""
        row = self._conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()
        if int(row["n"]) > 0:
            return
        legacy = self._conn.execute("SELECT * FROM memories ORDER BY id").fetchall()
        if not legacy:
            return

        for item in legacy:
            content = str(item["content"] or "").strip()
            if not content:
                continue
            created = int(item["created_at"] or time.time())
            self._conn.execute(
                "INSERT INTO cards"
                "(kind, summary, detail, entities, occurred_at, importance,"
                " source_from, source_to, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    _LEGACY_KIND.get(str(item["kind"]), "fact"),
                    content[:80],
                    content[80:],
                    "[]",
                    created,
                    0.5,
                    item["source_from"],
                    item["source_to"],
                    created,
                    int(item["updated_at"] or created),
                ),
            )
        self._conn.commit()

    def _try_enable_fts(self) -> bool:
        try:
            self._conn.executescript(_FTS_SCHEMA)
            return True
        except sqlite3.OperationalError:
            return False  # 旧版 SQLite 无 trigram 分词器，退化为关键词 LIKE

    # ---------------- messages ----------------
    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        qq_msg_id: str | None = None,
        send_state: str = "sent",
        created_at: int | None = None,
        image_tags: list[str] | None = None,
        origin: str = "reply",
        parts: list[str] | None = None,
    ) -> int | None:
        """写入消息；qq_msg_id 重复时返回 None（幂等保护）。"""
        ts = int(created_at if created_at is not None else time.time())
        tags_json = json.dumps(image_tags or [], ensure_ascii=False)
        parts_json = json.dumps(parts or [], ensure_ascii=False)
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO messages"
                "(session_id, qq_msg_id, role, content, send_state, image_tags,"
                " origin, parts_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    qq_msg_id,
                    role,
                    content,
                    send_state,
                    tags_json,
                    origin,
                    parts_json,
                    ts,
                ),
            )
            self._conn.commit()
            return cur.lastrowid if cur.rowcount > 0 else None

    def has_qq_msg_id(self, qq_msg_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM messages WHERE qq_msg_id = ? LIMIT 1", (qq_msg_id,)
            ).fetchone()
        return row is not None

    def recent_messages(self, session_id: str, limit: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def get_message(self, message_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM messages WHERE id = ?", (int(message_id),)
            ).fetchone()
        return dict(row) if row else None

    def add_feedback(
        self,
        message_id: int,
        rating: str,
        note: str = "",
        card_id: int | None = None,
        created_at: int | None = None,
    ) -> int:
        """记录一次“这句合拍/不准确”，只做信号，不直接改写人格。"""
        now = int(created_at if created_at is not None else time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO feedback(message_id, rating, note, card_id, created_at)"
                " VALUES (?,?,?,?,?)",
                (int(message_id), str(rating), str(note or ""), card_id, now),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_feedback(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM feedback ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)
            ).fetchall()
        return [dict(row) for row in rows]

    def count_by_origin(self, origin: str) -> int:
        """发过多少条某类消息（语音/表情/画）——比数缓存文件准。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE origin = ?", (str(origin),)
            ).fetchone()
        return int(row[0] or 0)

    def count_messages(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE session_id = ?", (session_id,)
            ).fetchone()
        return int(row["n"])

    def first_message_at(self, session_id: str | None = None) -> int | None:
        """最早一条消息的时间；用来算关系阶段。"""
        with self._lock:
            if session_id is None:
                row = self._conn.execute(
                    "SELECT MIN(created_at) AS ts FROM messages"
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT MIN(created_at) AS ts FROM messages WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
        return int(row["ts"]) if row and row["ts"] is not None else None

    def recent_messages_after(self, session_id: str, after_id: int, limit: int) -> list[dict]:
        """取窗口内的消息（id > after_id），供 /reset 之后的上下文使用。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE session_id = ? AND id > ?"
                " ORDER BY id DESC LIMIT ?",
                (session_id, int(after_id), limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def max_message_id(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(id), 0) AS n FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row["n"])

    def has_recent_image(self, session_id: str, last_n: int = 10) -> bool:
        """最近 N 条消息里是否发过图——配图冷却的依据。"""
        if last_n <= 0:
            return False
        with self._lock:
            rows = self._conn.execute(
                "SELECT image_tags FROM messages WHERE session_id = ?"
                " ORDER BY id DESC LIMIT ?",
                (session_id, last_n),
            ).fetchall()
        return any((row["image_tags"] or "[]") not in ("[]", "") for row in rows)

    def update_send_state(self, message_id: int, state: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE messages SET send_state = ? WHERE id = ?", (state, message_id)
            )
            self._conn.commit()

    def update_sent_parts(self, message_id: int, count: int) -> None:
        """记到第几段为止：重放只补后面的，已经发出去的不再发一遍。"""
        with self._lock:
            self._conn.execute(
                "UPDATE messages SET sent_parts = ? WHERE id = ?",
                (max(0, int(count)), int(message_id)),
            )
            self._conn.commit()

    def update_message_content(self, message_id: int, content: str) -> None:
        """补写消息内容：图片描述是收到之后才知道的，得回填同一行。"""
        with self._lock:
            self._conn.execute(
                "UPDATE messages SET content = ? WHERE id = ?", (content, message_id)
            )
            self._conn.commit()

    def get_image_note(self, sha256: str) -> str:
        row = self._conn.execute(
            "SELECT note FROM image_notes WHERE sha256 = ?", (sha256,)
        ).fetchone()
        return str(row["note"]) if row else ""

    def save_image_note(self, sha256: str, note: str, now: int | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO image_notes (sha256, note, created_at)"
                " VALUES (?, ?, ?)",
                (sha256, note, int(now if now is not None else time.time())),
            )
            self._conn.commit()

    def list_failed_replies(self, since: int, limit: int = 10) -> list[dict]:
        """发送失败、还值得补发的回复。

        只挑 origin='reply'：主动消息失败了不重放，隔几个小时再冒出来等于骚扰。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages"
                " WHERE role = 'assistant' AND send_state = 'failed'"
                " AND origin = 'reply' AND created_at >= ?"
                " ORDER BY id ASC LIMIT ?",
                (int(since), int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def bump_reply_attempt(self, message_id: int, state: str = "failed") -> int:
        """重放又失败：次数 +1，返回新次数。"""
        with self._lock:
            self._conn.execute(
                "UPDATE messages SET attempts = attempts + 1, send_state = ? WHERE id = ?",
                (state, int(message_id)),
            )
            row = self._conn.execute(
                "SELECT attempts FROM messages WHERE id = ?", (int(message_id),)
            ).fetchone()
            self._conn.commit()
        return int(row["attempts"]) if row is not None else 0

    # ---------------- 主动消息 ----------------
    def last_message_time(self, session_id: str, role: str | None = None) -> int | None:
        sql = "SELECT MAX(created_at) AS ts FROM messages WHERE session_id = ?"
        params: list = [session_id]
        if role:
            sql += " AND role = ?"
            params.append(role)
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return int(row["ts"]) if row and row["ts"] else None

    def add_proactive_event(
        self,
        kind: str,
        content: str,
        trigger_at: int | None = None,
        status: str = "sent",
        created_at: int | None = None,
    ) -> int | None:
        now = int(created_at if created_at is not None else time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO proactive_events(kind, content, trigger_at, status, created_at)"
                " VALUES (?,?,?,?,?)",
                (
                    kind,
                    content,
                    int(trigger_at if trigger_at is not None else now),
                    status,
                    now,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def count_proactive_since(
        self, since: int, kind: str | None = None, status: str = "sent"
    ) -> int:
        sql = "SELECT COUNT(*) AS n FROM proactive_events WHERE created_at >= ? AND status = ?"
        params: list = [int(since), status]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return int(row["n"])

    def latest_proactive(
        self, kind: str | None = None, status: str | None = None
    ) -> dict | None:
        sql = "SELECT * FROM proactive_events WHERE 1 = 1"
        params: list = []
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC, id DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def latest_attempt(self) -> dict | None:
        """最近一次真正尝试过的主动消息（待办事件不算，它还没发过）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM proactive_events WHERE status IN ('sent', 'failed')"
                " ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def list_proactive(self, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM proactive_events ORDER BY created_at DESC, id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    # ---------------- 未来事件（scheduled）----------------
    def add_scheduled_event(
        self, content: str, trigger_at: int, now: int | None = None
    ) -> int | None:
        """登记一个未来事件。同名待办已存在时只更新时间，不重复登记。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM proactive_events WHERE kind = 'scheduled'"
                " AND status = 'pending' AND content = ?",
                (content,),
            ).fetchone()
        if row is not None:
            self.update_proactive_event(int(row["id"]), trigger_at=trigger_at)
            return None
        return self.add_proactive_event(
            "scheduled", content, trigger_at=trigger_at, status="pending", created_at=now
        )

    def list_events(self, status: str = "pending", limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM proactive_events WHERE kind = 'scheduled' AND status = ?"
                " ORDER BY trigger_at ASC, id ASC LIMIT ?",
                (status, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def next_due_event(self, now: int) -> dict | None:
        """最早一个已经到点的待办事件。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM proactive_events WHERE kind = 'scheduled'"
                " AND status = 'pending' AND trigger_at <= ?"
                " ORDER BY trigger_at ASC, id ASC LIMIT 1",
                (int(now),),
            ).fetchone()
        return dict(row) if row else None

    def update_proactive_event(
        self,
        event_id: int,
        content: str | None = None,
        status: str | None = None,
        trigger_at: int | None = None,
        created_at: int | None = None,
    ) -> bool:
        fields: list[str] = []
        params: list = []
        if content is not None:
            fields.append("content = ?")
            params.append(content)
        if status is not None:
            fields.append("status = ?")
            params.append(status)
        if trigger_at is not None:
            fields.append("trigger_at = ?")
            params.append(int(trigger_at))
        if created_at is not None:
            fields.append("created_at = ?")
            params.append(int(created_at))
        if not fields:
            return False
        params.append(int(event_id))
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE proactive_events SET {', '.join(fields)} WHERE id = ?", params
            )
            self._conn.commit()
            return cur.rowcount > 0

    def delete_proactive_event(self, event_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM proactive_events WHERE id = ?", (int(event_id),)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def reschedule_event(self, event_id: int, next_trigger: int) -> bool:
        """把一个待办事件往后推，并记一次尝试。

        用于"这次先不说"和"发送失败"——不是放弃，是换个时间再来一次。
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE proactive_events SET trigger_at = ?, attempts = attempts + 1"
                " WHERE id = ?",
                (int(next_trigger), int(event_id)),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def search_messages(self, query: str, limit: int = 5) -> list[dict]:
        """按关键词召回历史消息；任何异常都退化为空结果，不影响主流程。"""
        with self._lock:
            if self.fts_enabled:
                match = sanitize_fts_query(query)
                if not match:
                    return []
                try:
                    rows = self._conn.execute(
                        "SELECT m.* FROM messages_fts f JOIN messages m ON m.id = f.rowid"
                        " WHERE messages_fts MATCH ? ORDER BY m.id DESC LIMIT ?",
                        (match, limit),
                    ).fetchall()
                    return [dict(r) for r in rows]
                except sqlite3.Error:
                    return []
            # 退化路径：LIKE 模糊匹配（不做分词，够用即可）
            words = [w for w in _SPLIT.split(query or "") if len(w) >= 2]
            if not words:
                return []
            like = "%" + words[0] + "%"
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
                (like, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def recall_messages(
        self,
        query: str,
        limit: int = 3,
        exclude_ids: set | None = None,
        role: str = "user",
    ) -> list[dict]:
        """从历史原话里召回和这句话相关的几句（"你以前说过的"）。

        两条路：三元片段走 FTS（bm25 排序），两字词走有界 LIKE 兜底——
        trigram 索引不到「香菜」「火锅」这种两个字的词，可那才是最常说的。
        """
        skip = set(exclude_ids or ())
        picked: list[dict] = []
        seen: set = set()

        def take(rows) -> None:
            for row in rows:
                item = dict(row)
                if item["id"] in skip or item["id"] in seen:
                    continue
                seen.add(item["id"])
                picked.append(item)
                if len(picked) >= int(limit):
                    return

        with self._lock:
            terms = trigrams(query)
            if terms and self.fts_enabled:
                match = " OR ".join(f'"{term}"' for term in terms)
                try:
                    rows = self._conn.execute(
                        "SELECT m.* FROM messages_fts f JOIN messages m ON m.id = f.rowid"
                        " WHERE messages_fts MATCH ? AND m.role = ?"
                        " ORDER BY bm25(messages_fts) LIMIT ?",
                        (match, role, int(limit) + len(skip)),
                    ).fetchall()
                    take(rows)
                except sqlite3.Error:
                    pass  # FTS 坏了不该影响对话
            if len(picked) < int(limit):
                take(self._recall_by_pairs(query, role, limit))
        return picked[: int(limit)]

    _RECENT_SCAN = 8000

    def _recall_by_pairs(self, query: str, role: str, limit: int) -> list:
        """两字词兜底：只在最近一段消息里扫，不做全表 LIKE。"""
        pairs = bigrams(query)
        if not pairs:
            return []
        floor_row = self._conn.execute("SELECT MAX(id) FROM messages").fetchone()
        floor = (floor_row[0] or 0) - self._RECENT_SCAN
        clause = " OR ".join(["content LIKE ?"] * len(pairs))
        return self._conn.execute(
            f"SELECT * FROM messages WHERE role = ? AND id >= ? AND ({clause})"
            " ORDER BY id DESC LIMIT ?",
            (role, floor, *[f"%{pair}%" for pair in pairs], int(limit) * 3),
        ).fetchall()

    # ---------------- 状态（情绪 / 驱力） ----------------
    def load_state(self) -> dict[str, tuple[float, int]]:
        """读全部状态值：key -> (value, updated_at)。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value, updated_at FROM bot_state"
            ).fetchall()
        return {
            row["key"]: (float(row["value"]), int(row["updated_at"])) for row in rows
        }

    def save_state(self, values: dict[str, float], now: int) -> None:
        with self._lock:
            self._conn.executemany(
                "INSERT INTO bot_state(key, value, updated_at) VALUES (?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                " updated_at = excluded.updated_at",
                [(key, float(value), int(now)) for key, value in values.items()],
            )
            self._conn.commit()

    # ---------------- memories ----------------
    def add_memory(
        self,
        kind: str,
        content: str,
        source_from: int | None = None,
        source_to: int | None = None,
    ) -> int:
        now = int(time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO memories(kind, content, source_from, source_to, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (kind, content, source_from, source_to, now, now),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_memories(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memories ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_memory(self, memory_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def delete_memories_matching(self, keyword: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM memories WHERE content LIKE ?", (f"%{keyword}%",)
            )
            self._conn.commit()
            return int(cur.rowcount)

    def count_memories(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()
        return int(row["n"])

    # ---------------- cards ----------------
    def add_card(
        self,
        kind: str,
        summary: str,
        detail: str = "",
        entities: list[str] | None = None,
        occurred_at: int | None = None,
        importance: float = 0.5,
        source_from: int | None = None,
        source_to: int | None = None,
        pinned: bool = False,
        created_at: int | None = None,
        confirmed: bool = True,
    ) -> int:
        now = int(created_at if created_at is not None else time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO cards"
                "(kind, summary, detail, entities, occurred_at, importance, pinned,"
                " source_from, source_to, created_at, updated_at, confirmed)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    kind,
                    summary,
                    detail,
                    json.dumps(entities or [], ensure_ascii=False),
                    occurred_at,
                    float(importance),
                    1 if pinned else 0,
                    source_from,
                    source_to,
                    now,
                    now,
                    1 if confirmed else 0,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    @staticmethod
    def _card_row(row) -> dict:
        item = dict(row)
        try:
            item["entities"] = json.loads(item.get("entities") or "[]")
        except json.JSONDecodeError:
            item["entities"] = []
        item["pinned"] = bool(item.get("pinned"))
        item["confirmed"] = bool(item.get("confirmed", 1))
        return item

    def set_confirmed(self, card_id: int, confirmed: bool = True) -> bool:
        """确认（或撤回确认）一张卡；不存在返回 False。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE cards SET confirmed = ?, updated_at = ? WHERE id = ?",
                (1 if confirmed else 0, int(time.time()), int(card_id)),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def get_card(self, card_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM cards WHERE id = ?", (int(card_id),)).fetchone()
        return self._card_row(row) if row else None

    def list_cards(self, status: str = "active", limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM cards WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status, int(limit)),
            ).fetchall()
        return [self._card_row(row) for row in rows]

    def count_cards(self, status: str = "active") -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM cards WHERE status = ?", (status,)
            ).fetchone()
        return int(row["n"])

    def update_card(self, card_id: int, **fields) -> bool:
        """只允许改白名单字段；entities 传 list 会转成 JSON。"""
        updates = {k: v for k, v in fields.items() if k in _CARD_UPDATABLE}
        if not updates:
            return False
        if "entities" in updates and isinstance(updates["entities"], list):
            updates["entities"] = json.dumps(updates["entities"], ensure_ascii=False)
        if "importance" in updates:
            updates["importance"] = float(updates["importance"])
        if "updated_at" not in updates:
            updates["updated_at"] = int(time.time())

        assignments = ", ".join(f"{key} = ?" for key in updates)
        values = list(updates.values()) + [int(card_id)]
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE cards SET {assignments} WHERE id = ?", values
            )
            self._conn.commit()
            return cur.rowcount > 0

    def delete_card(self, card_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM cards WHERE id = ?", (int(card_id),))
            self._conn.commit()
            return cur.rowcount > 0

    def delete_cards_matching(self, keyword: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM cards WHERE summary LIKE ?", (f"%{keyword}%",)
            )
            self._conn.commit()
            return int(cur.rowcount)

    # ---------------- goals ----------------
    @staticmethod
    def _goal_row(row) -> dict:
        item = dict(row)
        try:
            item["evidence"] = json.loads(item.get("evidence") or "[]")
        except json.JSONDecodeError:
            item["evidence"] = []
        item["priority"] = float(item.get("priority") or 0)
        item["confidence"] = float(item.get("confidence") or 0)
        return item

    def add_goal(
        self,
        kind: str,
        title: str,
        why: str = "",
        horizon: str = "week",
        priority: float = 0.5,
        status: str = "candidate",
        source: str = "reflection",
        evidence: list[int] | None = None,
        confidence: float = 0.5,
        due_at: int | None = None,
        review_at: int | None = None,
        created_at: int | None = None,
    ) -> int:
        now = int(created_at if created_at is not None else time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO goals(kind, title, why, horizon, priority, status, source,"
                " evidence, confidence, due_at, review_at, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    kind,
                    title,
                    why,
                    horizon,
                    float(priority),
                    status,
                    source,
                    json.dumps(evidence or []),
                    float(confidence),
                    due_at,
                    review_at,
                    now,
                    now,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def get_goal(self, goal_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM goals WHERE id = ?", (int(goal_id),)).fetchone()
        return self._goal_row(row) if row else None

    def list_goals(self, status: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM goals"
        args: list = []
        if status:
            sql += " WHERE status = ?"
            args.append(status)
        sql += " ORDER BY priority DESC, id DESC LIMIT ?"
        args.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._goal_row(row) for row in rows]

    def count_goals(self, status: str | None = None) -> int:
        if status:
            sql, args = "SELECT COUNT(*) AS n FROM goals WHERE status = ?", (status,)
        else:
            sql, args = "SELECT COUNT(*) AS n FROM goals", ()
        with self._lock:
            row = self._conn.execute(sql, args).fetchone()
        return int(row["n"] or 0)

    def update_goal(self, goal_id: int, **fields) -> bool:
        updates = {k: v for k, v in fields.items() if k in _GOAL_UPDATABLE}
        if not updates:
            return False
        if "evidence" in updates and isinstance(updates["evidence"], list):
            updates["evidence"] = json.dumps(updates["evidence"])
        if "priority" in updates:
            updates["priority"] = float(updates["priority"])
        if "confidence" in updates:
            updates["confidence"] = float(updates["confidence"])
        if "updated_at" not in updates:
            updates["updated_at"] = int(time.time())
        assignments = ", ".join(f"{key} = ?" for key in updates)
        values = list(updates.values()) + [int(goal_id)]
        with self._lock:
            cur = self._conn.execute(f"UPDATE goals SET {assignments} WHERE id = ?", values)
            self._conn.commit()
            return cur.rowcount > 0

    def add_goal_action(
        self,
        goal_id: int,
        action: str,
        payload: dict | None = None,
        run_at: int | None = None,
        status: str = "pending",
        approval: str = "auto",
        created_at: int | None = None,
    ) -> int:
        now = int(created_at if created_at is not None else time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO goal_actions(goal_id, action, payload, run_at, status, approval,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    int(goal_id),
                    action,
                    json.dumps(payload or {}, ensure_ascii=False),
                    run_at,
                    status,
                    approval,
                    now,
                    now,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def get_goal_action(self, action_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM goal_actions WHERE id = ?", (int(action_id),)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        try:
            item["payload"] = json.loads(item.get("payload") or "{}")
        except json.JSONDecodeError:
            item["payload"] = {}
        return item

    def list_goal_actions(self, status: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM goal_actions"
        args: list = []
        if status:
            sql += " WHERE status = ?"
            args.append(status)
        sql += " ORDER BY run_at ASC, id ASC LIMIT ?"
        args.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.get("payload") or "{}")
            except json.JSONDecodeError:
                item["payload"] = {}
            result.append(item)
        return result

    def update_goal_action(self, action_id: int, **fields) -> bool:
        allowed = {"status", "attempts", "result", "run_at", "approval", "updated_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        updates.setdefault("updated_at", int(time.time()))
        assignments = ", ".join(f"{key} = ?" for key in updates)
        values = list(updates.values()) + [int(action_id)]
        with self._lock:
            cur = self._conn.execute(f"UPDATE goal_actions SET {assignments} WHERE id = ?", values)
            self._conn.commit()
            return cur.rowcount > 0

    def add_outcome(self, action_id: int, signal: str, note: str = "", created_at: int | None = None) -> int:
        now = int(created_at if created_at is not None else time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO outcomes(action_id, signal, note, created_at) VALUES (?,?,?,?)",
                (int(action_id), signal, note, now),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def merge_cards(self, source_id: int, target_id: int) -> bool:
        """把信息少的卡并进信息多的那张：detail 合并、命中次数累加、源卡归档。

        归档不是删除：卡片还在库里，只是不再进上下文——做梦**永远不删**东西。
        """
        if int(source_id) == int(target_id):
            return False
        source = self.get_card(int(source_id))
        target = self.get_card(int(target_id))
        if source is None or target is None:
            return False

        detail = (target.get("detail") or "").strip()
        extra = (source.get("detail") or source.get("summary") or "").strip()
        if extra and extra not in detail:
            detail = (f"{detail}；{extra}" if detail else extra)[:400]

        now = int(time.time())
        with self._lock:
            self._conn.execute(
                "UPDATE cards SET detail = ?, hit_count = hit_count + ?, updated_at = ?"
                " WHERE id = ?",
                (detail, int(source.get("hit_count") or 0), now, int(target_id)),
            )
            self._conn.execute(
                "UPDATE cards SET status = 'archived', updated_at = ? WHERE id = ?",
                (now, int(source_id)),
            )
            self._conn.commit()
        return True

    def messages_since(self, since: int, limit: int = 400) -> list[dict]:
        """上次做梦之后的所有消息（跨会话，按时间正序）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE created_at >= ? ORDER BY id ASC LIMIT ?",
                (int(since), int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def messages_after_id(self, after_id: int, limit: int = 400) -> list[dict]:
        """按 message id 取下一批（做梦游标用，不会因为同一秒而卡住）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE id > ? ORDER BY id ASC LIMIT ?",
                (int(after_id), int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def last_message_id_before(self, stamp: int) -> int:
        """时间戳之前最后一条消息的 id；用来给老库初始化做梦游标。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(id), 0) FROM messages WHERE created_at < ?",
                (int(stamp),),
            ).fetchone()
        return int(row[0] or 0)

    def messages_between(self, start: int, end: int, limit: int = 800) -> list[dict]:
        """某个时间段的消息（按时间正序），给日摘要用。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE created_at >= ? AND created_at < ?"
                " ORDER BY id ASC LIMIT ?",
                (int(start), int(end), int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_pinned(self, card_id: int, pinned: bool) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE cards SET pinned = ?, updated_at = ? WHERE id = ?",
                (1 if pinned else 0, int(time.time()), int(card_id)),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def touch_cards(self, card_ids: list[int], now: int | None = None) -> None:
        """记一次命中。同一张卡每天最多计一次，否则权重很快被灌满。"""
        if not card_ids:
            return
        stamp = int(now if now is not None else time.time())
        day_start = stamp - (stamp % 86400)
        with self._lock:
            self._conn.executemany(
                "UPDATE cards SET hit_count = hit_count + 1, last_hit_at = ?"
                " WHERE id = ? AND (last_hit_at IS NULL OR last_hit_at < ?)",
                [(stamp, int(card_id), day_start) for card_id in card_ids],
            )
            self._conn.commit()

    def cards_since(self, after_id: int, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM cards WHERE source_to >= ? ORDER BY id LIMIT ?",
                (int(after_id), int(limit)),
            ).fetchall()
        return [self._card_row(row) for row in rows]

    # ---------------- images ----------------
    @staticmethod
    def _image_row(row) -> dict:
        item = dict(row)
        try:
            item["tags"] = json.loads(item.get("tags") or "[]")
        except json.JSONDecodeError:
            item["tags"] = []
        item["manual"] = bool(item.get("manual"))
        return item

    def add_image(
        self,
        sha256: str,
        rel_path: str,
        mime: str,
        byte_size: int,
        tags: list[str] | None = None,
        source: str = "user",
        source_msg_id: str | None = None,
        manual: bool = False,
        created_at: int | None = None,
    ) -> int | None:
        """插入图片记录；sha256 已存在时返回 None（去重）。"""
        now = int(created_at if created_at is not None else time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO images"
                "(sha256, rel_path, mime, byte_size, tags, manual, source, source_msg_id,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    sha256,
                    rel_path,
                    mime,
                    int(byte_size),
                    json.dumps(sorted({t for t in (tags or []) if t}), ensure_ascii=False),
                    1 if manual else 0,
                    source,
                    source_msg_id,
                    now,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid) if cur.rowcount > 0 else None

    def find_image_by_sha(self, sha256: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM images WHERE sha256 = ?", (sha256,)
            ).fetchone()
        return self._image_row(row) if row else None

    def get_image(self, image_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM images WHERE id = ?", (int(image_id),)
            ).fetchone()
        return self._image_row(row) if row else None

    def update_image_tags(self, image_id: int, tags: list[str]) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE images SET tags = ? WHERE id = ?",
                (
                    json.dumps(sorted({t for t in tags if t}), ensure_ascii=False),
                    int(image_id),
                ),
            )
            self._conn.commit()

    def list_untagged_images(self, limit: int = 20) -> list[dict]:
        """图库里还没有标签的（老收藏多半是这种），给 `/img retag` 补标用。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM images WHERE status = 'active' AND manual = 0"
                " AND (tags = '[]' OR tags = '') ORDER BY id ASC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [self._image_row(row) for row in rows]

    def list_images(self, status: str = "active", tag: str | None = None, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM images WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status, int(limit)),
            ).fetchall()
        items = [self._image_row(row) for row in rows]
        if tag:
            wanted = tag.strip().lower()
            items = [item for item in items if wanted in {t.lower() for t in item["tags"]}]
        return items

    def count_images(self, status: str = "active") -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM images WHERE status = ?", (status,)
            ).fetchone()
        return int(row["n"])

    def latest_image(self, source: str | None = None) -> dict | None:
        sql = "SELECT * FROM images WHERE status = 'active'"
        params: list = []
        if source:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY id DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return self._image_row(row) if row else None

    def tag_image(self, image_id: int, tags: list[str], manual: bool = True) -> bool:
        """合并标签；manual=True 表示人工打的，淘汰时优先保留。"""
        image = self.get_image(image_id)
        if image is None:
            return False
        merged = sorted({*(t for t in image["tags"]), *(t for t in tags if t)})
        with self._lock:
            cur = self._conn.execute(
                "UPDATE images SET tags = ?, manual = ? WHERE id = ?",
                (json.dumps(merged, ensure_ascii=False), 1 if manual else 0, int(image_id)),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def delete_image(self, image_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM images WHERE id = ?", (int(image_id),))
            self._conn.commit()
            return cur.rowcount > 0

    def record_image_use(self, image_id: int, now: int | None = None) -> None:
        stamp = int(now if now is not None else time.time())
        with self._lock:
            self._conn.execute(
                "UPDATE images SET use_count = use_count + 1, last_used_at = ? WHERE id = ?",
                (stamp, int(image_id)),
            )
            self._conn.commit()

    def evict_images(self, max_count: int) -> int:
        """超出容量时，从「无人工标签 + 最久未用」开始归档（文件保留）。"""
        total = self.count_images()
        if total <= max_count:
            return 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM images WHERE status = 'active' AND manual = 0"
                " ORDER BY COALESCE(last_used_at, created_at) ASC, id ASC LIMIT ?",
                (total - max_count,),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if ids:
                self._conn.executemany(
                    "UPDATE images SET status = 'archived' WHERE id = ?",
                    [(image_id,) for image_id in ids],
                )
                self._conn.commit()
        return len(ids)

    # ---------------- 做梦 ----------------
    def start_dream(self, started_at: int | None = None) -> int:
        now = int(started_at if started_at is not None else time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO dream_log(started_at) VALUES (?)", (now,)
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def finish_dream(
        self,
        dream_id: int,
        *,
        finished_at: int | None = None,
        messages_seen: int = 0,
        cards_added: int = 0,
        cards_merged: int = 0,
        cards_reweight: int = 0,
        journal_chars: int = 0,
        ok: bool = True,
        note: str = "",
    ) -> None:
        now = int(finished_at if finished_at is not None else time.time())
        with self._lock:
            self._conn.execute(
                "UPDATE dream_log SET finished_at = ?, messages_seen = ?,"
                " cards_added = ?, cards_merged = ?, cards_reweight = ?,"
                " journal_chars = ?, ok = ?, note = ? WHERE id = ?",
                (
                    now,
                    int(messages_seen),
                    int(cards_added),
                    int(cards_merged),
                    int(cards_reweight),
                    int(journal_chars),
                    1 if ok else 0,
                    note,
                    int(dream_id),
                ),
            )
            self._conn.commit()

    def last_dream_at(self) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(finished_at) AS ts FROM dream_log WHERE ok = 1"
            ).fetchone()
        return int(row["ts"]) if row is not None and row["ts"] is not None else None

    def list_dreams(self, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM dream_log ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(row) for row in rows]

    # ---------------- usage ----------------
    def log_usage(
        self, model: str, tokens_in: int, tokens_out: int, purpose: str, ok: bool = True
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO usage_log(model, tokens_in, tokens_out, purpose, ok, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (model, tokens_in, tokens_out, purpose, 1 if ok else 0, int(time.time())),
            )
            self._conn.commit()

    def usage_between(self, start: int, end: int) -> tuple[int, int]:
        """某段时间的 token（给"你自己"那块用）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0)"
                " FROM usage_log WHERE created_at >= ? AND created_at < ?",
                (int(start), int(end)),
            ).fetchone()
        return int(row[0] or 0), int(row[1] or 0)

    def count_messages_between(self, start: int, end: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE created_at >= ? AND created_at < ?",
                (int(start), int(end)),
            ).fetchone()
        return int(row[0] or 0)

    def usage_total(self) -> tuple[int, int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(tokens_in),0) AS i, COALESCE(SUM(tokens_out),0) AS o"
                " FROM usage_log"
            ).fetchone()
        return int(row["i"]), int(row["o"])

    # ---------------- cursors ----------------
    def get_cursor(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT v FROM cursors WHERE k = ?", (key,)).fetchone()
        return str(row["v"]) if row else default

    def set_cursor(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO cursors(k, v) VALUES (?,?)"
                " ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                (key, value),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
