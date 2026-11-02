"""她的"生活线"。

为什么要有它：没有生活线的角色，每句话都挂在他身上——他说什么她答什么，
像客服，不像一个有自己日子的人。真人聊天会突然冒一句"我刚吃了个超酸的橘子"。

**现在是她自己选的**：以前这里是一份写死的菜单（`BUSY_THINGS` 按日期轮换），
她负责把指定那件事写成日记——那是填表。现在每天她自己决定推进哪件事、
开新的还是放下，并说一句为什么；选择落到 `qqbot.projects`，明天还在。

存 `data/life/YYYY-MM-DD.md`，随 system prompt 注入。她想用就用，不想用也没关系。
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date
from pathlib import Path

from . import projects as projects_mod

log = logging.getLogger("qqbot")

# 只输出几行短句：长了会被模型当成"设定"照抄，反而更假
PROMPT = """你在给一个角色写"她今天的日子"，用她自己的口吻。

角色：18 岁，有点娇羞、有点馋，手上总有点小事。住在他附近，白天一个人待着。

**你不是在填别人给的表。** 今天做什么、推进哪件事、要不要开一件新的、
要不要把某件放下——你自己定；选完说清楚为什么，别只是为了不一样而不一样。

{PROJECTS}

今天：{DATE}（{WEEKDAY}）。
{CONTEXT}
{PAST}

只输出一个 JSON，不要解释、不要代码块：
{{"action": "advance",
 "project": "手上那件事的名字",
 "note": "今天到哪一步了，一句，最多 20 字",
 "reason": "为什么今天选它，第一人称，一句，最多 20 字",
 "today": ["今天做了什么", "遇到的小麻烦", "现在手边有什么", "想跟他提的一件小事"]}}

action 只能是 advance / start / pause / drop / done / none：
- advance 推进一件已有的；start 开一件新的（project 写新名字）；
- pause 先放下；drop 以后不做了；done 做完了；none 今天就想干点别的。
- today 四句要具体、口语，像日记随手写的；不要写心情总结；
  前三句别提"他"，第四句可以。
- 不要出现"AI""模型""虚拟"这类字眼。"""

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 她可以开的新事：只是灵感，不是菜单
BUSY_THINGS = (
    "学做一道没做过的菜",
    "把房间重新布置一下",
    "改简历，想换个工作",
    "追完那部剧",
    "给朋友挑生日礼物",
    "研究怎么冲咖啡",
    "把相册整理进硬盘",
    "把那盆快死的绿萝救回来",
)


def busy_things(day: date, count: int = 2) -> str:
    """按日期给几条"可以试试的新事"当灵感。不再决定她今天做什么。"""
    start = day.toordinal()
    picked = [BUSY_THINGS[(start + index) % len(BUSY_THINGS)] for index in range(count)]
    return "、".join(picked)


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


def parse_decision(raw: str) -> dict | None:
    """把她那次选择解析出来；不是 JSON 就返回 None（当纯日记收下，不更新项目）。"""
    text = (raw or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None

    today = data.get("today")
    if isinstance(today, list):
        data["today"] = [str(item).strip() for item in today if str(item).strip()]
    elif isinstance(today, str):
        data["today"] = [line.strip() for line in today.splitlines() if line.strip()]
    else:
        data["today"] = []

    action = str(data.get("action") or "none").strip().lower()
    if action not in ("advance", "start", "pause", "drop", "done", "none"):
        action = "none"
    data["action"] = action
    return data


def _salvage(text: str) -> dict | None:
    """JSON 被截断时的抢救：把还能认出来的字段抠出来，别丢掉一整天。"""
    if not text.lstrip().startswith("{"):
        return None
    out: dict = {"action": "none"}
    for key in ("action", "project", "note", "reason"):
        found = re.search(rf'"{key}"\s*:\s*"([^"]*)"', text)
        if found:
            out[key] = found.group(1)
    if out["action"] not in ("advance", "start", "pause", "drop", "done", "none"):
        out["action"] = "none"
    block = re.search(r'"today"\s*:\s*\[(.*)', text, re.S)
    lines: list[str] = []
    if block:
        for item in re.findall(r'"([^"]+)"', block.group(1)):
            if item.strip():
                lines.append(item.strip())
    out["today"] = lines[:4]
    return out if lines else None


def _compose(decision: dict, lines: str) -> str:
    """把"她自己挑的 + 为什么"放在最前面：她读到时，那是她的决定，不是别人的安排。"""
    title = str(decision.get("project") or "").strip()
    reason = str(decision.get("reason") or "").strip()
    action = decision.get("action")
    tail = f"（{reason}）" if reason else ""
    if action == "none" or not title:
        head = "今天没特意推进手上的事"
    elif action == "start":
        head = f"今天开了件新事：{title}{tail}"
    elif action in ("pause", "drop", "done"):
        head = f"把「{title}」放下了{tail}"
    else:
        head = f"今天挑的是：{title}{tail}"
    return head + "\n" + lines


def today(settings, llm, context: str = "", clock=time.time) -> str:
    """生成今天的日子并落盘，同时更新她手上的事。已经生成过就直接返回。"""
    day = date.fromtimestamp(clock())
    path = _day_path(settings, day)
    cached = _read(path)
    if cached:
        return cached
    if llm is None:
        return ""

    items = projects_mod.load(settings)
    prompt = PROMPT.replace("{DATE}", day.strftime("%Y年%m月%d日"))
    prompt = prompt.replace("{WEEKDAY}", WEEKDAYS[day.weekday()])
    prompt = prompt.replace("{CONTEXT}", context or "（没有什么特别的背景，自己写几件小事）")
    prompt = prompt.replace("{PROJECTS}", projects_mod.block(items, BUSY_THINGS))
    prompt = prompt.replace("{PAST}", recent(settings, day))

    try:
        result = llm.chat(
            [
                {
                    "role": "system",
                    "content": "你在替一个角色安排今天并写日记。只输出一个 JSON。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.9,
            # 推理模型的思考也算 token：给 800 会偶发"空回复"，一天就白丢了
            max_tokens=2000,
        )
    except Exception:  # noqa: BLE001 - 生活线是加分项，失败就当今天没写
        log.exception("生成今天的日子失败")
        return ""

    decision = parse_decision(result.text or "") or _salvage(result.text or "")
    if decision is not None:
        lines = _clean("\n".join(decision["today"]))
        if not lines or len(lines.splitlines()) < 2:
            log.warning("她今天的选择里没有像样的日子，丢掉：%r", (result.text or "")[:60])
            return ""
        try:
            projects_mod.save(settings, projects_mod.apply(items, decision, day.isoformat()))
        except OSError:
            log.exception("保存她手上的事失败")
        text = _compose(decision, lines)
    else:
        # 模型没按 JSON 来：当纯日记收下，但不更新项目（宁可不记，也别记错）
        text = _clean(result.text or "")
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
