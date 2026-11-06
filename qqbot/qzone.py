"""QQ 空间：一天最多一条，可以配一张当天发过的图，默认只写草稿（试运行）。

真发走 NapCat 的扩展动作 `send_qzone_msg`（需要 NapCat ≥ 4.18.42）。
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from . import digest as digest_mod
from . import guard as guard_mod
from . import growth as growth_mod
from . import life as life_mod

log = logging.getLogger("qqbot")

WINDOW = (20, 23)
MIN_GAP = 1200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS qzone_posts (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  content    TEXT NOT NULL,
  images     TEXT NOT NULL DEFAULT '[]',
  tid        TEXT NOT NULL DEFAULT '',
  status     TEXT NOT NULL,
  error      TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL
);
"""

PROMPT = """你是「大肥鱼」，正在写今天的 QQ 空间说说。第一人称，像跟熟人说话，120~300 字。

写今天真正发生的一两件事，加上你当时的感受。不要标题、不要分点、不要"今天真是充实的一天"这种套话，
不要提"AI""模型""用户"，不要 @ 人，不要放链接。

【今天聊到的】
{digest}

【你今天】
{life}

【你记住的高光】
{events}

【你现在的心情】
{mood}

【最近两三条说说】
{recent}

【这轮别再写】
{banned}

同一件事别连着写：上面写过的场景、菜、物件，这条换掉，别又写同一锅汤。只输出说说正文。"""


def ensure(store) -> None:
    if getattr(store, "_qzone_ready", False):
        return
    with store._lock:  # noqa: SLF001
        store._conn.executescript(_SCHEMA)  # noqa: SLF001
        store._conn.commit()  # noqa: SLF001
    store._qzone_ready = True


def _today_start(now: int) -> int:
    local = time.localtime(now)
    return int(time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)))


def _posted_today(store, now: int) -> int:
    ensure(store)
    with store._lock:  # noqa: SLF001
        row = store._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) AS n FROM qzone_posts WHERE created_at >= ? AND status IN ('ok','dry_run')",
            (_today_start(now),),
        ).fetchone()
    return int(row["n"]) if row is not None else 0


def in_window(now: int) -> bool:
    hour = time.localtime(now).tm_hour
    return WINDOW[0] <= hour <= WINDOW[1]


def pick_image(store, settings, now: int) -> Path | None:
    """今天她发过的图里挑最后一张；没有就 None。"""
    with store._lock:  # noqa: SLF001
        row = store._conn.execute(  # noqa: SLF001
            "SELECT rel_path FROM images WHERE created_at >= ? AND status IN ('ok','active') "
            "ORDER BY id DESC LIMIT 1",
            (_today_start(now),),
        ).fetchone()
    if row is None or not row["rel_path"]:
        return None
    path = Path(getattr(settings, "image_dir", Path("data/images"))) / str(row["rel_path"])
    return path if path.exists() else None



def _recent_block(store, limit: int = 2) -> str:
    ensure(store)
    with store._lock:  # noqa: SLF001
        rows = store._conn.execute(  # noqa: SLF001
            "SELECT content FROM qzone_posts WHERE status IN ('ok','dry_run')"
            " ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    lines = [f"- {str(row['content']).strip()[:80]}" for row in rows if row["content"]]
    return "\n".join(lines) or "（最近没发过）"



_STOP_GRAMS = frozenset((
    "今天", "昨天", "明天", "上午", "下午", "晚上", "时候", "一个", "这个", "那个",
    "自己", "他们", "我们", "什么", "有点", "还是", "已经", "然后", "就是", "觉得",
    "起来", "出来", "看着", "不敢", "回来", "他说", "她说", "我发", "发了",
))


def _recent_posts(store, limit: int = 2) -> list[str]:
    ensure(store)
    with store._lock:  # noqa: SLF001
        rows = store._conn.execute(  # noqa: SLF001
            "SELECT content FROM qzone_posts WHERE status IN ('ok','dry_run')"
            " ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [str(row["content"]).strip() for row in rows if row["content"]]


def _banned_list(posts: list[str], limit: int = 5) -> list[str]:
    """最近几条都在写的东西（比如连着几条的牛腩），这轮点名别写。"""
    if len(posts) < 2:
        return []
    counts: dict[str, int] = {}
    for post in posts:
        seen = set()
        for chunk in re.findall(r"[\u4e00-\u9fff]+", post):
            for index in range(len(chunk) - 1):
                gram = chunk[index:index + 2]
                if gram in _STOP_GRAMS or gram in seen:
                    continue
                seen.add(gram)
                counts[gram] = counts.get(gram, 0) + 1
    shared = [gram for gram, count in counts.items() if count >= 2]
    shared.sort(key=lambda gram: (-counts[gram], gram))
    return shared[:limit]


def _banned_topics(posts: list[str], limit: int = 5) -> str:
    return "、".join(_banned_list(posts, limit)) or "（没有）"


def _events_block(store, limit: int = 3) -> str:
    try:
        events = growth_mod.recent(store, limit=limit)
    except Exception:  # noqa: BLE001 - 事件流坏了不该挡住发空间
        return ""
    lines = [f"- {event['text']}" for event in events if event.get("text")]
    return "\n".join(lines) or "（今天没什么特别的）"


def draft(store, llm, settings, now: int | None = None) -> dict:
    stamp = int(now if now is not None else time.time())
    mood = ""
    prompt = PROMPT.format(
        digest=digest_mod.recent_text(settings) or "（今天没聊多少）",
        life=life_mod.snippet(settings) or "（今天还没想好写什么）",
        events=_events_block(store),
        mood=mood or "（还行）",
        recent=_recent_block(store),
        banned=_banned_topics(_recent_posts(store)),
    )
    recent_posts = _recent_posts(store)
    banned = _banned_list(recent_posts)
    result = llm.chat(
        [{"role": "system", "content": prompt}, {"role": "user", "content": "写吧。"}],
        temperature=0.85,
        max_tokens=600,
    )
    text = _clean(getattr(result, "text", "") or "")
    repeated = [word for word in banned if word in text]
    if repeated:
        # 写了最近重复的事：再要一版，点名不许提
        retry = prompt + "\n\n【重写】上一版又写了：" + "、".join(repeated) + "。这一版一个字都别提这些，换今天别的两件事写。"
        try:
            again = llm.chat(
                [{"role": "system", "content": retry}, {"role": "user", "content": "写吧。"}],
                temperature=0.95,
                max_tokens=600,
            )
        except Exception:  # noqa: BLE001 - 重写失败就用第一版
            again = None
        retried = _clean(getattr(again, "text", "") or "") if again is not None else ""
        still = [word for word in repeated if word in retried]
        if retried and not still:
            text = retried
    image = pick_image(store, settings, stamp) if getattr(settings, "qzone_image_enabled", True) else None
    return {"text": text, "image": image, "at": stamp}


def _clean(raw: str) -> str:
    text = (raw or "").strip()
    text = text.strip("`").strip()
    for prefix in ("说说：", "正文：", "【说说】"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)[:600]


def publish(store, guard, sender, text: str, image: Path | None = None,
            now: int | None = None, reason: str = "") -> dict:
    """过闸门再发。返回 {"status": ..., "detail": ...}。"""
    ensure(store)
    stamp = int(now if now is not None else time.time())
    decision = guard.check("qzone", "post", target=reason, user_id=guard.settings.allow_user_id, now=stamp)
    if not decision.allowed:
        guard.record("qzone", "post", target=reason, payload=text, result="denied",
                     error=decision.reason, now=stamp)
        return {"status": "denied", "detail": decision.reason}
    if decision.dry_run:
        guard.record("qzone", "post", target=reason, payload=text, result="dry_run", now=stamp)
        _save(store, text, image, "", "dry_run", "", stamp)
        return {"status": "dry_run", "detail": "只写了草稿，没有真发"}
    images = []
    if image is not None and Path(image).exists():
        images = [Path(image).resolve().as_uri()]
    ok, data = sender.request(
        "send_qzone_msg",
        {"content": text, "images": images, "ugc_right": 1},
    )
    if ok:
        tid = str((data or {}).get("tid") or "")
        guard.record("qzone", "post", target=reason, payload=text, result="ok", now=stamp)
        _save(store, text, image, tid, "ok", "", stamp)
        return {"status": "ok", "detail": tid}
    error = str((data or {}).get("message") or (data or {}).get("wording") or "接口失败")
    guard.record("qzone", "post", target=reason, payload=text, result="failed", error=error, now=stamp)
    _save(store, text, image, "", "failed", error, stamp)
    return {"status": "failed", "detail": error}


def _save(store, text: str, image: Path | None, tid: str, status: str, error: str, now: int) -> None:
    images = json.dumps([str(image)] if image else [], ensure_ascii=False)
    with store._lock:  # noqa: SLF001
        store._conn.execute(  # noqa: SLF001
            "INSERT INTO qzone_posts (content, images, tid, status, error, created_at) VALUES (?,?,?,?,?,?)",
            (text, images, tid, status, error, now),
        )
        store._conn.commit()  # noqa: SLF001


def last_posts(store, limit: int = 5) -> list[dict]:
    ensure(store)
    with store._lock:  # noqa: SLF001
        rows = store._conn.execute(  # noqa: SLF001
            "SELECT * FROM qzone_posts ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def maybe_post(store, llm, settings, guard, sender, now: int | None = None) -> dict | None:
    """定时入口：窗口内、今天没超额度，就写一条并（试运行地）发。"""
    if not getattr(settings, "qzone_enabled", False):
        return None
    stamp = int(now if now is not None else time.time())
    if not in_window(stamp):
        return None
    if _posted_today(store, stamp) >= int(getattr(settings, "qzone_daily_max", 3)):
        return None
    if not guard.check("qzone", "post", user_id=settings.allow_user_id, now=stamp).allowed:
        return None
    matter = draft(store, llm, settings, now=stamp)
    if not matter["text"]:
        return None
    return publish(store, guard, sender, matter["text"], matter["image"], now=stamp, reason="定时")


def tick_loop(store, llm, settings, guard, sender, stop_event, interval: float = 300.0) -> None:
    while not stop_event.wait(interval):
        try:
            result = maybe_post(store, llm, settings, guard, sender)
            if result is not None:
                log.info("空间：%s（%s）", result["status"], result["detail"][:60])
        except Exception:  # noqa: BLE001 - 空间失败不该拖垮主进程
            log.warning("空间定时任务失败", exc_info=True)
