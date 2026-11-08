"""她的「生活线」：只记今天真实发生过的事。

以前她每天自己挑一件事（做菜、收拾屋子）写日记，那些都是编的。
2026-11-08 起改成「只写有真实依据的」：没有真实素材就不生成，空间也不硬发。

存 `data/life/YYYY-MM-DD.md`，随 system prompt 注入。
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date
from pathlib import Path

from . import capability as capability_mod

log = logging.getLogger("qqbot")

PROMPT = """你在替「大肥鱼」写今天的小记，第一人称，口语，像随手记下来的。

只能写下面【今天真实发生】里出现过的事，以及她当时的感受。
不要编任何没发生的事：没出门就别写出门，没做饭就别写做饭，没买东西就别写买东西，没见谁就别写见谁。
不要写她的项目、计划、家务安排；不要提"AI""模型""用户"。
2~4 行，每行一句，不要标题、不要编号、不要总结。

【今天真实发生】
{EVENTS}

【他今天说的话（可以引用，别整段复读）】
{QUOTES}
"""

# 她不能凭空写的东西：只有他真的在聊天里提过/发生过，才算数。
# 命中这些词、但今天的真实素材里没有，就判为编造，重写；再犯就丢掉这一天。
_LIFE_PROPS = (
    "房间", "书桌", "桌子", "椅子", "阳台", "窗帘", "厨房", "锅", "灶", "碗",
    "洗衣", "晾", "拖把", "扫地", "抹布", "纸箱", "胶带", "螺丝", "拼图",
    "牛腩", "汤", "做菜", "超市", "便利店", "下楼", "出门", "上班", "公司",
    "地铁", "公交", "快递", "绿萝", "咖啡", "面包", "电视剧", "猫", "狗",
    "被子", "枕头", "衣柜", "冰箱", "买菜", "做饭",
)


def recent(settings, day: date, days: int = 2) -> str:
    """最近几天写过的日子，喂回去让它别重复。"""
    lines: list[str] = []
    for offset in range(1, days + 1):
        text = _read(_day_path(settings, day.fromordinal(day.toordinal() - offset)))
        if text:
            lines.append(text.replace("\n", " / "))
    if not lines:
        return ""
    return "最近两天已经写过这些，今天的别跟它们重复：\n- " + "\n- ".join(lines)


COOLDOWN_DAYS = 7


def _grams(text: str) -> set[str]:
    words: set[str] = set()
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text or ""):
        for index in range(len(chunk) - 1):
            gram = chunk[index:index + 2]
            if gram not in _STOP_GRAMS:
                words.add(gram)
    return words


def recent_words(settings, day: date, days: int = COOLDOWN_DAYS) -> set[str]:
    """最近几天日记里出现过的东西：今天别拿它们当主线。"""
    words: set[str] = set()
    for offset in range(1, days + 1):
        text = _read(_day_path(settings, day.fromordinal(day.toordinal() - offset)))
        words |= _grams(text)
    return words


def _day_start(day: date) -> int:
    return int(time.mktime(day.timetuple()))


def _today_rows(store, day: date) -> tuple[list, list, list]:
    """今天真实发生过的原始记录：消息、图片、发出去的说说。"""
    if store is None:
        return [], [], []
    start = _day_start(day)
    end = start + 86400
    try:
        with store._lock:  # noqa: SLF001
            messages = store._conn.execute(  # noqa: SLF001
                "SELECT role, content FROM messages WHERE created_at >= ? AND created_at < ?"
                " ORDER BY id DESC LIMIT 40",
                (start, end),
            ).fetchall()
            images = store._conn.execute(  # noqa: SLF001
                "SELECT tags, source FROM images WHERE created_at >= ? AND created_at < ?",
                (start, end),
            ).fetchall()
            try:
                posts = store._conn.execute(  # noqa: SLF001
                    "SELECT content FROM qzone_posts WHERE created_at >= ? AND created_at < ?"
                    " AND status IN ('ok','dry_run')",
                    (start, end),
                ).fetchall()
            except Exception:  # noqa: BLE001 - 空间表可能还没建
                posts = []
    except Exception:  # noqa: BLE001 - 读失败就当作没有素材
        return [], [], []
    return messages, images, posts


def _has_material(store, day: date) -> bool:
    """今天他有没有真的留下东西：一句话，或者一张图。"""
    if store is None:
        return False
    start = _day_start(day)
    end = start + 86400
    try:
        with store._lock:  # noqa: SLF001
            row = store._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) AS n FROM messages WHERE role = 'user'"
                " AND created_at >= ? AND created_at < ?",
                (start, end),
            ).fetchone()
            if row is not None and int(row["n"]) > 0:
                return True
            row = store._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) AS n FROM images WHERE source = 'user'"
                " AND created_at >= ? AND created_at < ?",
                (start, end),
            ).fetchone()
    except Exception:  # noqa: BLE001 - 表不全就当没有
        return False
    return row is not None and int(row["n"]) > 0


def day_events(store, settings, day: date) -> list[str]:
    """今天真实发生过的事——小记只能围着这些写。"""
    messages, images, posts = _today_rows(store, day)
    out: list[str] = []
    mine = [row for row in messages if row["role"] == "user"]
    if mine:
        last = str(mine[0]["content"] or "").replace("\n", " ").strip()
        out.append(f"他今天找你说过 {len(mine)} 次话，最后一句是「{last[:24]}」")
    tags: list[str] = []
    for row in images:
        try:
            tags.extend(str(tag) for tag in json.loads(row["tags"] or "[]"))
        except ValueError:
            continue
    if images:
        label = "、".join(dict.fromkeys(tags))[:24]
        out.append(f"你今天收了 {len(images)} 张图{f'（{label}）' if label else ''}")
    if posts:
        first = str(posts[0]["content"] or "").replace("\n", " ").strip()
        out.append(f"你发过 {len(posts)} 条说说，开头是「{first[:20]}」")
    return out


def _quotes(store, day: date, limit: int = 6) -> list[str]:
    """他今天说过的原话，旧到新，给小记当证据。"""
    if store is None:
        return []
    start = _day_start(day)
    try:
        with store._lock:  # noqa: SLF001
            rows = store._conn.execute(  # noqa: SLF001
                "SELECT content FROM messages WHERE role = 'user'"
                " AND created_at >= ? AND created_at < ? ORDER BY id DESC LIMIT ?",
                (start, start + 86400, limit),
            ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    quotes = [str(row["content"] or "").replace("\n", " ").strip()[:60] for row in rows]
    return [quote for quote in reversed(quotes) if quote]


def _invented(body: str, evidence: str) -> list[str]:
    """小记里出现的具体东西，如果今天的真实素材里没有，就是编的。"""
    return [word for word in _LIFE_PROPS if word in body and word not in evidence]


def invented(text: str, evidence: str) -> list[str]:
    """外部（比如空间）也能用同一套标准检查有没有编造。"""
    return _invented(text, evidence)


def _day_path(settings, day: date) -> Path:
    return Path(settings.life_dir) / f"{day.isoformat()}.md"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _clean(text: str, limit: int = 4) -> str:
    """只要前几行短句，去掉编号和引号。"""
    lines: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-•*·").strip()
        line = line.lstrip("0123456789.、)） ").strip()
        line = line.strip("「」\"'“”")
        if not line:
            continue
        lines.append(line[:60])
        if len(lines) >= limit:
            break
    return "\n".join(lines)


def _world(settings) -> str:
    """今天能写什么、不能写什么：系统做不到的事，日子也不能写。"""
    forbidden = capability_mod.cannot(settings)
    if not forbidden:
        return ""
    return (
        "你现在做不到：" + "、".join(forbidden) + "。"
        "今天的小记别写这些——别写\"刷到一个视频\"\"在网上看到\"\"我拍下来了\"，那是编的。"
    )


def today(settings, llm, context: str = "", clock=time.time, store=None) -> str:
    """写今天的真实小记并落盘。没有真实素材就不写，已经写过就直接返回。"""
    day = date.fromtimestamp(clock())
    path = _day_path(settings, day)
    cached = _read(path)
    if cached:
        return cached
    if llm is None:
        return ""
    if not _has_material(store, day):
        log.info("今天没有真实素材，生活线不生成")
        return ""

    events = day_events(store, settings, day)
    quotes = _quotes(store, day)
    evidence = "\n".join(events + quotes)
    prompt = PROMPT.format(
        EVENTS="\n".join(f"- {event}" for event in events) or "（没有）",
        QUOTES="\n".join(f"- 「{quote}」" for quote in quotes) or "（没有）",
    )
    world = _world(settings)
    if world:
        prompt = world + "\n\n" + prompt

    try:
        result = llm.chat(
            [
                {"role": "system", "content": "你在替一个角色写今天的小记，只写真实发生过的事。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            # 推理模型的思考也算 token：给少了会偶发空回复
            max_tokens=800,
        )
    except Exception:  # noqa: BLE001 - 生活线是加分项，失败就当今天没写
        log.exception("生成今天的小记失败")
        return ""

    body = _clean(result.text or "")
    if not body or len(body.splitlines()) < 2:
        log.warning("今天的小记不像样子，丢掉：%r", (result.text or "")[:60])
        return ""

    bad = _invented(body, evidence)
    if bad:
        ban = "、".join(bad)
        retry_prompt = prompt + f"\n\n【重写】上一版写了今天没发生的事：{ban}。删掉它们，只写真实发生的。"
        try:
            again = llm.chat(
                [
                    {"role": "system", "content": "你在替一个角色写今天的小记，只写真实发生过的事。"},
                    {"role": "user", "content": retry_prompt},
                ],
                temperature=0.8,
                max_tokens=800,
            )
        except Exception:  # noqa: BLE001 - 重写失败就丢掉
            again = None
        candidate = _clean(getattr(again, "text", "") or "") if again is not None else ""
        if candidate and len(candidate.splitlines()) >= 2 and not _invented(candidate, evidence):
            body = candidate
        else:
            log.warning("今天的小记写了没依据的事，丢掉：%r", ban)
            return ""

    if capability_mod.claims_disabled(body, settings):
        log.warning("她写了自己做不到的事，丢掉：%r", body[:60])
        return ""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body + "\n", encoding="utf-8")
    except OSError:
        log.exception("写今天的小记失败")
    log.info("今天的小记已生成：%s", day.isoformat())
    return body


# 判断"她今天已经提过这事没有"时用的词：太泛的二合字不算
_STOP_GRAMS = frozenset(
    (
        "今天", "昨天", "明天", "早上", "上午", "中午", "下午", "晚上", "现在", "刚才",
        "一个", "这个", "那个", "什么", "有点", "还是", "已经", "然后", "就是", "觉得",
        "时候", "自己", "他们", "我们", "你们", "因为", "所以", "但是", "不过", "而且",
        "如果", "其实", "一下", "一点", "一会", "起来", "出来", "看着", "盯着", "在灶",
        "手边", "试着", "想再", "这回", "下回", "结果", "感觉",
    )
)


def keywords(settings, clock=time.time) -> set[str]:
    """今天这篇小记的"关键词"：从第一行里取，够具体才认。"""
    text = _read(_day_path(settings, date.fromtimestamp(clock())))
    seeds = [text.splitlines()[0] if text else ""]
    words: set[str] = set()
    for chunk in re.findall(r"[\u4e00-\u9fff]+", " ".join(seeds)):
        for index in range(len(chunk) - 1):
            gram = chunk[index : index + 2]
            if gram not in _STOP_GRAMS:
                words.add(gram)
    return words


def mentions_today(store, session: str, settings, clock=time.time) -> int:
    """她今天已经提过这件事几次——用来按住"对了，牛腩……"这种硬拐。"""
    words = keywords(settings, clock)
    if not words or store is None:
        return 0
    day = date.fromtimestamp(clock())
    midnight = _day_start(day)
    count = 0
    for row in store.recent_messages(session, 200):
        if row.get("role") != "assistant" or int(row.get("created_at") or 0) < midnight:
            continue
        content = str(row.get("content") or "")
        if any(word in content for word in words):
            count += 1
    return count


def snippet(settings, clock=time.time, budget: int = 220, repeat: int = 0) -> str:
    """进 system prompt 的那一段。没生成过就返回空串。

    `repeat` 是"今天已经提过几次"：说太多了就在末尾压一句，别让她一天到晚
    拿同一件小事当转折——那是"话题转移太刻意"的来源。
    """
    day = date.fromtimestamp(clock())
    text = _read(_day_path(settings, day))
    if not text:
        return ""
    if repeat >= 3:
        # 说第三遍以上：这一段先收起来，不然她一有机会就往上靠
        return ""
    note = ""
    if repeat >= 2:
        note = f"\n（这件事今天已经说过 {repeat} 次了——这轮别提它，先接住他的话。）"
    return "【你今天】\n" + text[:budget] + note


def prune(settings, keep: int | None = None) -> int:
    """只留最近几天的，别让 data/life 长成一片草原。"""
    limit = keep if keep is not None else int(getattr(settings, "life_keep_days", 14))
    folder = Path(settings.life_dir)
    if not folder.exists():
        return 0
    files = sorted(folder.glob("*.md"))
    removed = 0
    for path in files[:-limit] if limit > 0 else []:
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed
