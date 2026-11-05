"""梗百科：把梗鲸（aigengtu.com）档案馆的台词/释义搬进来，让她听得懂梗。

只存文本元数据（编号、台词、角色、情绪、释义、出处、作者），不重分发图片。
抓取限速 1 请求/0.7 秒，支持断点续传（已入库的编号跳过）。
"""

from __future__ import annotations

import logging
import re
import time
import urllib.request
from html import unescape

log = logging.getLogger("qqbot")

BASE = "https://aigengtu.com/zh/meme/{number}"
TOTAL = 2956
DELAY = 0.7

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memes (
  number     INTEGER PRIMARY KEY,
  title      TEXT NOT NULL,
  quote      TEXT NOT NULL DEFAULT '',
  role       TEXT NOT NULL DEFAULT '',
  kind       TEXT NOT NULL DEFAULT '',
  emotion    TEXT NOT NULL DEFAULT '',
  meaning    TEXT NOT NULL DEFAULT '',
  origin     TEXT NOT NULL DEFAULT '',
  author     TEXT NOT NULL DEFAULT '',
  page_url   TEXT NOT NULL DEFAULT '',
  fetched_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memes_emotion ON memes(emotion);
"""

_TAG_RE = re.compile(r"<[^>]+>")
_H1_RE = re.compile(r'<h1[^>]*>(.*?)</h1>', re.S)
_MEANING_RE = re.compile(r'<span class="lang">中文</span>(.*?)</p>', re.S)
_ORIGIN_RE = re.compile(r'<span class="origin-label">出处</span>(.*?)</p>', re.S)
_AUTHOR_RE = re.compile(r'<p class="contrib">✍️\s*(.*?)</p>', re.S)
_TYPE_RE = re.compile(r'<span class="wi-type">(.*?)</span>', re.S)
_ROLE_RE = re.compile(r'href="/[^"]+">#([^<]+)</a>', re.S)
_QUOTE_RE = re.compile(r"『(.*?)』", re.S)
_EMOTION_RE = re.compile(r"』(.+?)表情包")


def ensure(store) -> None:
    if getattr(store, "_memes_ready", False):
        return
    with store._lock:  # noqa: SLF001
        store._conn.executescript(_SCHEMA)  # noqa: SLF001
        store._conn.commit()  # noqa: SLF001
    store._memes_ready = True


def _text(raw: str) -> str:
    return unescape(_TAG_RE.sub("", raw or "")).strip()


def parse_detail(html: str) -> dict:
    match = _H1_RE.search(html)
    title = unescape(_TAG_RE.sub("", match.group(1))).strip() if match else ""
    if not title:
        return {}
    quote = (_QUOTE_RE.search(title) or [None, ""])[1]
    emotion = (_EMOTION_RE.search(title) or [None, ""])[1]
    return {
        "title": title,
        "quote": _text(quote),
        "role": _text((_ROLE_RE.search(html) or [None, ""])[1]),
        "kind": _text((_TYPE_RE.search(html) or [None, ""])[1]),
        "emotion": _text(emotion),
        "meaning": _text((_MEANING_RE.search(html) or [None, ""])[1])[:400],
        "origin": _text((_ORIGIN_RE.search(html) or [None, ""])[1])[:300],
        "author": _text((_AUTHOR_RE.search(html) or [None, ""])[1])[:40],
    }


def fetch(number: int, timeout: float = 20.0) -> str:
    url = BASE.format(number=number)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) dafeiyu/1.0",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def has(store, number: int) -> bool:
    ensure(store)
    with store._lock:  # noqa: SLF001
        row = store._conn.execute("SELECT 1 FROM memes WHERE number = ?", (number,)).fetchone()
    return row is not None


def save(store, number: int, data: dict, now: int | None = None) -> None:
    ensure(store)
    with store._lock:  # noqa: SLF001
        store._conn.execute(  # noqa: SLF001
            "INSERT OR REPLACE INTO memes (number, title, quote, role, kind, emotion, meaning,"
            " origin, author, page_url, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                number,
                data.get("title", ""),
                data.get("quote", ""),
                data.get("role", ""),
                data.get("kind", ""),
                data.get("emotion", ""),
                data.get("meaning", ""),
                data.get("origin", ""),
                data.get("author", ""),
                BASE.format(number=number),
                int(now if now is not None else time.time()),
            ),
        )
        store._conn.commit()  # noqa: SLF001


def ingest(store, start: int = 1, end: int = TOTAL, delay: float = DELAY,
           fetch=None, progress=None, sleep=time.sleep, limit: int | None = None) -> int:
    """把编号 start..end 抓进库。已存在跳过，失败记日志继续。"""
    ensure(store)
    getter = fetch or globals()["fetch"]
    report = progress or (lambda done, total, number: None)
    written = 0
    numbers = [n for n in range(start, end + 1) if not has(store, n)]
    if limit is not None:
        numbers = numbers[:limit]
    for index, number in enumerate(numbers, 1):
        try:
            html = getter(number)
        except Exception as exc:  # noqa: BLE001 - 单条失败不影响整批
            log.warning("梗 %d 抓取失败：%s", number, exc)
            sleep(delay)
            continue
        data = parse_detail(html)
        if not data:
            log.warning("梗 %d 解析为空", number)
            sleep(delay)
            continue
        save(store, number, data)
        written += 1
        report(index, len(numbers), number)
        sleep(delay)
    return written


def get(store, number: int) -> dict:
    ensure(store)
    with store._lock:  # noqa: SLF001
        row = store._conn.execute("SELECT * FROM memes WHERE number = ?", (number,)).fetchone()
    return dict(row) if row is not None else {}


def count(store) -> int:
    ensure(store)
    with store._lock:  # noqa: SLF001
        row = store._conn.execute("SELECT COUNT(*) AS n FROM memes").fetchone()
    return int(row["n"]) if row is not None else 0


def _bigrams(text: str) -> set[str]:
    body = re.sub(r"\s+", "", text or "").lower()
    if len(body) < 2:
        return {body} if body else set()
    return {body[i : i + 2] for i in range(len(body) - 1)}


def search(store, query: str, limit: int = 2) -> list[dict]:
    """按台词/释义找梗；纯本地打分，不联网。"""
    ensure(store)
    grams = _bigrams(query)
    if not grams:
        return []
    with store._lock:  # noqa: SLF001
        rows = store._conn.execute(
            "SELECT * FROM memes ORDER BY number LIMIT 5000"
        ).fetchall()
    scored = []
    for row in rows:
        body = _bigrams(f"{row['title']}{row['quote']}{row['meaning']}{row['emotion']}")
        hits = len(grams & body)
        if hits:
            scored.append((hits, dict(row)))
    scored.sort(key=lambda item: (-item[0], item[1]["number"]))
    return [row for _, row in scored[:limit]]


def block(store, query: str, limit: int = 2, max_chars: int = 240) -> str:
    rows = search(store, query, limit=limit)
    if not rows:
        return ""
    lines = ["【这个梗】"]
    for row in rows:
        head = f"#{row['number']} {row['quote'] or row['title']}（{row['emotion'] or row['kind'] or '梗'}）"
        tail = (row["meaning"] or "")[:120]
        lines.append(f"- {head}：{tail}" if tail else f"- {head}")
    return "\n".join(lines)[:max_chars]
