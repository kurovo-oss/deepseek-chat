"""她的"生活线"。

为什么要有它：没有生活线的角色，每句话都挂在他身上——他说什么她答什么，
像客服，不像一个有自己日子的人。真人聊天会突然冒一句"我刚吃了个超酸的橘子"，
那句话跟上下文没关系，但它让对面相信"她真的在过日子"。

做法：每天生成一小份"今天"（做了什么、有点小麻烦、手边有什么、想跟他提的一件事），
存 `data/life/YYYY-MM-DD.md`，随 system prompt 注入。她想用就用，不想用也没关系。
"""

from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path

log = logging.getLogger("qqbot")

# 只输出几行短句：长了会被模型当成"设定"照抄，反而更假
PROMPT = """你在给一个角色写"她今天的日子"，用她自己的口吻。

角色：18 岁，有点娇羞、有点馋、画画很慢。住在他附近，白天一个人待着。
今天：{DATE}（{WEEKDAY}）。
{CONTEXT}

只输出 4 行，每行一句，最多 25 个字，第一人称，口语，不要编号、不要解释：
1. 今天做了什么（具体到一件事，比如"把窗帘洗了""画了一下午的鱼"）
2. 遇到的一个小麻烦或不顺（很小的事，比如"削笔刀找不到了"）
3. 现在手边有什么/正在干什么（具体的东西，比如"泡了第二杯茶""脚边有袋橘子"）
4. 想跟他提的一件小事（不是想他，是一件具体的事，比如"楼下新开了家面包店"）

要求：
- 像日记里随手写的，不是总结报告；允许有废话。
- 不要每行都提"他"；第 4 行才可以。
- 不要写心情总结（"今天心情不错"这种不要）。
- 不要出现"AI""模型""虚拟"这类字眼。"""

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _day_path(settings, day: date) -> Path:
    return Path(settings.life_dir) / f"{day.isoformat()}.md"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _clean(text: str, limit: int = 6) -> str:
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


def today(settings, llm, context: str = "", clock=time.time) -> str:
    """生成今天的日子并落盘。已经生成过就直接返回。失败返回空串。"""
    day = date.fromtimestamp(clock())
    path = _day_path(settings, day)
    cached = _read(path)
    if cached:
        return cached
    if llm is None:
        return ""

    prompt = PROMPT.replace("{DATE}", day.strftime("%Y年%m月%d日"))
    prompt = prompt.replace("{WEEKDAY}", WEEKDAYS[day.weekday()])
    prompt = prompt.replace("{CONTEXT}", context or "（没有什么特别的背景，自己编几件小事）")

    try:
        result = llm.chat(
            [
                {"role": "system", "content": "你在写角色日记，只输出几行短句。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.9,
            max_tokens=800,
        )
    except Exception:  # noqa: BLE001 - 生活线是加分项，失败就当今天没写
        log.exception("生成今天的日子失败")
        return ""

    text = _clean(result.text or "")
    # 模型偶尔会像聊天一样回一句"好呀。"——这种明显不是日子，别写进文件
    if not text or len(text) < 8 or len(text.splitlines()) < 2:
        log.warning("今天的日子不像日子，丢掉：%r", text[:40])
        return ""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    except OSError:
        log.exception("写今天的日子失败")
    log.info("今天的日子已生成：%s", day.isoformat())
    return text


def snippet(settings, clock=time.time, budget: int = 220) -> str:
    """进 system prompt 的那一段。没生成过就返回空串。"""
    day = date.fromtimestamp(clock())
    text = _read(_day_path(settings, day))
    if not text:
        return ""
    return "【你今天】\n" + text[:budget]


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
