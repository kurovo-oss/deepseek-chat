"""抖音视频理解：在已打开的播放页截几张关键帧，交给多模态模型总结。

不下载原视频，不绕过验证码；只截当前页面可见的播放器。
截帧失败时返回空串，调用方必须降级成“只看到文案”。
"""

from __future__ import annotations

import hashlib
import logging
import time

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
    if not frames:
        return ""
    summary = vision.describe_frames(llm, frames, context=desc)
    if summary:
        store.save_video_note(
            str(aweme_id),
            summary,
            desc_hash=desc_hash,
            frames=[f"frame:{index}" for index in range(len(frames))],
            now=now,
        )
    return summary
