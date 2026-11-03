"""表情库：按情绪分桶，发的时候按当下情绪挑。

借的是头部（Aurabot）那套库结构，不是它的图——它的表情库是运行时"偷"来的，
不随包发；所以这里把**分类和挑法**拿过来：

- 八个情绪桶（照抄 Aurabot 的 DEFAULT_TAGS）：开心、卖萌、生气、委屈、无语、嘲讽、疑惑、早晚安
- 已有素材先入库：他发来的表情（视觉模型打过标签）按标签归桶，不浪费
- 发送时：先看回复里的情绪词，再看她当前心情（困→早晚安、想他→卖萌、黏→开心），
  最后才随机；同一个桶里挑最久没发过的
- 她也可以点名：回复里单独一行写 `{{表情:委屈}}`

库的位置：data/stickers/<情绪桶>/，往里丢图就能扩充，重启后自动认。
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import shutil
import threading
import time
from pathlib import Path

log = logging.getLogger("qqbot")

BUCKETS = ("开心", "卖萌", "生气", "委屈", "无语", "嘲讽", "疑惑", "早晚安", "日常")

# 视觉模型打的是英文标签，映射到情绪桶
TAG_BUCKETS = {
    "开心": ("happy", "joy", "smile", "laugh", "excited", "开心", "高兴", "笑"),
    "卖萌": ("cute", "cute_cat", "cat", "dog", "pet", "萌", "可爱", "动物", "heart", "love", "kiss"),
    "生气": ("angry", "mad", "rage", "生气", "怒"),
    "委屈": ("sad", "cry", "tears", "委屈", "哭", "难过", "upset"),
    "无语": ("speechless", "awkward", "sigh", "无语", "尴尬", "汗"),
    "嘲讽": ("smirk", "sarcasm", "mock", "嘲讽", "偷笑", "doge", "狗头"),
    "疑惑": ("confused", "thinking", "hmm", "question", "疑惑", "问号", "what"),
    "早晚安": ("tired", "sleepy", "sleep", "night", "morning", "困", "晚安", "早安", "睡"),
    "日常": ("日常", "干饭", "摸鱼", "上班", "打工", "吃饭"),
}

# 情绪对不上时优先用这几桶：梗图里带字的很多，随手甩一张"真当我是便宜货啊"
# 会在不合时宜的时候扎人；宁可发个卖萌的
SAFE_FALLBACK = ("卖萌", "开心", "早晚安")

MOOD_BUCKETS = {
    "tired": "早晚安",
    "miss": "卖萌",
    "warmth": "开心",
}

# 回复里出现这些词，说明她这会儿是这个情绪
CUE_WORDS = {
    "委屈": ("委屈", "难受", "哭", "想哭", "难过", "抱抱"),
    "生气": ("生气", "气死", "哼", "讨厌", "不理你"),
    "无语": ("无语", "服了", "救命", "尴尬"),
    "嘲讽": ("嘲讽", "偷笑", "狗头", "就这"),
    "疑惑": ("疑惑", "问号", "看不懂", "什么鬼", "搞不懂"),
    "开心": ("开心", "嘿嘿", "哈哈", "高兴", "太好了"),
    "早晚安": ("晚安", "早安", "睡了", "困", "睡觉"),
    "卖萌": ("想你", "喜欢你", "贴贴", "蹭", "喵"),
}

TAG_RE = re.compile(r"\{\{\s*表情\s*[:：]\s*([^{}\n]{1,8}?)\s*\}\}")

INDEX_NAME = "index.json"
_LOCK = threading.RLock()


# ---------------------------------------------------------------- 结构
def root(settings) -> Path:
    return Path(getattr(settings, "sticker_dir", "data/stickers"))


def index_path(settings) -> Path:
    return root(settings) / INDEX_NAME


def load_index(settings) -> dict:
    path = index_path(settings)
    if not path.is_file():
        return {"items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"items": []}
    if not isinstance(data.get("items"), list):
        return {"items": []}
    return data


def save_index(settings, index: dict) -> None:
    path = index_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with _LOCK:
        tmp.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)


def items(settings, bucket: str | None = None) -> list[dict]:
    rows = load_index(settings)["items"]
    if bucket:
        rows = [row for row in rows if row.get("bucket") == bucket]
    # 先按桶筛再查文件：库大了以后每条都 stat 一次太亏
    return [row for row in rows if (root(settings) / row.get("file", "")).is_file()]


def bucket_from_tags(tags) -> str:
    text = " ".join(str(tag).lower() for tag in (tags or ()))
    for bucket, words in TAG_BUCKETS.items():
        if any(word in text for word in words):
            return bucket
    return "卖萌"


def bucket_from_mood(values: dict) -> str:
    for drive, bucket in MOOD_BUCKETS.items():
        if float(values.get(drive) or 0) >= 0.5:
            return bucket
    return ""


def bucket_from_text(text: str) -> str:
    for bucket, words in CUE_WORDS.items():
        if any(word in text for word in words):
            return bucket
    return ""


# ---------------------------------------------------------------- 入库
def add_file(settings, source: Path, bucket: str, tags=None, sha: str = "") -> dict | None:
    source = Path(source)
    if not source.is_file() or bucket not in BUCKETS:
        return None
    sha = sha or hashlib.sha256(source.read_bytes()).hexdigest()
    with _LOCK:
        index = load_index(settings)
        if any(row.get("sha") == sha for row in index["items"]):
            return None
        target_dir = root(settings) / bucket
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{sha[:12]}{source.suffix.lower() or '.gif'}"
        try:
            if not target.is_file():
                shutil.copyfile(source, target)
        except OSError:
            log.warning("表情复制失败：%s", source, exc_info=True)
            return None
        row = {
            "file": f"{bucket}/{target.name}",
            "bucket": bucket,
            "tags": list(tags or []),
            "sha": sha,
            "uses": 0,
            "last_used": 0,
        }
        index["items"].append(row)
        save_index(settings, index)
    return row


def seed_from_collected(settings, store, images) -> int:
    """把他发过的表情按标签归桶——库里先有货，才谈得上按情绪发。"""
    added = 0
    for row in store.list_images(limit=500):
        path = images.path_of(row) if images is not None else None
        if path is None:
            continue
        bucket = bucket_from_tags(row.get("tags") or [])
        if add_file(settings, path, bucket, row.get("tags"), sha=row.get("sha256") or ""):
            added += 1
    if added:
        log.info("表情库初始化：收了 %d 张", added)
    return added


def ensure_seeded(settings, store, images) -> int:
    if items(settings):
        return 0
    return seed_from_collected(settings, store, images)


# ---------------------------------------------------------------- 挑
def pick(settings, bucket: str = "", cues=(), rng=random) -> Path | None:
    """按桶挑，同桶里挑最久没发过的；桶空了就退到任意一张。"""
    pool = items(settings, bucket) if bucket else []
    if not pool:
        for cue in cues:
            pool = items(settings, cue)
            if pool:
                break
    if not pool:
        pool = items(settings)
    if not pool:
        return None
    pool.sort(key=lambda row: (row.get("uses") or 0, row.get("last_used") or 0))
    row = pool[0] if len(pool) == 1 else pool[int(rng.random() * min(3, len(pool)))]
    return root(settings) / row["file"]


def record_use(settings, path: Path) -> None:
    rel = str(Path(path).relative_to(root(settings))).replace("\\", "/")
    index = load_index(settings)
    for row in index["items"]:
        if row.get("file") == rel:
            row["uses"] = int(row.get("uses") or 0) + 1
            row["last_used"] = int(time.time())
            save_index(settings, index)
            return


def pick_for_reply(settings, reply_text: str, values: dict, rng=random) -> Path | None:
    bucket = bucket_from_text(str(reply_text or "")) or bucket_from_mood(values)
    if bucket:
        path = pick(settings, bucket, rng=rng)
        if path is not None:
            return path
    for safe in SAFE_FALLBACK:
        if items(settings, safe):
            return pick(settings, safe, rng=rng)
    return pick(settings, "", rng=rng)


# ---------------------------------------------------------------- 她的点名
def extract_tags(text: str) -> tuple[str, list[str]]:
    """取出 `{{表情:委屈}}`，返回 (去掉标记的文本, 桶列表)。"""
    buckets: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        bucket = name if name in BUCKETS else bucket_from_text(name)
        if bucket and bucket not in buckets:
            buckets.append(bucket)
        return ""

    cleaned = TAG_RE.sub(_replace, text or "")
    return cleaned, buckets


def prune(settings) -> int:
    """删掉索引里指向不存在文件的条目，再删掉空桶目录。"""
    with _LOCK:
        return _prune(settings)


def _prune(settings) -> int:
    index = load_index(settings)
    kept = []
    removed = 0
    for row in index["items"]:
        if (root(settings) / row.get("file", "")).is_file():
            kept.append(row)
        else:
            removed += 1
    if removed:
        index["items"] = kept
        save_index(settings, index)
    folder = root(settings)
    if folder.is_dir():
        for child in folder.iterdir():
            if child.is_dir() and not any(child.iterdir()):
                try:
                    child.rmdir()
                    removed += 1
                except OSError:
                    continue
    return removed
