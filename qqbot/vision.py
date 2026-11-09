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


def _clean(text: str) -> str:
    note = " ".join((text or "").split())
    if len(note) > MAX_NOTE_CHARS:
        note = note[: MAX_NOTE_CHARS - 1] + "…"
    return note


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
