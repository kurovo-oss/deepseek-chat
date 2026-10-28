"""图库：把 tag 解析成磁盘上的图片文件。

当前数据源是 `assets/seed-images/manifest.json`（内置占位图）。
M3 会接入 `images` 表（用户表情包收藏），届时只需换一个 resolver。

安全约束（与 MVP 文档 §6.4 一致）：
- tag 只允许 [a-z0-9_-]
- 解析出的路径必须位于图库根目录内（防 `../` 穿越）
- 只允许白名单图片扩展名
"""

from __future__ import annotations

import json
import random
from pathlib import Path

ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


class ImageLibrary:
    def __init__(self, root: Path, rng: random.Random | None = None) -> None:
        self.root = Path(root).resolve()
        self._rng = rng or random.Random()
        self._index: dict[str, list[Path]] = {}
        self._load()

    def __len__(self) -> int:
        """图片文件数（同一文件挂多个 tag 只算一张）。"""
        return len({path for paths in self._index.values() for path in paths})

    @property
    def tags(self) -> list[str]:
        return sorted(self._index)

    def _load(self) -> None:
        manifest = self.root / "manifest.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            for item in data.get("items", []):
                filename = str(item.get("file", ""))
                path = self._safe_path(filename)
                if path is None:
                    continue
                for tag in item.get("tags", []):
                    key = str(tag).strip().lower()
                    if key:
                        self._index.setdefault(key, []).append(path)
            return

        # 没有 manifest 时退化为「文件名即 tag」
        if self.root.is_dir():
            for path in sorted(self.root.iterdir()):
                if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES:
                    self._index.setdefault(path.stem.lower(), []).append(path)

    def _safe_path(self, filename: str) -> Path | None:
        if not filename or "/" in filename or "\\" in filename:
            return None
        candidate = (self.root / filename).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        if candidate.suffix.lower() not in ALLOWED_SUFFIXES:
            return None
        if not candidate.is_file():
            return None
        return candidate

    def resolve(self, tag: str) -> Path | None:
        """按 tag 随机取一张；没有命中返回 None（调用方静默忽略）。"""
        paths = self._index.get((tag or "").strip().lower())
        if not paths:
            return None
        return self._rng.choice(paths)
