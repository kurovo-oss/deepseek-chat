"""画图：她说"我画了"，就真给她一张图。

两个来源：
- `pollinations`：免费、不要 key，几秒钟出图（默认）
- `openai`：任何 OpenAI 兼容的图片接口（自己填 base_url / key / model）

别人的活不重写：只负责"把话变成提示词 → 拿到字节 → 落盘 → 交给发送层"。
"""

import base64
import hashlib
import json
import random
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .images import sniff_image
from .llm import LLMClient

STYLE_SUFFIX = "，柔和的手绘动画插画风格，暖色调，干净背景，不要文字"
PROMPT_SYSTEM = (
    "把下面这句中文改写成一段给画图模型用的英文提示词。"
    "只输出提示词本身，不要解释、不要引号、不要分点。"
)
MAX_PROMPT_CHARS = 400


def translate(llm: LLMClient, text: str) -> str:
    """她的中文描述 → 画图模型的英文提示词。失败就原样用。"""
    text = (text or "").strip()[:MAX_PROMPT_CHARS]
    if not text:
        return ""
    try:
        result = llm.chat(
            [
                {"role": "system", "content": PROMPT_SYSTEM},
                {"role": "user", "content": text},
            ],
            temperature=0.6,
            max_tokens=300,
        )
    except Exception:  # noqa: BLE001 - 翻译失败不该让画图失败
        return text
    prompt = " ".join((result.text or "").split())
    return prompt[:MAX_PROMPT_CHARS] or text


def _pollinations(prompt: str, size: int, timeout: float) -> bytes | None:
    url = (
        "https://image.pollinations.ai/prompt/"
        + urllib.parse.quote(prompt[:MAX_PROMPT_CHARS])
        + f"?width={size}&height={size}&nologo=true&seed={random.randint(1, 10**6)}"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _openai_compatible(prompt: str, size: int, timeout: float, settings) -> bytes | None:
    if not settings.draw_base_url:
        return None
    payload = {
        "model": settings.draw_model,
        "prompt": prompt[:MAX_PROMPT_CHARS],
        "n": 1,
        "size": f"{size}x{size}",
        "response_format": "b64_json",
    }
    headers = {"Content-Type": "application/json"}
    if settings.draw_api_key:
        headers["Authorization"] = f"Bearer {settings.draw_api_key}"
    request = urllib.request.Request(
        settings.draw_base_url.rstrip("/") + "/images/generations",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    items = data.get("data") or []
    if not items:
        return None
    first = items[0]
    if first.get("b64_json"):
        return base64.b64decode(first["b64_json"])
    url = first.get("url")
    if not url:
        return None
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def generate(settings, llm: LLMClient | None, text: str) -> bytes | None:
    """她写的中文 → 一张图（bytes）。任何一步失败都返回 None，不抛异常。"""
    prompt = translate(llm, text) if llm is not None else text
    prompt = (prompt or "").strip()
    if not prompt:
        return None
    if not prompt.lower().endswith(("style", "illustration", "anime")):
        prompt += STYLE_SUFFIX
    try:
        if settings.draw_provider == "openai":
            data = _openai_compatible(prompt, settings.draw_size, settings.draw_timeout, settings)
        else:
            data = _pollinations(prompt, settings.draw_size, settings.draw_timeout)
    except Exception:  # noqa: BLE001 - 画图是附加能力，挂了不能影响聊天
        return None
    if not data or not sniff_image(data):
        return None
    return data


def save(root, data: bytes) -> "Path | None":
    """落盘：data/drawn/YYYY-MM-DD/<sha>.<ext>，同图只存一份。"""
    sniffed = sniff_image(data)
    if not sniffed:
        return None
    _, ext = sniffed
    digest = hashlib.sha256(data).hexdigest()
    day = time.strftime("%Y-%m-%d", time.localtime())
    target = Path(root) / day / f"{digest[:16]}{ext}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(data)
    return target
