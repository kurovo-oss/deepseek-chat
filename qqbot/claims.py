"""说说发布前的 claim 级事实校验。

大肥鱼会编，这没关系；但不能把编的事当成"我做过"发出去。参考
`dsh-dafeiyu-life` 的 `evidence_claims`：每条对外发言都要能对上真实证据，
对不上就不发，留给主人审。

这里不追求全语义判真，只拦最容易被读出来是假的那几类：
- 她根本没有的能力（拍摄、剪辑、投稿、直播、上班、线下见面……）
- 她从没看过的平台内容（B站/抖音/小红书，两个开关都是关的）
- 引用了"他说过的话"，但最近根本没说过
- 说"我发过说说 / 你发过表情"，但账本里没有

返回的每一条都是 {"kind", "quote", "ok", "reason"}，ok=False 就是不放行。
"""

from __future__ import annotations

import re
import time

WEEK = 7 * 86400

CAPABILITY = re.compile(
    r"(?:我|自己).{0,4}(?:拍了|拍过|剪了|剪过|投稿|直播|上台|线下见|加微信|打电话|"
    r"上班|加班|在公司|开会|同事|出差|去了他家|来我家)"
)

PLATFORM = re.compile(
    r"(?:我|自己).{0,8}(?:刷到|刷过|刷视频|刷抖音|刷B站|逛B站|逛抖音|刷小红书)"
    r"|(?:我|自己).{0,6}(?:看到|看过).{0,4}(?:视频|直播)"
)

QUOTE = re.compile(r"(?:他|你)(?:昨天|上次|之前|刚才|今天)?(?:说|讲|问|提)[^「“]{0,6}[「“]([^」”]{1,40})[」”]")

STICKER = re.compile(
    r"(?:你|他).{0,4}(?:发|给).{0,6}(?:表情|贴纸|图)"
    r"|(?:我|自己).{0,4}(?:收|存|抢).{0,6}(?:表情|贴纸|图)"
)

POSTED = re.compile(r"(?:我|自己).{0,6}(?:发|写|发过|写了).{0,4}(?:说说|空间)")


def _rows(store, sql: str, args: tuple):
    with store._lock:  # noqa: SLF001
        return store._conn.execute(sql, args).fetchall()  # noqa: SLF001


def _user_said(store, quote: str, now: int) -> bool:
    stamp = now - WEEK
    text = re.sub(r"[\s，。！？、,.!?「」“”\"']", "", quote)
    if not text:
        return False
    rows = _rows(
        store,
        "SELECT content FROM messages WHERE role = 'user' AND created_at >= ?"
        " ORDER BY id DESC LIMIT 400",
        (stamp,),
    )
    for row in rows:
        if text in re.sub(r"[\s，。！？、,.!?「」“”\"']", "", str(row["content"] or "")):
            return True
    return False


def _recent_images(store, now: int) -> int:
    rows = _rows(
        store,
        "SELECT COUNT(*) AS n FROM images WHERE created_at >= ? AND status IN ('ok','active')",
        (now - WEEK,),
    )
    return int(rows[0]["n"]) if rows else 0


def _recent_posts(store, now: int) -> int:
    try:
        rows = _rows(
            store,
            "SELECT COUNT(*) AS n FROM qzone_posts WHERE created_at >= ? AND status IN ('ok','dry_run')",
            (now - WEEK,),
        )
    except Exception:  # noqa: BLE001 - 空间表还没建就当没有
        return 0
    return int(rows[0]["n"]) if rows else 0


def verify(text: str, store, now: int | None = None) -> list[dict]:
    """把一段说说拆成 claim 并逐条对账。"""
    stamp = int(now if now is not None else time.time())
    body = str(text or "")
    claims: list[dict] = []

    for match in CAPABILITY.finditer(body):
        claims.append({
            "kind": "capability",
            "quote": match.group(0),
            "ok": False,
            "reason": "她做不到这件事（没有拍摄/剪辑/投稿/线下见面的能力）",
        })

    for match in PLATFORM.finditer(body):
        claims.append({
            "kind": "platform",
            "quote": match.group(0),
            "ok": False,
            "reason": "B站/抖音都没开，没有平台观看记录",
        })

    for match in QUOTE.finditer(body):
        quote = match.group(1)
        ok = _user_said(store, quote, stamp)
        claims.append({
            "kind": "quote",
            "quote": quote,
            "ok": ok,
            "reason": "" if ok else "他最近没说过这句话",
        })

    if STICKER.search(body):
        ok = _recent_images(store, stamp) > 0
        claims.append({
            "kind": "sticker",
            "quote": STICKER.search(body).group(0),
            "ok": ok,
            "reason": "" if ok else "最近没有收发过表情或图",
        })

    if POSTED.search(body):
        ok = _recent_posts(store, stamp) > 0
        claims.append({
            "kind": "post",
            "quote": POSTED.search(body).group(0),
            "ok": ok,
            "reason": "" if ok else "账本里最近没有发过说说",
        })

    return claims


def issues(claims: list[dict]) -> list[dict]:
    return [claim for claim in claims if not claim.get("ok")]
