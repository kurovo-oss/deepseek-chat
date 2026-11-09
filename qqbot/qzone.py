"""QQ 空间：一天最多一条，可以配一张当天发过的图，默认只写草稿（试运行）。

真发走 NapCat 的扩展动作 `send_qzone_msg`（需要 NapCat ≥ 4.18.42）。
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from . import claims as claims_mod
from . import digest as digest_mod
from . import guard as guard_mod
from . import growth as growth_mod
from . import life as life_mod
from . import mood as mood_mod

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
  review     TEXT NOT NULL DEFAULT '',
  claims     TEXT NOT NULL DEFAULT '[]',
  created_at INTEGER NOT NULL
);
"""

PROMPT = """你是「大肥鱼」，正在写今天的 QQ 空间说说。第一人称，像跟熟人说话，长度按下面【这条怎么写】来。

写今天真正发生的一两件事，加上你当时的感受。不要标题、不要分点、不要"今天真是充实的一天"这种套话，
不要提"AI""模型""用户"，不要 @ 人，不要放链接。
今天没聊什么就写短一点，别编自己的活动、地点、物品；没发生过的事不要写。

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

【这条怎么写】
{style_label}，{style_min}~{style_max} 字。{style_guide}

同一件事别连着写：上面写过的场景、菜、物件，这条换掉，别又写同一锅汤。只输出说说正文。"""


def ensure(store) -> None:
    if getattr(store, "_qzone_ready", False):
        return
    with store._lock:  # noqa: SLF001
        store._conn.executescript(_SCHEMA)  # noqa: SLF001
        columns = {row["name"] for row in store._conn.execute("PRAGMA table_info(qzone_posts)")}
        for name, ddl in (("review", "TEXT NOT NULL DEFAULT ''"), ("claims", "TEXT NOT NULL DEFAULT '[]'")):
            if name not in columns:
                store._conn.execute(f"ALTER TABLE qzone_posts ADD COLUMN {name} {ddl}")  # noqa: S608
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


def _has_today_material(store, now: int) -> bool:
    """今天有没有真实素材：他说过话，或者发过图。没有就不写、不发。"""
    start = _today_start(now)
    with store._lock:  # noqa: SLF001
        row = store._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) AS n FROM messages WHERE role = 'user'"
            " AND created_at >= ? AND created_at < ?",
            (start, start + 86400),
        ).fetchone()
        if row is not None and int(row["n"]) > 0:
            return True
        row = store._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) AS n FROM images WHERE source = 'user'"
            " AND created_at >= ? AND created_at < ?",
            (start, start + 86400),
        ).fetchone()
    return row is not None and int(row["n"]) > 0


def _today_evidence(store, now: int) -> str:
    """今天的真实素材：他说的话 + 他发的图的标签。"""
    start = _today_start(now)
    with store._lock:  # noqa: SLF001
        rows = store._conn.execute(  # noqa: SLF001
            "SELECT content FROM messages WHERE role = 'user'"
            " AND created_at >= ? AND created_at < ?",
            (start, start + 86400),
        ).fetchall()
        images = store._conn.execute(  # noqa: SLF001
            "SELECT tags FROM images WHERE source = 'user'"
            " AND created_at >= ? AND created_at < ?",
            (start, start + 86400),
        ).fetchall()
    parts = [str(row["content"] or "") for row in rows]
    for row in images:
        try:
            parts.extend(str(tag) for tag in json.loads(row["tags"] or "[]"))
        except ValueError:
            continue
    return "\n".join(part for part in parts if part)


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




STYLES = {
    "mumble": {
        "label": "随口碎碎念",
        "min": 20,
        "max": 90,
        "guide": "像随手冒出来的一句话：一件小事、一个小念头，轻轻说完就好，不用解释意义。",
    },
    "feeling": {
        "label": "小情绪",
        "min": 20,
        "max": 100,
        "guide": "让此刻的心情决定语气，只说当下的感受，别编情绪的原因。",
    },
    "playful": {
        "label": "俏皮吐槽",
        "min": 20,
        "max": 100,
        "guide": "可以好奇、嘴硬、撒娇、轻微自嘲，像平时说话，别硬塞口癖。",
    },
    "serious": {
        "label": "偶尔认真",
        "min": 50,
        "max": 200,
        "guide": "难得认真说一件真正触动你的事，只谈一个点，别写成议论文。",
    },
}

STYLE_ROTATION = ("mumble", "feeling", "playful", "mumble", "feeling", "mumble", "playful", "serious")

_SUMMARY_TONE = re.compile(r"首先|其次|总之|归根结底|这让我意识到|由此可见|从.+角度来看")
_CLAIMS = re.compile(r"我.{0,3}(?:拍|剪|投稿|上传)|线下见|加个微信|加微信")
_SENSITIVE = re.compile(r"https?://|www\.|\b\d{6,}\b|[\w.+-]+@[\w-]+\.[\w.]+")


def pick_style(store) -> str:
    ensure(store)
    with store._lock:  # noqa: SLF001
        row = store._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) AS n FROM qzone_posts WHERE status IN ('ok','dry_run')"
        ).fetchone()
    count = int(row["n"]) if row is not None else 0
    return STYLE_ROTATION[count % len(STYLE_ROTATION)]


def content_issue(text: str, style: str) -> str:
    spec = STYLES[style]
    body = (text or "").strip()
    if _SUMMARY_TONE.search(body):
        return "别用总结腔（首先/总之/这让我意识到这类）"
    if _CLAIMS.search(body):
        return "别写你做不到的事（拍摄/剪辑/投稿/线下见面）"
    if _SENSITIVE.search(body):
        return "别放链接、手机号或邮箱"
    if len(body) < spec["min"]:
        return f"太短了（{len(body)} 字，要 {spec['min']} 字以上）"
    if len(body) > spec["max"]:
        return f"太长了（{len(body)} 字，最多 {spec['max']} 字）"
    return ""


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


def draft(store, llm, settings, now: int | None = None, mood=None) -> dict:
    stamp = int(now if now is not None else time.time())
    mood_text = mood_mod.mood_line(mood.snapshot(stamp)) if mood is not None else ""
    style = pick_style(store)
    spec = STYLES[style]
    prompt = PROMPT.format(
        # 只给今天：旧摘要会被模型当成"今天"扩写（11-07 八条复读就是这么来的）
        digest=digest_mod.recent_text(settings, days=1) or "（今天没聊多少）",
        life=life_mod.snippet(settings) or "（今天还没写什么）",
        events=_events_block(store),
        mood=mood_text or "（还行）",
        recent=_recent_block(store),
        banned=_banned_topics(_recent_posts(store)),
        style_label=spec["label"],
        style_min=spec["min"],
        style_max=spec["max"],
        style_guide=spec["guide"],
    )
    recent_posts = _recent_posts(store)
    banned = _banned_list(recent_posts)
    result = llm.chat(
        [{"role": "system", "content": prompt}, {"role": "user", "content": "写吧。"}],
        temperature=0.85,
        max_tokens=600,
        purpose="qzone",
    )
    text = _clean(getattr(result, "text", "") or "")
    issue = content_issue(text, style)
    if issue:
        fix = prompt + f"\n\n【重写】上一版的问题：{issue}。按上面的风格和字数重写。"
        try:
            again = llm.chat(
                [{"role": "system", "content": fix}, {"role": "user", "content": "写吧。"}],
                temperature=0.9,
                max_tokens=600,
                purpose="qzone",
            )
            fixed = _clean(getattr(again, "text", "") or "")
        except Exception:  # noqa: BLE001 - 重写失败就当这条不发了
            fixed = ""
        text = fixed if fixed and not content_issue(fixed, style) else ""
    repeated = [word for word in banned if word in text]
    if repeated:
        # 写了最近重复的事：再要一版，点名不许提
        retry = prompt + "\n\n【重写】上一版又写了：" + "、".join(repeated) + "。这一版一个字都别提这些，换今天别的两件事写。"
        try:
            again = llm.chat(
                [{"role": "system", "content": retry}, {"role": "user", "content": "写吧。"}],
                temperature=0.95,
                max_tokens=600,
                purpose="qzone",
            )
        except Exception:  # noqa: BLE001 - 重写失败就用第一版
            again = None
        retried = _clean(getattr(again, "text", "") or "") if again is not None else ""
        still = [word for word in repeated if word in retried]
        if retried and not still:
            text = retried
    evidence = _today_evidence(store, stamp)
    if text and evidence:
        invented = life_mod.invented(text, evidence)
        if invented:
            fix = prompt + (
                "\n\n【重写】上一版写了今天没发生的事："
                + "、".join(invented)
                + "。删掉它们，只写今天真实发生过的。"
            )
            try:
                again = llm.chat(
                    [{"role": "system", "content": fix}, {"role": "user", "content": "写吧。"}],
                    temperature=0.9,
                    max_tokens=600,
                    purpose="qzone",
                )
                fixed = _clean(getattr(again, "text", "") or "")
            except Exception:  # noqa: BLE001 - 重写失败就当这条不发了
                fixed = ""
            text = fixed if fixed and not content_issue(fixed, style) and not life_mod.invented(fixed, evidence) else ""
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
            now: int | None = None, reason: str = "", claims: list | None = None) -> dict:
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
        _save(store, text, image, "", "dry_run", "", stamp, claims=claims)
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
        _save(store, text, image, tid, "ok", "", stamp, claims=claims)
        return {"status": "ok", "detail": tid}
    error = str((data or {}).get("message") or (data or {}).get("wording") or "接口失败")
    guard.record("qzone", "post", target=reason, payload=text, result="failed", error=error, now=stamp)
    _save(store, text, image, "", "failed", error, stamp, claims=claims)
    return {"status": "failed", "detail": error}


def _save(store, text: str, image: Path | None, tid: str, status: str, error: str, now: int,
          review: str = "", claims: list | None = None) -> int:
    images = json.dumps([str(image)] if image else [], ensure_ascii=False)
    ledger = json.dumps(claims or [], ensure_ascii=False)
    with store._lock:  # noqa: SLF001
        cursor = store._conn.execute(  # noqa: SLF001
            "INSERT INTO qzone_posts (content, images, tid, status, error, review, claims, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (text, images, tid, status, error, review, ledger, now),
        )
        store._conn.commit()  # noqa: SLF001
        return int(cursor.lastrowid or 0)


def _get(store, post_id: int) -> dict | None:
    ensure(store)
    with store._lock:  # noqa: SLF001
        row = store._conn.execute(  # noqa: SLF001
            "SELECT * FROM qzone_posts WHERE id = ?", (int(post_id),)
        ).fetchone()
    return dict(row) if row is not None else None


def drafts(store, limit: int = 10) -> list[dict]:
    ensure(store)
    with store._lock:  # noqa: SLF001
        rows = store._conn.execute(  # noqa: SLF001
            "SELECT * FROM qzone_posts WHERE status = 'draft' ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def _update(store, post_id: int, **fields) -> None:
    if not fields:
        return
    keys = ", ".join(f"{name} = ?" for name in fields)
    with store._lock:  # noqa: SLF001
        store._conn.execute(  # noqa: SLF001
            f"UPDATE qzone_posts SET {keys} WHERE id = ?",  # noqa: S608 - 列名是内部常量
            (*fields.values(), int(post_id)),
        )
        store._conn.commit()  # noqa: SLF001


def save_draft(store, matter: dict, now: int, claims: list, review: str) -> int:
    ensure(store)
    return _save(
        store, matter["text"], matter.get("image"), "", "draft", "", now,
        review=review, claims=claims,
    )


def delete_draft(store, post_id: int) -> bool:
    ensure(store)
    with store._lock:  # noqa: SLF001
        cursor = store._conn.execute(  # noqa: SLF001
            "DELETE FROM qzone_posts WHERE id = ? AND status = 'draft'", (int(post_id),)
        )
        store._conn.commit()  # noqa: SLF001
    return int(cursor.rowcount or 0) > 0


def last_posts(store, limit: int = 5) -> list[dict]:
    ensure(store)
    with store._lock:  # noqa: SLF001
        rows = store._conn.execute(  # noqa: SLF001
            "SELECT * FROM qzone_posts ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def _pending_draft(store, now: int) -> bool:
    ensure(store)
    with store._lock:  # noqa: SLF001
        row = store._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) AS n FROM qzone_posts WHERE status = 'draft' AND created_at >= ?",
            (_today_start(now),),
        ).fetchone()
    return bool(row and int(row["n"]) > 0)


def approve_draft(store, guard, sender, post_id: int, now: int | None = None) -> dict:
    """把一条待审草稿过一遍 claim 校验，过了才发。"""
    ensure(store)
    stamp = int(now if now is not None else time.time())
    row = _get(store, post_id)
    if row is None:
        return {"status": "missing", "detail": f"没有 #{post_id}"}
    if row["status"] != "draft":
        return {"status": "not_draft", "detail": f"#{post_id} 是 {row['status']}，不是草稿"}
    ledger = claims_mod.verify(str(row["content"]), store, now=stamp)
    bad = claims_mod.issues(ledger)
    ledger_text = json.dumps(ledger, ensure_ascii=False)
    if bad:
        review = "；".join(claim["reason"] for claim in bad)
        _update(store, post_id, review=review, claims=ledger_text)
        guard.record("qzone", "approve", payload=str(row["content"]), result="held", error=review, now=stamp)
        return {"status": "held", "detail": review}
    decision = guard.check("qzone", "post", user_id=guard.settings.allow_user_id, now=stamp)
    if not decision.allowed:
        _update(store, post_id, review=decision.reason)
        return {"status": "denied", "detail": decision.reason}
    if decision.dry_run:
        _update(store, post_id, status="dry_run", review="", claims=ledger_text)
        return {"status": "dry_run", "detail": "只写了草稿，没有真发"}
    images = []
    try:
        paths = json.loads(row["images"] or "[]")
    except ValueError:
        paths = []
    for path in paths:
        candidate = Path(str(path))
        if candidate.exists():
            images.append(candidate.resolve().as_uri())
    ok, data = sender.request("send_qzone_msg", {"content": row["content"], "images": images, "ugc_right": 1})
    if ok:
        tid = str((data or {}).get("tid") or "")
        _update(store, post_id, status="ok", tid=tid, error="", review="", claims=ledger_text)
        guard.record("qzone", "post", payload=str(row["content"]), result="ok", now=stamp)
        return {"status": "ok", "detail": tid}
    error = str((data or {}).get("message") or (data or {}).get("wording") or "接口失败")
    _update(store, post_id, status="failed", error=error, review="", claims=ledger_text)
    guard.record("qzone", "post", payload=str(row["content"]), result="failed", error=error, now=stamp)
    return {"status": "failed", "detail": error}


def maybe_post(store, llm, settings, guard, sender, now: int | None = None, mood=None) -> dict | None:
    """定时入口：窗口内、今天没超额度，就写一条并（试运行地）发。"""
    if not getattr(settings, "qzone_enabled", False):
        return None
    stamp = int(now if now is not None else time.time())
    if not in_window(stamp):
        return None
    if _posted_today(store, stamp) >= int(getattr(settings, "qzone_daily_max", 3)):
        return None
    if _pending_draft(store, stamp):
        return None
    if not _has_today_material(store, stamp):
        # 只写有真实依据的：今天没聊、没图，就不硬造一条
        log.info("空间：今天没有真实素材，跳过")
        return None
    if not guard.check("qzone", "post", user_id=settings.allow_user_id, now=stamp).allowed:
        return None
    matter = draft(store, llm, settings, now=stamp, mood=mood)
    if not matter["text"]:
        return None
    ledger = claims_mod.verify(matter["text"], store, now=stamp)
    bad = claims_mod.issues(ledger)
    if bad:
        review = "；".join(claim["reason"] for claim in bad)
        post_id = save_draft(store, matter, stamp, ledger, review)
        guard.record("qzone", "post", target="定时", payload=matter["text"], result="held", error=review, now=stamp)
        log.info("空间：草稿 #%s 待审（%s）", post_id, review)
        try:
            if hasattr(sender, "send_private"):
                sender.send_private(
                    int(settings.allow_user_id),
                    f"今晚这条说说没过事实校验，先存成草稿 #{post_id}：{review}\n"
                    f"/空间 发 {post_id} 通过，/空间 删 {post_id} 删掉",
                )
        except Exception:  # noqa: BLE001 - 通知失败不影响草稿
            log.warning("空间：待审通知没发出去", exc_info=True)
        return {"status": "held", "detail": f"草稿 #{post_id} 待审：{review}"}
    return publish(store, guard, sender, matter["text"], matter["image"], now=stamp, reason="定时", claims=ledger)


def tick_loop(store, llm, settings, guard, sender, stop_event, interval: float = 300.0, mood=None, runtime=None) -> None:
    while not stop_event.wait(interval):
        if runtime is not None and not runtime.enabled("qzone"):
            continue
        try:
            result = maybe_post(store, llm, settings, guard, sender, mood=mood)
            if result is not None:
                log.info("空间：%s（%s）", result["status"], result["detail"][:60])
        except Exception:  # noqa: BLE001 - 空间失败不该拖垮主进程
            log.warning("空间定时任务失败", exc_info=True)
