"""她真的能查一下：Bing 的 RSS 结果，只读标题和摘要。

主路是 360 搜索（中文长句的结果页明显更对题），备用 Bing 的 RSS（干净、稳）。
百度基本走不通：第一次给 1.2MB，第二次只给 1.4KB 的验证页。

规矩：只拿标题和摘要，**没点进去看全文**——所以她只能说"我查到的是"，
不能装成读完整篇。查不到就让她承认不知道，这比编一个强。
"""

from __future__ import annotations

import html
import logging
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

log = logging.getLogger("qqbot")

TAG_RE = re.compile(r"\{\{\s*查\s*[:：]\s*([^{}\n]{1,40}?)\s*\}\}")
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}
BING = "https://www.bing.com/search?q={query}&format=rss&setlang=zh-Hans&mkt=zh-CN"
SO = "https://www.so.com/s?q={query}"
CACHE_TTL = 1800.0
MAX_SNIPPET = 120

_cache: dict[str, tuple[float, list[dict]]] = {}


def strip_tag(text: str) -> tuple[str, str]:
    """把 `{{查:关键词}}` 摘出来；回复里不能留这个标记。"""
    body = text or ""
    found = TAG_RE.search(body)
    if not found:
        return body, ""
    query = found.group(1).strip()
    cleaned = TAG_RE.sub("", body).strip()
    return cleaned, query


def _clean(raw: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", raw or "")).strip()


def _fetch(url: str, timeout: float) -> str:
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def _parse_360(page: str) -> list[dict]:
    """360 的结果块；标题和摘要分开抓，摘要没有就算了。"""
    rows: list[dict] = []
    for chunk in re.findall(r'<li class="res-list".*?</li>', page, re.S):
        title_match = re.search(r"<h3[^>]*>(.*?)</h3>", chunk, re.S)
        if not title_match:
            continue
        title = _clean(title_match.group(1))
        if not title:
            continue
        link_match = re.search(r'href="([^"]+)"', chunk)
        snippet = ""
        for pattern in (r'class="res-desc"[^>]*>(.*?)</p>', r'class="res-comm-con"[^>]*>(.*?)</div>'):
            found = re.search(pattern, chunk, re.S)
            if found:
                snippet = _clean(found.group(1))[:MAX_SNIPPET]
                break
        rows.append(
            {
                "title": title,
                "url": (link_match.group(1) if link_match else ""),
                "snippet": snippet,
            }
        )
    return rows


def _parse(raw: bytes) -> list[dict]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    rows: list[dict] = []
    for item in root.iter("item"):
        title = _clean(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        snippet = _clean(item.findtext("description") or "")
        if not title:
            continue
        rows.append({"title": title, "url": link, "snippet": snippet[:MAX_SNIPPET]})
    return rows


def search(query: str, limit: int = 4, timeout: float = 12.0) -> list[dict]:
    """查一次，返回 [{title, url, snippet}]；失败返回空。"""
    query = (query or "").strip()[:60]
    if not query:
        return []
    cached = _cache.get(query)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1][:limit]
    quoted = urllib.parse.quote(query)
    rows: list[dict] = []
    try:
        rows = _parse_360(_fetch(SO.format(query=quoted), timeout))
    except Exception:  # noqa: BLE001 - 主路挂了走备用
        log.warning("360 查询失败：%s", query)
    if not rows:
        try:
            rows = _parse(_fetch(BING.format(query=quoted), timeout).encode("utf-8"))
        except Exception:  # noqa: BLE001 - 查不到就当没查
            log.warning("查询失败：%s", query)
            return []
    rows = rows[:limit]
    _cache[query] = (time.time(), rows)
    return rows


def block(rows: list[dict], limit: int = 3) -> str:
    """给模型的第二遍提示：她刚查到的东西。"""
    if not rows:
        return ""
    lines = ["【你刚查到的（只有标题和摘要，没点开看全文）】"]
    for row in rows[:limit]:
        host = ""
        try:
            host = urllib.parse.urlparse(row.get("url") or "").netloc.replace("www.", "")
        except ValueError:
            host = ""
        tail = f"（{host}）" if host else ""
        lines.append(f"- {row['title']}{tail}：{row['snippet']}")
    lines.append("用这些把话说完：说人话，别念标题，也别编摘要里没有的细节。")
    return "\n".join(lines)[:900]


def command(rest: str) -> str:
    """/查 关键词 —— 把结果摊给他看，不经过模型。"""
    query = (rest or "").strip()
    if not query:
        return "查什么呀？/查 地球到月球多远"
    rows = search(query, limit=3)
    if not rows:
        return "我查了，没查到什么像样的结果。"
    lines = [f"我查到的（{query}）："]
    for index, row in enumerate(rows, start=1):
        lines.append(f"{index}. {row['title']}")
        if row["snippet"]:
            lines.append(f"   {row['snippet']}")
    return "\n".join(lines)


def follow_up(settings, complete, messages: list[dict], text: str, log_usage=None) -> str:
    """她说"我查一下"：真查一遍，再让她用自己的话说。

    返回最终该发出去的那段；没查（或查不到）就把标记摘掉、用第一遍的话。
    """
    cleaned, query = strip_tag(text)
    if not query or not getattr(settings, "search_enabled", False):
        return cleaned
    rows = search(query)
    if not rows:
        return cleaned
    try:
        second = complete(
            [
                *messages,
                {"role": "assistant", "content": cleaned},
                {"role": "system", "content": block(rows)},
            ]
        )
    except Exception:  # noqa: BLE001 - 查资料失败不该让这条回复消失
        log.exception("二次查资料失败")
        return cleaned
    if second is None or not (second.text or "").strip():
        return cleaned
    if log_usage is not None:
        log_usage(second)
    return re.sub(r"\n{2,}", "\n", second.text)
