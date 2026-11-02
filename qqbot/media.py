"""发不发图、发不发表情包：这件事也归情绪管。

累了懒得找图，心情好、想他的时候更想甩一张过来。
单独成文件是因为 engine.py 已经是编排层，再往里塞选图逻辑就成杂物间了。
"""

from __future__ import annotations

import random
import re
from pathlib import Path

from . import mood as mood_mod

# 他明确要图的时候，别拿"冷却"和"今天累了"当借口——他要就得给
REQUEST_RE = re.compile(
    r"(表情包?|图片|照片|那张|这个图|你存的|你收藏的|刚才那)"
    r"|(发|给|来|再)(我)?(张|个|一张|一个)"
)

# "刚才那个"：按最新收藏的一张解析，不用她记住 tag
LAST_TAGS = ("last", "new")
LAST_HINTS = ("刚才", "刚那个", "刚那张", "上次那", "存的那", "存的", "收藏的那", "收藏的", "最新", "这个", "那张")


def _is_last_tag(tag: str) -> bool:
    key = (tag or "").strip().lower()
    if not key:
        return False
    return key in LAST_TAGS or any(hint in key for hint in LAST_HINTS)


def wants_image(text: str) -> bool:
    """他这句话是不是在要图/表情包。"""
    return bool(REQUEST_RE.search(text or ""))


class MediaMixin:
    def _mood_media(self) -> dict[str, float | bool]:
        """情绪怎么影响"发不发图/表情"：累了懒得找，心情好、想他会更想发。"""
        mood = self.mood
        if mood is None:
            return {"sticker": 1.0, "image": 1.0, "drop_image": False}
        values = mood.snapshot()
        if values["tired"] >= mood_mod.TIRED_LIMIT:
            return {"sticker": 0.4, "image": 0.5, "drop_image": True}
        if values["miss"] >= mood_mod.MISS_LIMIT:
            return {"sticker": 1.3, "image": 1.3, "drop_image": False}
        if values["warmth"] >= 0.6:
            return {"sticker": 1.2, "image": 1.2, "drop_image": False}
        return {"sticker": 1.0, "image": 1.0, "drop_image": False}

    def _latest_collected_path(self) -> Path | None:
        row = self.store.latest_image(source="user") or self.store.latest_image()
        if row is None:
            return None
        path_of = getattr(self.images, "path_of", None)
        if path_of is None:
            return None
        return path_of(row)

    def _pick_image(self, session: str, tags: list[str], force: bool = False) -> Path | None:
        """配图策略：冷却窗口内发过图就直接放弃，不找替代，也不提示。

        `force=True` 是"他开口要的"：这时候冷却和情绪都得让路，
        否则她说"发啦"而他什么都没收到——比不发更糟。
        """
        if not tags or self.images is None or not self.settings.image_enabled:
            return None

        for tag in tags:
            if _is_last_tag(tag):
                return self._latest_collected_path()

        if not force:
            if self._mood_media()["drop_image"] and self._rng.random() < 0.5:
                return None  # 累了：一半的图就懒得翻了
            if self.store.has_recent_image(session, self.settings.image_cooldown_messages):
                return None

        for tag in tags:
            path = self.images.resolve(tag, max_bytes=self.settings.image_max_bytes)
            if path is not None:
                return path
        if force:
            # 他开口要了，tag 却没命中：宁可给最新收藏的那张，也别让他空等
            return self._latest_collected_path()
        return None

    def _maybe_sticker(self, session: str) -> Path | None:
        """随手甩个表情包。概率很低，且刚发过图就不发。"""
        chance = float(getattr(self.settings, "sticker_chance", 0.0) or 0.0)
        if chance <= 0 or self.images is None or not self.settings.image_enabled:
            return None
        chance = min(0.95, chance * float(self._mood_media()["sticker"]))
        if self._rng.random() >= chance:
            return None
        if self.store.has_recent_image(session, self.settings.image_cooldown_messages):
            return None
        tags = [
            item.strip()
            for item in str(getattr(self.settings, "sticker_tags", "")).split(",")
            if item.strip()
        ]
        random.shuffle(tags)
        for tag in tags:
            path = self.images.resolve(tag, max_bytes=self.settings.image_max_bytes)
            if path is not None:
                return path
        return None
