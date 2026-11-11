"""可选本地 ASR：SenseVoiceSmall + sherpa-onnx CLI。

约束：
- 默认关闭；模型/二进制缺失时静默降级；
- 所有临时音频和下载都放在项目内（默认 data/tmp/asr），绝不写 C 盘；
- 不把模型、ffmpeg、音频提交进仓库；
- 只接收本地文件或短期音视频 URL，失败返回空字符串。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

log = logging.getLogger("qqbot")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


class ASRError(RuntimeError):
    pass


def _path(settings, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(settings.base_dir) / path


def _is_c_drive(path: Path) -> bool:
    return str(path.drive).upper().startswith("C:")


def temp_dir(settings) -> Path:
    """始终返回项目内的临时目录；配置误填 C 盘时回退到项目 data/tmp/asr。"""
    configured = _path(settings, getattr(settings, "asr_tmp_dir", "data/tmp/asr")).resolve()
    base = Path(settings.base_dir).resolve()
    try:
        configured.relative_to(base)
        safe = not _is_c_drive(configured)
    except ValueError:
        safe = False
    if not safe:
        fallback_base = PROJECT_ROOT if not _is_c_drive(PROJECT_ROOT) else base
        configured = (fallback_base / "data" / "tmp" / "asr").resolve()
    configured.mkdir(parents=True, exist_ok=True)
    return configured


def _env(settings) -> dict[str, str]:
    env = os.environ.copy()
    temp = str(temp_dir(settings))
    env["TMP"] = temp
    env["TEMP"] = temp
    env["TMPDIR"] = temp
    return env


def _run(command: list[str], settings, timeout: float) -> subprocess.CompletedProcess:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        command,
        cwd=str(Path(settings.base_dir)),
        env=_env(settings),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(5.0, float(timeout)),
        **kwargs,
    )


def available(settings) -> tuple[bool, str]:
    if not getattr(settings, "asr_enabled", False):
        return False, "ASR 未启用"
    checks = (
        ("sherpa-onnx", "asr_bin"),
        ("SenseVoice 模型", "asr_model"),
        ("tokens", "asr_tokens"),
        ("ffmpeg", "asr_ffmpeg"),
    )
    for label, attr in checks:
        path = _path(settings, getattr(settings, attr, ""))
        if not path.is_file():
            return False, f"{label} 不存在：{path}"
    return True, ""


def _parse_text(raw: str) -> str:
    """兼容 JSON 输出和常见的 `0.0 -- 1.2: 文本` 输出。"""
    raw = raw or ""
    matches = re.findall(r'"text"\s*:\s*"((?:\\.|[^"\\])*)"', raw)
    if matches:
        pieces = []
        for item in matches:
            try:
                pieces.append(json.loads(f'"{item}"'))
            except ValueError:
                pieces.append(item)
        text = " ".join(piece.strip() for piece in pieces if piece.strip())
        return text.strip()
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("[") or line.lower().startswith(("sherpa", "parse", "warn")):
            continue
        line = re.sub(r"^\d+(?:\.\d+)?\s*--\s*\d+(?:\.\d+)?\s*:\s*", "", line)
        if line.startswith("text:") or line.startswith("text："):
            line = line.split(":", 1)[-1].split("：", 1)[-1].strip()
        if line:
            lines.append(line)
    return " ".join(lines).strip()


def extract_wav(video_path: Path | str, wav_path: Path | str, settings) -> bool:
    ffmpeg = _path(settings, getattr(settings, "asr_ffmpeg", "tools/ffmpeg/ffmpeg.exe"))
    command = [
        str(ffmpeg),
        "-y",
        "-i",
        str(video_path),
        "-t",
        str(max(5, int(getattr(settings, "asr_max_seconds", 180)))),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(wav_path),
    ]
    try:
        result = _run(command, settings, timeout=getattr(settings, "asr_timeout", 120))
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("ASR ffmpeg 失败：%s", exc)
        return False
    if result.returncode != 0 or not Path(wav_path).is_file():
        log.info("ASR ffmpeg 返回 %s：%s", result.returncode, (result.stderr or "")[:300])
        return False
    return True


def transcribe_video(video_path: Path | str, settings) -> str:
    ok, reason = available(settings)
    if not ok:
        log.info("ASR 跳过：%s", reason)
        return ""
    work = Path(tempfile.mkdtemp(prefix="job-", dir=str(temp_dir(settings))))
    wav = work / "audio.wav"
    try:
        if not extract_wav(video_path, wav, settings):
            return ""
        command = [
            str(_path(settings, settings.asr_bin)),
            f"--tokens={_path(settings, settings.asr_tokens)}",
            f"--sense-voice-model={_path(settings, settings.asr_model)}",
            "--use-itn=true",
            f"--language={getattr(settings, 'asr_language', 'zh')}",
            str(wav),
        ]
        try:
            result = _run(command, settings, timeout=getattr(settings, "asr_timeout", 120))
        except (OSError, subprocess.SubprocessError) as exc:
            log.info("ASR 进程失败：%s", exc)
            return ""
        if result.returncode != 0:
            log.info("ASR 返回 %s：%s", result.returncode, (result.stderr or "")[:300])
            return ""
        text = _parse_text((result.stdout or "") + "\n" + (result.stderr or ""))
        return text[:4000]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def transcribe_url(
    url: str,
    settings,
    headers: dict[str, str] | None = None,
) -> str:
    """下载短期音视频 URL 到项目临时目录，转写后立刻删除。"""
    ok, reason = available(settings)
    if not ok:
        log.info("ASR 跳过：%s", reason)
        return ""
    if not str(url or "").startswith(("http://", "https://")):
        return ""
    work = Path(tempfile.mkdtemp(prefix="url-", dir=str(temp_dir(settings))))
    target = work / "media.bin"
    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://www.douyin.com/",
                **(headers or {}),
            },
        )
        written = 0
        with urllib.request.urlopen(request, timeout=getattr(settings, "asr_timeout", 120)) as response:
            with target.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_DOWNLOAD_BYTES:
                        log.info("ASR 跳过：媒体超过 %d MB", MAX_DOWNLOAD_BYTES // 1024 // 1024)
                        return ""
                    output.write(chunk)
        if written <= 0:
            return ""
        return transcribe_video(target, settings)
    except Exception as exc:  # noqa: BLE001 - 下载失败只降级
        log.info("ASR 下载失败：%s", exc)
        return ""
    finally:
        shutil.rmtree(work, ignore_errors=True)
