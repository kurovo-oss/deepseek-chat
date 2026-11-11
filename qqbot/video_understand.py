"""抖音视频理解：优先截播放页关键帧；页面不渲染时用媒体文件兜底。

不绕过验证码；下载只用于本地截帧/ASR，临时文件放项目目录，
失败时返回空串，调用方必须降级成“只看到文案”。
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import tempfile
import time
import urllib.request
from pathlib import Path

from . import asr
from . import vision

log = logging.getLogger("qqbot")

FRACTIONS = (0.15, 0.4, 0.65, 0.88)
MAX_FRAMES = 4

FRAME_INFO_JS = """(() => {
  const v = document.querySelector('video');
  if (!v) return {ok: false, reason: 'no_video'};
  v.muted = true;
  try { v.scrollIntoView({block: 'center'}); } catch (e) {}
  try { v.play(); } catch (e) {}
  return new Promise((resolve) => {
    const finish = () => {
      const r = v.getBoundingClientRect();
      resolve({
        ok: true,
        duration: Number(v.duration) || 0,
        rect: {x: r.left, y: r.top, width: r.width, height: r.height},
        viewport: {width: window.innerWidth, height: window.innerHeight},
        paused: v.paused,
      });
    };
    if (v.readyState >= 2) {
      setTimeout(finish, 220);
      return;
    }
    const timer = setTimeout(finish, 8000);
    v.addEventListener('loadeddata', () => {
      clearTimeout(timer);
      setTimeout(finish, 220);
    }, {once: true});
  });
})()"""

SEEK_JS = """(async (target) => {
  const v = document.querySelector('video');
  if (!v) return false;
  try { v.pause(); } catch (e) {}
  try { v.currentTime = target; } catch (e) { return false; }
  await new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      v.removeEventListener('seeked', finish);
      resolve();
    };
    v.addEventListener('seeked', finish, {once: true});
    setTimeout(finish, 1600);
  });
  return true;
})"""


def _clip(rect: dict, viewport: dict, max_width: int = 720) -> dict | None:
    width = float(rect.get("width") or 0)
    height = float(rect.get("height") or 0)
    x = float(rect.get("x") or 0)
    y = float(rect.get("y") or 0)
    if width < 80 or height < 60:
        return None
    vw = float(viewport.get("width") or 1280)
    vh = float(viewport.get("height") or 900)
    x = max(0.0, min(x, vw - 1))
    y = max(0.0, min(y, vh - 1))
    width = min(width, vw - x)
    height = min(height, vh - y)
    if width < 80 or height < 60:
        return None
    scale = min(1.0, max_width / width)
    return {"x": x, "y": y, "width": width, "height": height, "scale": scale}


def capture_frames(page, fractions: tuple[float, ...] = FRACTIONS) -> list[bytes]:
    """截取当前视频页的关键帧；失败返回空列表。"""
    if page is None:
        return []
    try:
        page.call("Page.enable", timeout=10)
        page.call("Runtime.enable", timeout=10)
    except Exception:  # noqa: BLE001 - 有的页面不需要 enable
        pass
    try:
        info = page.evaluate(FRAME_INFO_JS, timeout=20) or {}
    except Exception:  # noqa: BLE001 - 页面结构变了就降级
        log.info("视频截帧失败：读不到播放器", exc_info=True)
        return []
    if not info.get("ok"):
        return []
    duration = float(info.get("duration") or 0)
    if duration <= 0:
        return []
    clip = _clip(info.get("rect") or {}, info.get("viewport") or {})
    if clip is None:
        return []
    frames: list[bytes] = []
    for fraction in fractions[:MAX_FRAMES]:
        target = max(0.0, min(duration - 0.2, duration * fraction))
        try:
            if not page.evaluate(f"({SEEK_JS})({target!r})", timeout=20):
                continue
            data = page.screenshot(clip=clip, quality=70, timeout=20)
        except Exception:  # noqa: BLE001 - 单帧失败不影响其他帧
            log.info("视频截帧失败：fraction=%s", fraction, exc_info=True)
            continue
        if data:
            frames.append(data)
    return frames


def _ffmpeg_path(settings) -> Path:
    path = Path(settings.asr_ffmpeg)
    return path if path.is_absolute() else Path(settings.base_dir) / path


def _duration(video_path: Path, settings) -> float:
    try:
        result = asr._run([str(_ffmpeg_path(settings)), "-i", str(video_path)], settings, 30)
    except Exception:  # noqa: BLE001
        return 0.0
    match = re.search(
        r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
        (result.stderr or "") + (result.stdout or ""),
    )
    if not match:
        return 0.0
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))


def capture_frames_from_file(
    video_path: Path | str,
    settings,
    fractions: tuple[float, ...] = FRACTIONS,
) -> list[bytes]:
    """用 ffmpeg 从本地音视频提取关键帧；页面不渲染时的兜底。"""
    duration = _duration(Path(video_path), settings)
    if duration <= 0:
        return []
    work = Path(tempfile.mkdtemp(prefix="frames-", dir=str(asr.temp_dir(settings))))
    frames: list[bytes] = []
    try:
        for index, fraction in enumerate(fractions[:MAX_FRAMES]):
            target = max(0.0, min(duration - 0.2, duration * fraction))
            output = work / f"frame-{index}.jpg"
            try:
                result = asr._run(
                    [
                        str(_ffmpeg_path(settings)),
                        "-y",
                        "-ss",
                        str(target),
                        "-i",
                        str(video_path),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=640:-2",
                        "-q:v",
                        "3",
                        str(output),
                    ],
                    settings,
                    30,
                )
            except Exception:  # noqa: BLE001
                continue
            if result.returncode == 0 and output.is_file():
                data = output.read_bytes()
                if data:
                    frames.append(data)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return frames


def _download_media(url: str, settings) -> tuple[Path | None, Path | None]:
    if not str(url or "").startswith(("http://", "https://")):
        return None, None
    work = Path(tempfile.mkdtemp(prefix="media-", dir=str(asr.temp_dir(settings))))
    target = work / "media.bin"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": asr.USER_AGENT,
            "Referer": "https://www.douyin.com/",
        },
    )
    try:
        written = 0
        with urllib.request.urlopen(request, timeout=getattr(settings, "asr_timeout", 120)) as response:
            with target.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > asr.MAX_DOWNLOAD_BYTES:
                        shutil.rmtree(work, ignore_errors=True)
                        return None, None
                    output.write(chunk)
        if written <= 0:
            shutil.rmtree(work, ignore_errors=True)
            return None, None
        return target, work
    except Exception:  # noqa: BLE001
        shutil.rmtree(work, ignore_errors=True)
        return None, None


def context_block(store, days: int = 2, limit: int = 2, max_chars: int = 240) -> str:
    """最近看过的视频摘要；让正常聊天也能接上“刚刷到的那个视频”。"""
    since = int(time.time()) - max(1, int(days)) * 86400
    rows = store.recent_video_notes(since=since, limit=max(1, int(limit)))
    lines = []
    for row in rows:
        summary = " ".join(str(row.get("summary") or "").split())[:120]
        if summary:
            lines.append(f"- {summary}")
    if not lines:
        return ""
    return ("【最近看过的视频】\n" + "\n".join(lines))[:max_chars]


def probe_page(page, llm, desc: str = "") -> dict | None:
    """用一帧做内容预判；不值得继续时返回低分，失败返回 None 交给旧流程。"""
    if page is None or llm is None:
        return None
    frames = capture_frames(page, fractions=(0.22,))
    if not frames:
        return None
    result = vision.classify_frame(llm, frames[0], context=desc)
    if result is None:
        return None
    result["frame"] = frames[0]
    return result


def analyze_page(
    store,
    llm,
    page,
    aweme_id: str,
    desc: str = "",
    now: int | None = None,
    media_url: str = "",
    settings=None,
) -> str:
    """分析当前打开的视频页；有缓存就直接返回。"""
    if llm is None or page is None or not aweme_id:
        return ""
    desc = str(desc or "")
    desc_hash = hashlib.sha1(desc.encode("utf-8")).hexdigest()[:16] if desc else ""
    cached = store.get_video_note(str(aweme_id), desc_hash)
    if cached:
        return cached
    frames = capture_frames(page)
    transcript = ""
    work = None
    media_path = None
    if (
        media_url
        and settings is not None
        and (not frames or getattr(settings, "asr_enabled", False))
    ):
        media_path, work = _download_media(media_url, settings)
        if media_path is not None:
            if not frames:
                frames = capture_frames_from_file(media_path, settings)
            if getattr(settings, "asr_enabled", False):
                transcript = asr.transcribe_video(media_path, settings)
    try:
        if not frames:
            return ""
        summary = vision.describe_frames(
            llm, frames, context=desc, transcript=transcript
        )
        if summary:
            store.save_video_note(
                str(aweme_id),
                summary,
                desc_hash=desc_hash,
                transcript=transcript,
                frames=[f"frame:{index}" for index in range(len(frames))],
                now=now,
            )
        return summary
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)
