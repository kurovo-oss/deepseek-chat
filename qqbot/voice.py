"""语音：情绪重的时候说一句，不是每条都发。

为什么单独一层：语音条是"真人感"里最贵的一样东西——用对了很亲，用错了很吵。
所以这里只做三件事：
1. 决定这条要不要发（情绪 + 长度 + 概率）
2. 把话合成成 mp3（edge-tts，音色偏少女）
3. 发不出去就算了，绝不因为语音失败影响文字

依赖：edge-tts（已装在项目自己的 Python 里）。没装、没网、合成失败都只是少一条语音。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("qqbot")

EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")
MARK_RE = re.compile(r"\[[^\]]{1,6}\]")

# 音色：晓伊偏甜、晓晓偏成熟、晓涵偏轻快
VOICES = {
    "xiaoyi": "zh-CN-XiaoyiNeural",
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",
    "xiaohan": "zh-CN-XiaohanNeural",
}


def clean_text(parts: list[str]) -> str:
    """能念出来的部分：去掉表情和 [表情包] 这类标记。"""
    text = "".join(parts or [])
    text = MARK_RE.sub("", text)
    text = EMOJI_RE.sub("", text)
    return re.sub(r"\s+", "", text).strip()


def should_speak(parts: list[str], values: dict, rng, settings) -> bool:
    if not bool(getattr(settings, "voice_enabled", False)):
        return False
    text = clean_text(parts)
    limit = int(getattr(settings, "voice_max_chars", 32) or 32)
    if len(text) < 3 or len(text) > limit:
        return False
    chance = float(getattr(settings, "voice_chance", 0.0) or 0.0)
    if chance <= 0:
        return False
    if float(values.get("tired") or 0) >= 0.5:
        chance = min(0.9, chance * 1.6)
    if float(values.get("miss") or 0) >= 0.6:
        chance = min(0.9, chance * 1.5)
    if float(values.get("warmth") or 0) >= 0.7:
        chance = min(0.9, chance * 1.2)
    return rng.random() < chance


def _gpt_sovits(text: str, settings, folder: Path) -> Path | None:
    ref = Path(getattr(settings, "gpt_sovits_ref_audio", ""))
    if not ref.is_absolute():
        ref = Path(settings.base_dir) / ref
    if not ref.is_file():
        log.info("GPT-SoVITS 参考音频不存在：%s", ref)
        return None
    payload = {
        "text": text,
        "text_lang": str(getattr(settings, "gpt_sovits_text_lang", "zh")),
        "ref_audio_path": str(ref),
        "prompt_text": str(getattr(settings, "gpt_sovits_prompt_text", "") or ""),
        "prompt_lang": str(getattr(settings, "gpt_sovits_prompt_lang", "zh")),
        "top_k": int(getattr(settings, "gpt_sovits_top_k", 15)),
        "top_p": 1.0,
        "temperature": float(getattr(settings, "gpt_sovits_temperature", 1.0)),
        "text_split_method": str(getattr(settings, "gpt_sovits_text_split_method", "cut5")),
        "batch_size": 1,
        "media_type": "wav",
        "streaming_mode": False,
    }
    url = str(getattr(settings, "gpt_sovits_url", "http://127.0.0.1:9880")).rstrip("/")
    endpoint = url if url.endswith("/tts") else url + "/tts"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=int(getattr(settings, "gpt_sovits_timeout", 12))
        ) as response:
            data = response.read()
            content_type = str(response.headers.get("Content-Type") or "")
    except Exception as exc:  # noqa: BLE001 - 外部服务失败只降级
        log.warning("GPT-SoVITS 请求失败：%s", exc)
        return None
    if not data or "audio" not in content_type.lower():
        log.warning("GPT-SoVITS 返回异常：%s %s", content_type, data[:120])
        return None
    target = folder / f"{int(time.time() * 1000)}.wav"
    target.write_bytes(data)
    ffmpeg = Path(getattr(settings, "asr_ffmpeg", "tools/ffmpeg/ffmpeg.exe"))
    if not ffmpeg.is_absolute():
        ffmpeg = Path(settings.base_dir) / ffmpeg
    if ffmpeg.is_file():
        mp3 = target.with_suffix(".mp3")
        try:
            result = subprocess.run(
                [
                    str(ffmpeg), "-y", "-i", str(target), "-vn",
                    "-codec:a", "libmp3lame", "-q:a", "5", str(mp3),
                ],
                cwd=str(settings.base_dir),
                env={**os.environ, "TMP": str(folder), "TEMP": str(folder), "TMPDIR": str(folder)},
                capture_output=True,
                timeout=30,
            )
            if result.returncode == 0 and mp3.is_file():
                target.unlink(missing_ok=True)
                return mp3
        except (OSError, subprocess.SubprocessError):
            log.warning("GPT-SoVITS WAV 转 MP3 失败", exc_info=True)
    return target


def _cosyvoice(text: str, settings, folder: Path) -> Path | None:
    ref = Path(getattr(settings, "cosyvoice_ref_audio", ""))
    if not ref.is_absolute():
        ref = Path(settings.base_dir) / ref
    if not ref.is_file():
        log.info("CosyVoice 参考音频不存在：%s", ref)
        return None
    payload = {
        "text": text,
        "ref_audio": str(ref),
        "prompt_text": str(getattr(settings, "cosyvoice_prompt_text", "") or ""),
        "instruct": str(getattr(settings, "cosyvoice_instruct", "") or ""),
    }
    url = str(getattr(settings, "cosyvoice_url", "http://127.0.0.1:50000")).rstrip("/")
    endpoint = url if url.endswith("/tts") else url + "/tts"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=int(getattr(settings, "cosyvoice_timeout", 60))
        ) as response:
            data = response.read()
            content_type = str(response.headers.get("Content-Type") or "")
    except Exception as exc:  # noqa: BLE001 - 外部服务失败只降级
        log.warning("CosyVoice 请求失败：%s", exc)
        return None
    if not data or "audio" not in content_type.lower():
        log.warning("CosyVoice 返回异常：%s %s", content_type, data[:120])
        return None
    target = folder / f"{int(time.time() * 1000)}.wav"
    target.write_bytes(data)
    ffmpeg = Path(getattr(settings, "asr_ffmpeg", "tools/ffmpeg/ffmpeg.exe"))
    if not ffmpeg.is_absolute():
        ffmpeg = Path(settings.base_dir) / ffmpeg
    if ffmpeg.is_file():
        mp3 = target.with_suffix(".mp3")
        try:
            result = subprocess.run(
                [str(ffmpeg), "-y", "-i", str(target), "-vn",
                 "-codec:a", "libmp3lame", "-q:a", "5", str(mp3)],
                cwd=str(settings.base_dir),
                env={**os.environ, "TMP": str(folder), "TEMP": str(folder), "TMPDIR": str(folder)},
                capture_output=True,
                timeout=30,
            )
            if result.returncode == 0 and mp3.is_file():
                target.unlink(missing_ok=True)
                return mp3
        except (OSError, subprocess.SubprocessError):
            log.warning("CosyVoice WAV 转 MP3 失败", exc_info=True)
    return target


def synthesize(text: str, settings, out_dir: Path | None = None) -> Path | None:
    """合成一条音频；失败返回 None（没装、没网、被限流都算失败）。"""
    text = (text or "").strip()
    if not text or not bool(getattr(settings, "voice_enabled", False)):
        return None
    folder = Path(out_dir or getattr(settings, "voice_dir", "data/voice"))
    folder.mkdir(parents=True, exist_ok=True)
    provider = str(getattr(settings, "voice_provider", "edge") or "edge").lower()
    if provider == "gpt_sovits":
        path = _gpt_sovits(text, settings, folder)
        if path is not None:
            prune(folder)
        return path
    if provider == "cosyvoice":
        path = _cosyvoice(text, settings, folder)
        if path is not None:
            prune(folder)
        return path
    try:
        import edge_tts
    except Exception:  # noqa: BLE001 - 没装 edge-tts 就不发语音
        log.info("没装 edge-tts，跳过语音")
        return None

    folder = Path(out_dir or getattr(settings, "voice_dir", "data/voice"))
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{int(time.time() * 1000)}.mp3"
    voice = VOICES.get(str(getattr(settings, "voice_name", "xiaoyi")), "zh-CN-XiaoyiNeural")
    rate = str(getattr(settings, "voice_rate", "+8%") or "+8%")
    pitch = str(getattr(settings, "voice_pitch", "+12Hz") or "+12Hz")

    async def _run() -> None:
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        await communicate.save(str(target))

    try:
        asyncio.run(_run())
    except Exception:  # noqa: BLE001 - 语音是加分项，失败就少一条
        log.warning("语音合成失败：%r", text[:20], exc_info=True)
        return None
    if not target.is_file() or target.stat().st_size < 800:
        return None
    prune(folder)
    return target


def prune(folder: Path, keep: int = 60) -> int:
    files = sorted(list(Path(folder).glob("*.mp3")) + list(Path(folder).glob("*.wav")))
    removed = 0
    for path in files[:-keep] if keep > 0 else files:
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed
