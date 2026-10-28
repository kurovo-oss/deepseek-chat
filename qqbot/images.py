"""图库：把 tag 解析成磁盘上的图片文件。

数据源统一为 SQLite 的 `images` 表：
- `source='builtin'`：启动时从 `assets/seed-images/` 导入一次
- `source='user'`：你自己发的表情包，自动收藏

安全约束（与 MVP §6.4 一致）：
- tag 只允许出现在图库标签集合里
- `rel_path` 拼成绝对路径后必须落在图库根目录内（防 `../` 穿越）
- 只允许白名单图片扩展名
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# 打标词汇表：必须与 prompts/system.md 里「可用 tag」保持一致，
# 否则收藏来的表情包永远不会被模型点名（tests/test_images.py 会校验）。
TAG_VOCABULARY = (
    "happy",
    "smile",
    "ok",
    "cat",
    "animal",
    "cute",
    "sleepy",
    "tired",
    "night",
    "sad",
    "down",
    "thanks",
    "thinking",
    "hmm",
)

_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
)


def sniff_image(data: bytes) -> tuple[str, str] | None:
    """按魔数判断真实图片类型；认不出就不收（避免把任意文件存进图库）。"""
    if not data:
        return None
    for magic, mime, ext in _MAGIC:
        if data.startswith(magic):
            return mime, ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_image_file(root: Path, data: bytes) -> tuple[str, str, str] | None:
    """把字节落盘，返回 (sha256, mime, rel_path)；类型不认识返回 None。"""
    sniffed = sniff_image(data)
    if sniffed is None:
        return None
    mime, ext = sniffed
    digest = sha256_of(data)
    rel_path = f"{digest[:2]}/{digest}{ext}"
    target = Path(root) / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        target.write_bytes(data)
    return digest, mime, rel_path.replace("\\", "/")


def import_seed_library(store, source_dir: Path, root: Path) -> int:
    """把内置占位图导进 images 表。可重复调用，按 sha256 去重。"""
    manifest = Path(source_dir) / "manifest.json"
    if not manifest.is_file():
        return 0
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0

    imported = 0
    for item in data.get("items", []):
        filename = str(item.get("file", ""))
        if not filename or "/" in filename or "\\" in filename:
            continue
        source = Path(source_dir) / filename
        if not source.is_file():
            continue
        payload = source.read_bytes()
        saved = save_image_file(root, payload)
        if saved is None:
            continue
        digest, mime, rel_path = saved
        if store.find_image_by_sha(digest) is not None:
            continue
        tags = [str(tag).strip().lower() for tag in item.get("tags", []) if str(tag).strip()]
        store.add_image(
            digest, rel_path, mime, len(payload), tags=tags, source="builtin"
        )
        imported += 1
    return imported


class ImageLibrary:
    """tag → 文件。优先用你自己发的表情包，其次才是内置占位图。"""

    def __init__(self, store, root: Path, rng: random.Random | None = None) -> None:
        self.store = store
        self.root = Path(root).resolve()
        self._rng = rng or random.Random()

    def __len__(self) -> int:
        return self.store.count_images()

    @property
    def tags(self) -> list[str]:
        collected: set[str] = set()
        for image in self.store.list_images(limit=1000):
            collected.update(str(tag).lower() for tag in image["tags"])
        return sorted(collected)

    def path_of(self, image: dict) -> Path | None:
        rel_path = str(image.get("rel_path") or "")
        if not rel_path:
            return None
        candidate = (self.root / rel_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        if candidate.suffix.lower() not in ALLOWED_SUFFIXES:
            return None
        return candidate if candidate.is_file() else None

    def resolve(self, tag: str, max_bytes: int | None = None) -> Path | None:
        """按 tag 取一张；没有命中返回 None（调用方静默忽略）。"""
        key = (tag or "").strip().lower()
        if not key:
            return None

        rows = self.store.list_images(tag=key, limit=1000)
        if max_bytes:
            rows = [row for row in rows if int(row.get("byte_size") or 0) <= max_bytes]
        rows = [row for row in rows if self.path_of(row) is not None]
        if not rows:
            return None

        collected = [row for row in rows if row.get("source") != "builtin"]
        pool = collected or rows
        lowest = min(int(row.get("use_count") or 0) for row in pool)
        candidates = [row for row in pool if int(row.get("use_count") or 0) <= lowest + 1]
        chosen = self._rng.choice(candidates)
        self.store.record_image_use(int(chosen["id"]))
        return self.path_of(chosen)
