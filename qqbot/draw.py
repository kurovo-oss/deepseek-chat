"""画图：她说"我画了"，就真给她一张图。

两个来源：
- `pollinations`：免费、不要 key，几秒钟出图（默认）
- `openai`：任何 OpenAI 兼容的图片接口（自己填 base_url / key / model）

别人的活不重写：只负责"把话变成提示词 → 拿到字节 → 落盘 → 交给发送层"。
"""

import base64
import copy
import hashlib
import json
import logging
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
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
    "an anime whale-themed character with two-head chibi proportions,"
    " a round soft body with slim limbs,"
    " long wavy hair graded from deep navy to light cyan with lighter tips,"
    " straight bangs and one ahoge on top,"
    " whale-fin shaped animal ears with a blue bow,"
    " a white frilled maid headband,"
    " a navy maid dress with a white frilled apron printed with a small whale"
    " and a big white bow at the back,"
    " white lace ankle socks and navy shoes,"
    " a huge thick round navy whale tail,"
    " big clear blue eyes and a gentle cute expression,"
    " flat cel shading, low saturation"
)

PROMPT_SYSTEM = (
    "把下面这句中文改写成一段给画图模型用的英文提示词，只输出提示词本身，"
    "不要解释、不要引号、不要分点。\n"
    "如果这句话是在画她自己（出现「我」「自己」「鲸鱼娘」「大肥鱼」），要写 full body shot，"
    "让整条鲸尾露在画面里，并且必须把这段固定外观原样写进提示词：" + CHARACTER + "。"
)
MAX_PROMPT_CHARS = 400

# 智谱这类国内接口审核很严："chibi""girl" 容易被判成未成年，直接 400。
# 命中 contentFilter 就把这些词换掉重试一次。
SOFTEN_MAP = {
    "chibi": "",
    "little girl": "character",
    "young girl": "character",
    "girl": "character",
    "girls": "characters",
    "loli": "",
    "child": "character",
    "kid": "character",
    "schoolgirl": "character",
    "bikini": "swimsuit",
    "lingerie": "clothes",
}


class ContentFiltered(Exception):
    """接口的内容审核拦了这次生成。"""


def soften(prompt: str) -> str:
    text = prompt
    for word, replacement in SOFTEN_MAP.items():
        text = re.sub(rf"\b{re.escape(word)}\b", replacement, text, flags=re.IGNORECASE)
    return " ".join(text.split())


# 国内接口（智谱等）中文提示词明显更准：直接用中文写，不要再翻成英文
CHARACTER_ZH = (
    "Q版蓝鲸娘，两头身，身体圆润但四肢纤细。深蓝渐变浅蓝的长卷发，齐刘海，"
    "头顶一根呆毛，鲸鱼鳍形状的兽耳，蓝色蝴蝶结发饰，白色荷叶边女仆发箍。"
    "深蓝色女仆连衣裙，白色荷叶边围裙，围裙上印小鲸鱼，后腰一个白色大蝴蝶结。"
    "白色蕾丝短袜，深蓝色小皮鞋。身后一条巨大肥厚圆润的深蓝色鲸鱼尾巴。"
    "蓝色通透的大眼睛，呆萌柔和的表情。日系二次元平涂，干净清晰的黑色轮廓线，"
    "柔和阴影，低饱和度，赛璐璐上色"
)
MAX_PROMPT_CHARS_ZH = 400
# 她不该出现的样子（用户给的负面词）。智谱会忽略，但硅基流动这类支持 negative_prompt。
NEGATIVE = (
    "(worst quality, low quality:1.4), blurry, ugly, deformed, disfigured,"
    " extra limbs, bad anatomy, thick legs, fat legs, muscular legs, realistic, 3d,"
    " render, photo, messy background, watermark, text clutter, multiple girls, gore,"
    " dark, heavy shading, over saturated, big hands, long torso, normal proportion,"
    " 4头身以上"
)


SELF_HINTS = ("我", "自己", "鲸鱼娘", "大肥鱼")


def is_self_portrait(text: str) -> bool:
    """这句话是不是在画她自己。她写"我"基本就是指自己。"""
    return any(hint in (text or "") for hint in SELF_HINTS)


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
    }
    headers = {"Content-Type": "application/json"}
    if settings.draw_api_key:
        headers["Authorization"] = f"Bearer {settings.draw_api_key}"
    endpoint = settings.draw_base_url.rstrip("/") + "/images/generations"

    # 各家支持不一致：response_format 智谱不认、negative_prompt 有的也不认，
    # 400 时依次退化成更"朴素"的请求体再试。
    full = dict(payload)
    negative = getattr(settings, "draw_negative", "") or ""
    if negative:
        full["negative_prompt"] = negative
    full["response_format"] = "b64_json"
    variants = [
        full,
        {k: v for k, v in full.items() if k != "response_format"},
        {k: v for k, v in full.items() if k not in ("response_format", "negative_prompt")},
    ]
    seen = []
    for body in variants:
        if body not in seen:
            seen.append(body)
    variants = seen
    result = None
    for body in variants:
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8", "replace"))
            break
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            if "contentFilter" in detail or "敏感" in detail:
                raise ContentFiltered(detail[:200]) from exc
            if exc.code == 400 and ("response_format" in body or "negative_prompt" in body):
                log.info("画图接口不认可选字段，退化成朴素请求再试")
                continue
            raise

    if not result:
        return None
    items = result.get("data") or []
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


def _with_model(settings, model: str):
    """复制一份设置只改模型名。settings 是 frozen dataclass，用 replace 最省事。"""
    try:
        return replace(settings, draw_model=model)
    except Exception:  # noqa: BLE001 - 测试里可能是普通对象
        clone = copy.copy(settings)
        setattr(clone, "draw_model", model)
        return clone


def build_prompt(settings, llm: LLMClient | None, text: str) -> str:
    """把她的中文描述变成真正发出去的提示词。

    国内接口吃中文：直接拼中文设定，省一次翻译、也更贴合；免费 flux 吃英文。
    """
    text = (text or "").strip()
    if settings.draw_provider == "openai":
        parts = [text]
        if is_self_portrait(text):
            parts.append(CHARACTER_ZH)
            parts.append("全身，尾巴露出画面")
        parts.append("干净背景，不要文字，不要水印")
        return "。".join(part for part in parts if part)[:MAX_PROMPT_CHARS_ZH]

    prompt = translate(llm, text) if llm is not None else text
    prompt = " ".join((prompt or "").split())
    if is_self_portrait(text) and CHARACTER[:30] not in prompt:
        prompt += ", full body shot with her whale tail visible, " + CHARACTER
    if "no watermark" not in prompt.lower():
        prompt += STYLE_SUFFIX
    return prompt


def generate(settings, llm: LLMClient | None, text: str) -> bytes | None:
    """她写的中文 → 一张图（bytes）。任何一步失败都返回 None，不抛异常。"""
    prompt = build_prompt(settings, llm, text)
    if not prompt:
        return None

    # 先用配置的模型，不行再退到便宜的备用模型，最后才退回免费版：有图总比没有强
    attempts = []
    if settings.draw_provider == "openai":
        attempts.append(("openai", None))
        fallback_model = getattr(settings, "draw_fallback_model", "")
        if fallback_model:
            attempts.append(("openai", fallback_model))
        attempts.append(("pollinations", None))
    else:
        attempts.append(("pollinations", None))
    softened = False

    for provider, model in attempts:
        use = settings if model is None else _with_model(settings, model)
        for attempt in range(2):
            data = None
            try:
                if provider == "openai":
                    data = _openai_compatible(
                        prompt, use.draw_size, use.draw_timeout, use
                    )
                else:
                    data = _pollinations(
                        prompt,
                        use.draw_size,
                        use.draw_timeout,
                        use.draw_model or "flux",
                    )
            except ContentFiltered as exc:
                if not softened:
                    softened = True
                    prompt = soften(prompt)
                    log.info("被内容审核拦了，软化提示词重试：%s", str(exc)[:80])
                    continue
                log.warning("画图被内容审核拦下（软化后仍失败）")
            except urllib.error.HTTPError as exc:
                log.warning(
                    "画图失败（%s，第 %d 次）：HTTP %s %s",
                    provider,
                    attempt + 1,
                    exc.code,
                    exc.reason,
                )
                if exc.code in (401, 402, 403):
                    break  # 没额度、没权限，换下一个供应商
            except Exception as exc:  # noqa: BLE001 - 画图是附加能力，挂了不能影响聊天
                log.warning(
                    "画图失败（%s，第 %d 次）：%s: %s",
                    provider,
                    attempt + 1,
                    type(exc).__name__,
                    exc,
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
