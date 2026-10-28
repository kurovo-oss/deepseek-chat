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
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cards_kind   ON cards(kind, status);
CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status);
CREATE INDEX IF NOT EXISTS idx_cards_source ON cards(source_from, source_to);

CREATE TABLE IF NOT EXISTS proactive_events (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,
  content     TEXT NOT NULL,
  trigger_at  INTEGER NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',
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
_LEGACY_KIND = {"fact": "fact", "preference": "preference", "event": "event"}
_CARD_UPDATABLE = frozenset(
    {"kind", "summary", "detail", "entities", "occurred_at", "importance", "status", "updated_at"}
)


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
        image_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(images)").fetchall()
        }
        if "manual" not in image_columns:
            self._conn.execute(
                "ALTER TABLE images ADD COLUMN manual INTEGER NOT NULL DEFAULT 0"
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
    ) -> int | None:
        """写入消息；qq_msg_id 重复时返回 None（幂等保护）。"""
        ts = int(created_at if created_at is not None else time.time())
        tags_json = json.dumps(image_tags or [], ensure_ascii=False)
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO messages"
                "(session_id, qq_msg_id, role, content, send_state, image_tags, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (session_id, qq_msg_id, role, content, send_state, tags_json, ts),
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

    def count_messages(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE session_id = ?", (session_id,)
            ).fetchone()
        return int(row["n"])

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
    ) -> int:
        now = int(created_at if created_at is not None else time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO cards"
                "(kind, summary, detail, entities, occurred_at, importance, pinned,"
                " source_from, source_to, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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
        return item

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
