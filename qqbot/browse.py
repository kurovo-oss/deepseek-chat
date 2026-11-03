"""刷点东西：她真的看见了什么，才说得出什么。

原则和"生活线"一样——不是给她一串话题让她念，是让她自己挑一条：
- 只读标题（点进去抓正文容易坏、也容易踩反爬），真看见的就是标题本身
- 她按自己的口味挑：吃、猫、天气、好玩的事优先；太沉重的（事故、案件、伤亡）跳过
- 挑中的那条当天落盘，之后聊天才可能提到；没落盘的不许编
- 网络不通就当今天没刷到

数据源（2026-11-03 实测可达）：
- 百度热榜 top.baidu.com
- B 站热门 api.bilibili.com
微博热搜 403、知乎 401，暂时不接。
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger("qqbot")

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}
SOURCE_LABEL = {"baidu": "热搜", "bilibili": "B站热门"}

# 她爱看什么：分数越高越可能被挑中
INTERESTS = {
    "猫": 6, "狗": 6, "宠物": 6, "动物": 4, "熊猫": 5,
    "吃": 4, "美食": 4, "火锅": 4, "奶茶": 4, "烧烤": 4,
    "天气": 3, "降温": 3, "下雨": 3, "雪": 3,
    "搞笑": 4, "离谱": 3, "好玩": 3, "可爱": 4, "萌": 3,
    "游戏": 3, "电影": 2, "演唱会": 2, "旅游": 2, "大学生": 3,
    "恋爱": 3, "情侣": 3, "结婚": 2, "分手": 2,
    "AI": 2, "机器人": 2, "手机": 1, "科技": 2,
}

# 太沉的她不会挑：不是不能看，是她不想拿这些找他说话
HEAVY = re.compile(
    r"伤亡|遇难|身亡|去世|逝世|讣告|判决|死刑|枪击|坠亡|自杀|车祸|事故|火灾|地震|洪水|"
    r"塌方|爆炸|拐卖|被拐|强奸|性侵|虐待|贪腐|落马|战争|导弹|疫情|病毒|隔离"
)


@dataclass
class Item:
    source: str
    title: str
    extra: str = ""
    seen_at: float = field(default_factory=time.time)

    def as_line(self) -> str:
        label = SOURCE_LABEL.get(self.source, self.source)
        return f"{self.title}（{label}）"


# ---------------------------------------------------------------- 抓取
def _get_json(url: str, timeout: float = 8.0):
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def parse_baidu(payload: dict) -> list[Item]:
    items: list[Item] = []
    try:
        cards = payload["data"]["cards"]
        for card in cards:
            for block in card.get("content") or []:
                for row in block.get("content") or []:
                    word = str(row.get("word") or "").strip()
                    if word:
                        items.append(Item("baidu", word))
    except (KeyError, TypeError):
        return []
    return items


def parse_bilibili(payload: dict) -> list[Item]:
    items: list[Item] = []
    for video in (payload.get("data") or {}).get("list") or []:
        title = str(video.get("title") or "").strip()
        if not title:
            continue
        owner = str((video.get("owner") or {}).get("name") or "").strip()
        items.append(Item("bilibili", title, owner))
    return items


def fetch(settings, timeout: float = 8.0) -> list[Item]:
    """两个源都试；一个挂了不影响另一个。"""
    items: list[Item] = []
    urls = (
        ("baidu", "https://top.baidu.com/api/board?platform=wise&tab=realtime", parse_baidu),
        ("bilibili", "https://api.bilibili.com/x/web-interface/popular?ps=30&pn=1", parse_bilibili),
    )
    for name, url, parser in urls:
        try:
            items.extend(parser(_get_json(url, timeout)))
        except Exception:  # noqa: BLE001 - 刷不到就当没网
            log.info("刷%s失败", SOURCE_LABEL.get(name, name), exc_info=True)
    return items


# ---------------------------------------------------------------- 挑选
def score(item: Item) -> int:
    return sum(weight for word, weight in INTERESTS.items() if word in item.title)


def pick(items: list[Item], rng=random) -> Item | None:
    """按她的口味挑一个：太沉的不看，标题太长的不要，同分随机。"""
    candidates = [
        item
        for item in items
        if item.title and 4 <= len(item.title) <= 40 and not HEAVY.search(item.title)
    ]
    if not candidates:
        return None
    scored = sorted(candidates, key=score, reverse=True)
    top = scored[:3] if len(scored) >= 3 else scored
    best = max(score(item) for item in top)
    return rng.choice([item for item in top if score(item) == best])


# ---------------------------------------------------------------- 落盘
def _path(settings, day: str | None = None) -> Path:
    folder = Path(getattr(settings, "browse_dir", "data/browse"))
    return folder / f"{day or date.today().isoformat()}.json"


def load(settings, day: str | None = None) -> list[Item]:
    path = _path(settings, day)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = []
    for row in raw.get("items") or []:
        if isinstance(row, dict) and row.get("title"):
            items.append(Item(**{k: row[k] for k in ("source", "title", "extra", "seen_at") if k in row}))
    return items


def save(settings, items: list[Item], day: str | None = None) -> None:
    path = _path(settings, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": day or date.today().isoformat(),
        "items": [asdict(item) for item in items],
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def collect(settings, rng=random, timeout: float = 8.0) -> Item | None:
    """刷一次、挑一条、记下来。刷不到返回 None。"""
    if not bool(getattr(settings, "web_enabled", False)):
        return None
    item = pick(fetch(settings, timeout=timeout), rng)
    if item is None:
        return None
    items = load(settings)
    titles = {existing.title for existing in items}
    if item.title not in titles:
        items.append(item)
        save(settings, items)
        log.info("今天刷到：%s", item.as_line())
    return item


def today_item(settings) -> Item | None:
    items = load(settings)
    return items[-1] if items else None


def block(settings) -> str:
    """给提示词用的一段：只说真看见的。"""
    if not bool(getattr(settings, "web_enabled", False)):
        return ""
    item = today_item(settings)
    if item is None:
        return ""
    return (
        "【你今天刷到的】\n"
        f"{item.as_line()}\n"
        "（你只是在列表里刷到这个标题，**没点进去看**。他问你在干嘛、或者"
        "说无聊的时候，顺口提一句「我刷到一个讲……的」最自然；标题以外的东西"
        "你不知道，别编内容。一天说一次就够了。）"
    )


def prune(settings, keep_days: int = 14) -> int:
    folder = Path(getattr(settings, "browse_dir", "data/browse"))
    if not folder.is_dir():
        return 0
    cutoff = datetime.now().timestamp() - keep_days * 86400
    removed = 0
    for path in folder.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
