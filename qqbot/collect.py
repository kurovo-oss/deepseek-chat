"""表情包收藏：下载 → 去重 → 落盘 → 打标。

只收表情包（`sub_type=1` 或 summary 带表情标记），普通照片不动——
避免把真人照片囤进图库（MVP §6.5）。
"""

from __future__ import annotations

import logging
import time
import urllib.request
from pathlib import Path

from . import vision
from .images import TAG_VOCABULARY, save_image_file
from .memory import load_json_object

log = logging.getLogger("qqbot")

STICKER_HINTS = ("表情", "sticker", "mface", "闪照")
MAX_TAGS = 3


def is_sticker(image) -> bool:
    try:
        sub_type = int(image.sub_type)
    except (TypeError, ValueError):
        sub_type = 0
    if sub_type == 1:
        return True
    summary = str(getattr(image, "summary", "") or "").lower()
    return any(hint in summary for hint in STICKER_HINTS)


class StickerCollector:
    def __init__(
        self,
        settings,
        store,
        root: Path,
        llm=None,
        tag_prompt: str = "",
        fetch=None,
        clock=time.time,
    ) -> None:
        self.settings = settings
        self.store = store
        self.root = Path(root)
        self.llm = llm
        self.tag_prompt = tag_prompt
        self._fetch = fetch or self._http_fetch
        self._clock = clock

    # ---------------- 下载 ----------------
    def _http_fetch(self, url: str) -> bytes | None:
        limit = self.settings.image_max_bytes
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "qqbot/0.1"})
            with urllib.request.urlopen(request, timeout=self.settings.request_timeout) as response:
                data = response.read(limit + 1)
        except Exception as exc:  # noqa: BLE001 - 网络问题不该影响对话
            log.warning("下载表情包失败：%s", exc)
            return None
        if len(data) > limit:
            log.warning("表情包超过 %d 字节，跳过", limit)
            return None
        return data

    # ---------------- 收藏 ----------------
    def collect(self, message, recent: list[dict] | None = None) -> list[dict]:
        """收藏消息里的表情包，返回本次涉及到的图库记录。"""
        if not self.settings.image_collect_enabled:
            return []

        results: list[dict] = []
        for image in getattr(message, "images", ()):  # noqa: B007
            if not is_sticker(image):
                continue
            image_id = self.collect_one(image, message.message_id, recent)
            if image_id is not None:
                record = self.store.get_image(image_id)
                if record is not None:
                    results.append(record)
        return results

    def collect_one(self, image, message_id: str, recent: list[dict] | None) -> int | None:
        url = str(getattr(image, "url", "") or "").strip()
        if not url:
            return None

        data = self._fetch(url)
        if not data:
            return None

        saved = save_image_file(self.root, data)
        if saved is None:
            log.info("认不出图片类型，跳过收藏")
            return None
        digest, mime, rel_path = saved

        existing = self.store.find_image_by_sha(digest)
        if existing is not None:
            self.store.record_image_use(int(existing["id"]))
            if not existing["tags"]:
                # 老收藏是"看不见图"时打的标，能补就补一次
                self.retag(existing, data)
            return int(existing["id"])

        tags = self._infer_tags(recent, self._describe(data))
        image_id = self.store.add_image(
            digest,
            rel_path,
            mime,
            len(data),
            tags=tags,
            source="user",
            source_msg_id=message_id,
        )
        if image_id is None:
            return None

        self.store.evict_images(self.settings.image_max_count)
        log.info("收藏表情包 #%s：%s（%s）", image_id, rel_path, "、".join(tags) or "无标签")
        return image_id

    # ---------------- 打标 ----------------
    def _describe(self, data: bytes) -> str:
        """让多模态模型看一眼这张表情包。没开看图就返回空串。"""
        if not self.settings.vision_enabled or self.llm is None:
            return ""
        try:
            return vision.note_for(self.store, self.llm, data, sticker=True)
        except Exception:  # noqa: BLE001 - 看图失败不该影响收藏
            log.warning("表情包看图失败", exc_info=True)
            return ""

    def retag(self, record: dict, data: bytes, recent: list[dict] | None = None) -> list[str]:
        """给一张已有的图重新打标（只补没标签的），返回新标签。"""
        note = self._describe(data)
        if not note:
            return []
        tags = self._infer_tags(recent, note)
        if tags:
            self.store.update_image_tags(int(record["id"]), tags)
            log.info("补标表情包 #%s：%s", record["id"], "、".join(tags))
        return tags

    def retag_all(self, root: Path, limit: int = 20) -> tuple[int, int, int]:
        """批量补标：返回 (检查张数, 补上张数, 没补上张数)。"""
        checked = tagged = failed = 0
        for record in self.store.list_untagged_images(limit):
            checked += 1
            try:
                data = (Path(root) / record["rel_path"]).read_bytes()
            except OSError:
                failed += 1
                continue
            if self.retag(record, data):
                tagged += 1
            else:
                failed += 1
        return checked, tagged, failed

    def _infer_tags(self, recent: list[dict] | None, note: str = "") -> list[str]:
        if not self.settings.image_auto_tag or self.llm is None or not self.tag_prompt:
            return []

        context = "\n".join(
            f"{'用户' if item.get('role') == 'user' else '大肥鱼'}："
            f"{str(item.get('content'))[:80]}"
            for item in (recent or [])
        )
        lines = [f"画面：{note}" if note else "画面：（没看清）", f"上下文：\n{context or '（没有上下文）'}"]
        try:
            result = self.llm.chat(
                [
                    {"role": "system", "content": self.tag_prompt},
                    {"role": "user", "content": "\n\n".join(lines)},
                ],
                temperature=0.0,
                # 现在模型是推理模型，reasoning_tokens 也吃 max_tokens：
                # 给 80 会被思考耗光、正文为空，打出来就是"无标签"
                max_tokens=600,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("表情包打标失败：%s", exc)
            self.store.log_usage(self.settings.model_id, 0, 0, "tag", ok=False)
            return []

        self.store.log_usage(
            result.model, result.tokens_in, result.tokens_out, "tag", True
        )
        data = load_json_object(result.text) or {}
        raw_tags = data.get("tags")
        if not isinstance(raw_tags, list):
            return []

        allowed = set(TAG_VOCABULARY)
        tags: list[str] = []
        for item in raw_tags:
            tag = str(item).strip().lower()
            if tag in allowed and tag not in tags:
                tags.append(tag)
        return tags[:MAX_TAGS]
