"""从公开 CDN 抓一批「蓝色大肥鱼」表情，按台词归到情绪桶。

图源：梗鲸 GengJing 的大肥鱼档案馆（https://aigengtu.com/archive），
CDN 公开可热链；这里只下预览图、转成 PNG 存进本地表情库。

- 台词里出现「哭/泪/委屈」→ 委屈，「气/骂/踹」→ 生气，以此类推
- 认不出情绪的跳过，不硬塞
- 同名（sha 相同）自动跳过，重复跑是安全的

用法（在项目根目录）：
    tools\\python\\python.exe scripts\\fetch-stickers.py --pages 4 --per-bucket 6
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import time
import urllib.request
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
ARCHIVE = "https://aigengtu.com/archive?page={page}"

# 顺序就是优先级：先判委屈，再判生气……最后才落到卖萌
BUCKET_WORDS = (
    ("委屈", ("哭", "泪", "委屈", "崩", "挽留", "可怜", "惨", "呜呜")),
    ("生气", ("愤怒", "声讨", "生气", "怒", "骂", "踹", "围攻", "气鼓鼓", "不爽")),
    ("无语", ("无语", "嫌弃", "沉默", "尴尬", "服了", "汗")),
    ("嘲讽", ("嘲讽", "哼", "傲娇", "幸灾乐祸", "找借口", "偷笑")),
    ("疑惑", ("疑惑", "问号", "不懂", "不耐烦", "为什么", "上错")),
    ("开心", ("开心", "快乐", "大笑", "高兴", "元气", "得意", "上手")),
    ("早晚安", ("晚安", "早安", "睡", "困", "加班", "双休", "摸鱼")),
    ("卖萌", ("害羞", "可爱", "吃饱", "摸摸", "蹭", "贴贴", "女仆", "撒娇")),
)

def classify(caption: str) -> str | None:
    """先看台词的"表情类型"后缀（如"崩溃大哭表情包"），再看整句。

    只看整句会被台词里的字带偏：'元气满满' 会被判成生气，'吃瓜围观' 也是。
    """
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
    """返回 [(preview_url, caption), ...]。"""
    rows: list[tuple[str, str]] = []
    for block in html.split('class="ar-card"')[1:]:
        preview = re.search(r'data-preview="([^"]+)"', block)
        caption = re.search(r'data-alt="([^"]+)"', block)
        if preview and caption:
            rows.append((preview.group(1), caption.group(1)))
    return rows


def fetch(url: str, timeout: float = 20.0) -> bytes:
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def to_png(data: bytes) -> bytes | None:
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:  # noqa: BLE001 - 坏图直接跳过
        return None
    image.thumbnail((512, 512))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=4, help="抓多少页档案，每页约 60 张")
    parser.add_argument("--per-bucket", type=int, default=6, help="每个情绪桶最多收几张")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root)

    counts = {bucket: 0 for bucket in stickers.BUCKETS}
    added = 0
    seen: set[str] = set()

    for page in range(1, args.pages + 1):
        try:
            html = fetch(ARCHIVE.format(page=page)).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - 某一页挂了就跳过
            print(f"第 {page} 页抓取失败，跳过")
            continue
        for preview, caption in parse_page(html):
            bucket = classify(caption)
            if bucket is None or counts[bucket] >= args.per_bucket:
                continue
            if preview in seen:
                continue
            seen.add(preview)
            if args.dry_run:
                print(f"[{bucket}] {caption}")
                counts[bucket] += 1
                continue
            try:
                png = to_png(fetch(preview))
            except Exception:  # noqa: BLE001
                continue
            if png is None:
                continue
            tmp = root / "data" / "stickers_incoming" / f"{abs(hash(preview))}.png"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(png)
            if stickers.add_file(settings, tmp, bucket, tags=[caption]):
                counts[bucket] += 1
                added += 1
            time.sleep(0.15)

    print("入库：", {bucket: count for bucket, count in counts.items() if count}, f"共 +{added}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
