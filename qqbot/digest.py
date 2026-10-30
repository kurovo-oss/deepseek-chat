"""日摘要：把一天说过的话压成一页，好让她答得出"上周发生了什么"。

为什么要有它：原始对话太多（一天几百条），塞不进 prompt；
而记忆卡片抽的是**结论**（"他不吃香菜"），不是**经过**（"昨天面试怎么样"）。
中间这一层——按天记的流水——正是"上周"这种问题唯一答得上的地方。

生成时机：
- `/digest` 手动
- 启动时补最近几天没做的（限 `digest_catchup_days` 天，别把模型额度一次烧光）
- 每条摘要落一个 `data/daily/YYYY-MM-DD.md`，存在就不再生成（幂等，重启不重复烧钱）
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from .formatting import fix_address

log = logging.getLogger("qqbot")

DAY_FORMAT = "%Y-%m-%d"
MAX_SUMMARY_CHARS = 700
RECENT_TITLE = "# 最近几天（摘要）"


def day_of(stamp: float | int) -> date:
    return datetime.fromtimestamp(int(stamp)).date()


def bounds(day: date) -> tuple[int, int]:
    """一天的起止时间戳（左闭右开，按本地时区）。"""
    start = datetime(day.year, day.month, day.day)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def digest_dir(settings) -> Path:
    return Path(settings.digest_dir)


def path_for(settings, day: date) -> Path:
    return digest_dir(settings) / f"{day.strftime(DAY_FORMAT)}.md"


def read(settings, day: date) -> str | None:
    path = path_for(settings, day)
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:  # noqa: BLE001
        log.exception("读日摘要失败：%s", path)
        return None


def write(settings, day: date, title: str, text: str) -> Path:
    directory = digest_dir(settings)
    directory.mkdir(parents=True, exist_ok=True)
    path = path_for(settings, day)
    body = f"# {day.strftime(DAY_FORMAT)} {title}\n\n{text.strip()}\n"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)  # 原子替换：不留半截文件
    return path


def format_messages(rows: list[dict], limit: int = 4000) -> str:
    """把一天的对话排成"谁说了什么"。指令消息不算聊天内容。"""
    lines: list[str] = []
    for row in rows:
        content = (row.get("content") or "").strip()
        if not content or content.startswith("/"):
            continue
        who = "他" if row.get("role") == "user" else "她"
        stamp = datetime.fromtimestamp(int(row.get("created_at") or 0)).strftime("%H:%M")
        lines.append(f"[{stamp}] {who}：{content}")
    text = "\n".join(lines)
    return text[:limit]


def _load_prompt(settings) -> str:
    path = Path(settings.digest_prompt_file)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:  # noqa: BLE001
        log.warning("读不到日摘要提示词：%s", path)
        return "用 150~300 字总结这一天聊了什么：{{MESSAGES}}"


def generate(store, llm, settings, day: date) -> str | None:
    """生成某天的摘要并落盘；那天没说话就返回 None。"""
    start, end = bounds(day)
    rows = store.messages_between(start, end)
    body = format_messages(rows)
    if not body.strip():
        return None

    prompt = (
        _load_prompt(settings)
        .replace("{{DATE}}", day.strftime(DAY_FORMAT))
        .replace("{{MESSAGES}}", body)
    )
    result = llm.chat(
        [
            {"role": "system", "content": "你在写日记，只记事实，不加评论。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=2000,
    )
    text = (result.text or "").strip()
    if not text:
        log.warning("日摘要没返回正文（推理占满额度？）")
        return None
    text = text[:MAX_SUMMARY_CHARS]
    write(settings, day, "这一天", text)
    log.info("日摘要已生成：%s（%d 字）", day.strftime(DAY_FORMAT), len(text))
    return text


def ensure_recent(store, llm, settings, days: int | None = None, clock=time.time) -> int:
    """补最近几天缺的摘要。返回生成了几篇。"""
    count = days if days is not None else int(getattr(settings, "digest_catchup_days", 3))
    today = day_of(clock())
    made = 0
    for offset in range(count):
        day = today - timedelta(days=offset)
        if read(settings, day) is not None:
            continue
        try:
            if generate(store, llm, settings, day) is not None:
                made += 1
        except Exception:  # noqa: BLE001 - 补摘要失败不影响主流程
            log.exception("补日摘要失败：%s", day)
    return made


def collect(settings, days: int = 7, today: date | None = None) -> list[tuple[date, str]]:
    """最近 N 天已有的摘要，新的在前。"""
    end = today or date.today()
    found: list[tuple[date, str]] = []
    for offset in range(days):
        day = end - timedelta(days=offset)
        text = read(settings, day)
        if text:
            found.append((day, text))
    return found


def recent_text(settings, days: int | None = None, today: date | None = None) -> str:
    """进 system prompt 的那段：最近几天各一行，超出预算就截断。"""
    if not getattr(settings, "digest_enabled", True):
        return ""
    count = days if days is not None else int(getattr(settings, "digest_days_in_prompt", 3))
    entries = collect(settings, count, today)
    if not entries:
        return ""
    budget = int(getattr(settings, "digest_in_prompt_chars", 600))
    blocks: list[str] = []
    used = 0
    for day, text in entries:
        body = text.split("\n", 1)[-1].strip()
        chunk = f"{day.strftime('%m-%d')}：{body}"
        if used + len(chunk) > budget:
            chunk = chunk[: max(0, budget - used)]
        if not chunk:
            break
        blocks.append(chunk)
        used += len(chunk)
        if used >= budget:
            break
    return fix_address(f"{RECENT_TITLE}\n" + "\n".join(blocks))


def week_text(settings, today: date | None = None) -> str:
    entries = collect(settings, 7, today)
    if not entries:
        return "这几天还没留下记录呢。"
    return "\n\n".join(text for _, text in entries)
