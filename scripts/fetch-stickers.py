"""从公开 CDN 抓「蓝色大肥鱼」表情，按台词归到情绪桶。

图源：梗鲸 GengJing 的大肥鱼档案馆（https://aigengtu.com/archive），
索引和图片都在公开 R2（img.aigengtu.com），不需要登录。

- 索引：index/current.json 指向最新的 index/<v>.json，里面的 gallery 带台词
- 先看台词的"表情类型"后缀（"崩溃大哭表情包" → 委屈），再看整句；
  只看整句会被台词带偏："元气满满" 会被判成生气
- 认不出情绪的归「日常」，不丢——他要的是"全给我安上"
- sha 去重，重复跑是安全的

用法（在项目根目录）：
    tools\\python\\python.exe scripts\\fetch-stickers.py --all
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from qqbot import stickers  # noqa: E402
from qqbot.config import load_settings  # noqa: E402

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}
INDEX_BASE = "https://img.aigengtu.com/index/"
ARCHIVE = "https://aigengtu.com/archive?page={page}"
PAGES_RE = re.compile(r"第\s*1\s*/\s*(\d+)\s*页")

# 顺序就是优先级：先判委屈，再判生气……最后才落到卖萌/日常
BUCKET_WORDS = (
    ("委屈", ("哭", "泪", "委屈", "崩", "挽留", "可怜", "惨", "呜呜")),
    ("生气", ("愤怒", "声讨", "生气", "怒", "骂", "踹", "围攻", "气鼓鼓", "不爽")),
    ("无语", ("无语", "嫌弃", "沉默", "尴尬", "服了", "汗")),
    ("嘲讽", ("嘲讽", "哼", "傲娇", "幸灾乐祸", "找借口", "偷笑")),
    ("疑惑", ("疑惑", "问号", "不懂", "不耐烦", "为什么", "上错")),
    ("开心", ("开心", "快乐", "大笑", "高兴", "元气", "得意", "上手")),
    ("早晚安", ("晚安", "早安", "睡", "困", "加班", "双休", "摸鱼")),
    ("卖萌", ("害羞", "可爱", "吃饱", "摸摸", "蹭", "贴贴", "女仆", "撒娇")),
    ("日常", ("干饭", "上班", "打工", "日常", "吃白饭", "白饭")),
)


def classify(caption: str) -> str | None:
    """先看台词的表情类型后缀，再看整句；都不中返回 None（由调用方归「日常」）。"""
    text = caption or ""
    tail = text
    for mark in ("』", "》", "」"):
        if mark in text:
            tail = text.rsplit(mark, 1)[1]
            break
    for source in (tail, text):
        for bucket, words in BUCKET_WORDS:
            if any(word in source for word in words):
                return bucket
    return None


def parse_page(html: str) -> list[tuple[str, str]]:
    """从档案页 HTML 里读卡片（备用路径，页数是服务端渲染的头 60 张）。"""
    rows: list[tuple[str, str]] = []
    for block in html.split('class="ar-card"')[1:]:
        preview = re.search(r'data-preview="([^"]+)"', block)
        caption = re.search(r'data-alt="([^"]+)"', block)
        if preview and caption:
            rows.append((preview.group(1), caption.group(1)))
    return rows


def parse_index(data: dict) -> list[tuple[str, str]]:
    """从 R2 索引里读全部条目：[(preview_url, caption), ...]。"""
    rows: list[tuple[str, str]] = []
    for section in (data.get("gallery") or {}).values():
        for image in section.get("images") or []:
            preview = image.get("preview") or image.get("original")
            caption = image.get("alt") or image.get("name") or ""
            if preview:
                rows.append((preview, caption))
    return rows


def total_pages(html: str) -> int:
    found = PAGES_RE.search(html)
    return int(found.group(1)) if found else 1


def fetch(url: str, timeout: float = 25.0) -> bytes:
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def load_all(timeout: float = 60.0) -> list[tuple[str, str]]:
    meta = json.loads(fetch(INDEX_BASE + "current.json", timeout=timeout).decode("utf-8"))
    version = str(meta.get("v") or "")
    if not version:
        raise RuntimeError("索引指针里没有 v")
    data = json.loads(fetch(f"{INDEX_BASE}{version}.json", timeout=timeout).decode("utf-8"))
    rows = parse_index(data)
    print(f"索引 {version}：{len(rows)} 条")
    return rows


def to_png(data: bytes) -> bytes | None:
    """压成 512 以内的调色板 PNG：表情包是插画，256 色看不出差别，体积小一半。"""
    try:
        image = Image.open(io.BytesIO(data))
        image.thumbnail((512, 512))
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            image = image.quantize(colors=256, method=Image.FASTOCTREE)
        else:
            image = image.convert("RGB").quantize(colors=256)
    except Exception:  # noqa: BLE001 - 坏图直接跳过
        return None
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="抓索引里的全部条目（默认就是全部）")
    parser.add_argument("--limit", type=int, default=0, help="只抓前 N 条（调试用）")
    parser.add_argument("--per-bucket", type=int, default=0, help="0 = 不限额")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root)
    incoming = root / "data" / "stickers_incoming"
    incoming.mkdir(parents=True, exist_ok=True)

    candidates = [(preview, classify(caption) or "日常") for preview, caption in load_all()]
    if args.limit:
        candidates = candidates[: args.limit]

    if args.per_bucket:
        kept: list[tuple[str, str]] = []
        counts: dict[str, int] = {}
        for row in candidates:
            bucket = row[1]
            if counts.get(bucket, 0) >= args.per_bucket:
                continue
            counts[bucket] = counts.get(bucket, 0) + 1
            kept.append(row)
        candidates = kept

    print(f"待入库 {len(candidates)} 张")
    if args.dry_run:
        for _preview, bucket in candidates[:20]:
            print(f"[{bucket}]")
        return 0

    added = 0
    skipped = 0

    def work(row: tuple[str, str]) -> bool:
        preview, bucket = row
        try:
            png = to_png(fetch(preview))
        except Exception:  # noqa: BLE001
            return False
        if png is None:
            return False
        tmp = incoming / f"{abs(hash(preview))}.png"
        tmp.write_bytes(png)
        return bool(stickers.add_file(settings, tmp, bucket, tags=[]))

    start = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for index, ok in enumerate(pool.map(work, candidates), start=1):
            if ok:
                added += 1
            else:
                skipped += 1
            if index % 200 == 0:
                print(f"  进度 {index}/{len(candidates)}，入库 {added}，{time.time() - start:.0f}s")

    print(f"完成：入库 {added}，跳过 {skipped}（重复/坏图），用时 {time.time() - start:.0f}s")
    for bucket in stickers.BUCKETS:
        print(f"  {bucket}: {len(stickers.items(settings, bucket))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
