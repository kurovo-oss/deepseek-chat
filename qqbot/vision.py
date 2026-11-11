"""看图：把图片交给多模态模型，换回一句人能看懂的话。

为什么不自己写识别：`deepseek-flash` 本身就是多模态的，实测能读出图片上的文字、
认出画面内容。这里只做三件事——编码、问一句、把答案缓存起来（同一张图不重复花钱）。
"""

import base64
import hashlib
from pathlib import Path

from .images import sniff_image
from .llm import LLMClient

MAX_NOTE_CHARS = 120
MAX_VIDEO_CHARS = 520

PROMPT = (
    "用一到两句话说明这张图里是什么：谁、在做什么、有没有文字或界面。"
    "如果它像表情包或梗图，再补一句它想表达的情绪。"
    "只描述画面，不要寒暄，不要分点，不超过 80 字。"
)

STICKER_PROMPT = (
    "这是一张聊天表情包。用一到两句话说明画面内容，再说它传达的情绪"
    "（比如开心、无语、害羞、疲惫）。不超过 80 字，不要寒暄。"
)


def _mime(data: bytes) -> str:
    sniffed = sniff_image(data)
    return sniffed[0] if sniffed else "image/jpeg"


def describe(llm: LLMClient, data: bytes, sticker: bool = False) -> str:
    """问多模态模型：这张图是什么。失败返回空串，调用方自己兜底。"""
    if not data:
        return ""
    encoded = base64.b64encode(data).decode("ascii")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": STICKER_PROMPT if sticker else PROMPT},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{_mime(data)};base64,{encoded}"},
                },
            ],
        }
    ]
    result = llm.chat(messages, temperature=0.2, max_tokens=2000, purpose="vision")
    return _clean(result.text)


def _clean(text: str, max_chars: int = MAX_NOTE_CHARS) -> str:
    note = " ".join((text or "").split())
    if len(note) > max_chars:
        note = note[: max_chars - 1] + "…"
    return note


def describe_frames(
    llm: LLMClient,
    frames: list[bytes],
    context: str = "",
) -> str:
    """多帧视频理解：一次交给多模态模型，返回视频实际内容摘要。"""
    frames = [data for data in frames if data][:6]
    if not frames:
        return ""
    parts = [
        {
            "type": "text",
            "text": (
                "这是同一条抖音视频按时间顺序截取的关键帧。"
                "请说明视频实际在讲什么、出现了哪些人物/物品/操作步骤，"
                "以及画面上的文字、字幕或界面。"
                "只根据画面判断，不确定就说不确定；不要复述标题，不要编造没看到的内容。"
                "用三到五句话概括。"
                + (f"\n标题/文案（仅作辅助，不要照抄）：{context[:300]}" if context else "")
            ),
        }
    ]
    for data in frames:
        encoded = base64.b64encode(data).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{_mime(data)};base64,{encoded}"},
            }
        )
    try:
        result = llm.chat(
            [{"role": "user", "content": parts}],
            temperature=0.2,
            max_tokens=2000,
            purpose="vision_video",
        )
    except Exception:  # noqa: BLE001 - 视频理解失败由调用方降级
        return ""
    return _clean(getattr(result, "text", "") or "", max_chars=MAX_VIDEO_CHARS)


def note_for(store, llm: LLMClient, data: bytes, sticker: bool = False) -> str:
    """带缓存的描述：同一张图（sha256 相同）只问一次模型。"""
    if not data:
        return ""
    sha = hashlib.sha256(data).hexdigest()
    cached = store.get_image_note(sha)
    if cached:
        return cached
    note = describe(llm, data, sticker=sticker)
    if note:
        store.save_image_note(sha, note)
    return note


def sentence(note: str, sticker: bool = False) -> str:
    """把描述包成一句她读得懂的话，塞进用户消息里。"""
    if not note:
        return ""
    label = "表情包" if sticker else "图片"
    return f"（他发来一张{label}：{note}）"


def read_local(path: Path) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError:
        return b""
