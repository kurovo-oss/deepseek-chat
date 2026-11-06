"""QQ 空间：一天最多一条，可以配一张当天发过的图，默认只写草稿（试运行）。

真发走 NapCat 的扩展动作 `send_qzone_msg`（需要 NapCat ≥ 4.18.42）。
"""

from __future__ import annotations

import json
import logging
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
    )
    result = llm.chat(
        [{"role": "system", "content": prompt}, {"role": "user", "content": "写吧。"}],
        temperature=0.85,
        max_tokens=600,
    )
    text = _clean(getattr(result, "text", "") or "")
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
