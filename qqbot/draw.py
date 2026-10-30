"""画图：她说"我画了"，就真给她一张图。

两个来源：
- `pollinations`：免费、不要 key，几秒钟出图（默认）
- `openai`：任何 OpenAI 兼容的图片接口（自己填 base_url / key / model）

别人的活不重写：只负责"把话变成提示词 → 拿到字节 → 落盘 → 交给发送层"。
"""

import base64
import hashlib
import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .images import sniff_image
from .llm import LLMClient

log = logging.getLogger("qqbot")

# 踩过的坑：提示词后缀写成中文会把画图模型的输出带崩（文字乱码、结构混乱），
# 一律用英文。
STYLE_SUFFIX = (
    ", soft anime illustration, clean lineart, gentle warm lighting,"
    " simple background, no text, no watermark"
)

# 鲸鱼娘（DeepSeek 社区角色，"吃白饭的蓝色大肥鱼"）的固定外观。
# 她画自己的时候必须带上这一串，否则每次长得都不一样。
CHARACTER = (
    "a chibi anime whale girl with long wavy hair graded from deep navy at the roots"
    " to bright cyan at the tips, one curved ahoge, big deep-blue eyes,"
    " white feathery fin-like ears instead of human ears,"
    " a plain white long-sleeved bodysuit,"
    " and a thick blue whale tail with a white underside and a light-blue split fin"
)

PROMPT_SYSTEM = (
    "把下面这句中文改写成一段给画图模型用的英文提示词，只输出提示词本身，"
    "不要解释、不要引号、不要分点。\n"
    "如果这句话是在画她自己（出现「我」「自己」「鲸鱼娘」「大肥鱼」），要写 full body shot，"
    "让整条鲸尾露在画面里，并且必须把这段固定外观原样写进提示词：" + CHARACTER + "。"
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
            max_tokens=900,
        )
    except Exception:  # noqa: BLE001 - 翻译失败不该让画图失败
        return text
    prompt = " ".join((result.text or "").split())
    return prompt[:MAX_PROMPT_CHARS] or text


def _pollinations(
    prompt: str, size: int, timeout: float, model: str = "flux"
) -> bytes | None:
    # flux 是免费额度里唯一还能用的；turbo/sana 已 402，kontext/seedream 403
    url = (
        "https://image.pollinations.ai/prompt/"
        + urllib.parse.quote(prompt[: MAX_PROMPT_CHARS * 2])
        + f"?width={size}&height={size}&nologo=true&model={model or 'flux'}"
        + f"&seed={random.randint(1, 10**6)}"
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
    prompt = " ".join((prompt or "").split())
    if not prompt:
        return None
    if "no watermark" not in prompt.lower():
        prompt += STYLE_SUFFIX

    for attempt in range(2):
        data = None
        try:
            if settings.draw_provider == "openai":
                data = _openai_compatible(
                    prompt, settings.draw_size, settings.draw_timeout, settings
                )
            else:
                data = _pollinations(
                    prompt,
                    settings.draw_size,
                    settings.draw_timeout,
                    settings.draw_model or "flux",
                )
        except urllib.error.HTTPError as exc:
            log.warning(
                "画图失败（第 %d 次）：HTTP %s %s", attempt + 1, exc.code, exc.reason
            )
            if exc.code in (401, 402, 403):
                return None  # 没额度、没权限，重试也是白搭
        except Exception as exc:  # noqa: BLE001 - 画图是附加能力，挂了不能影响聊天
            log.warning(
                "画图失败（第 %d 次）：%s: %s", attempt + 1, type(exc).__name__, exc
            )

        if data and sniff_image(data):
            return data
        if attempt == 0:
            time.sleep(2)
    return None


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
