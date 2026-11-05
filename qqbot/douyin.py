"""抖音：一个独立 profile 的 Edge/Chrome，走 CDP 刷推荐、点赞、把有趣的分享给主人。

为什么这么绕：抖音的接口签名（a_bogus/msToken）在页面脚本里算，离了页面就失效；
所以不自己算签名，直接在页面里读 DOM、点按钮，浏览器自动化是唯一稳的路子。

安全边界：
- 独立 profile 放 `data/browser/douyin`（H 盘，不碰 C 盘默认目录）
- 只点赞、不评论/不关注/不私信；分享只发链接 + 一句话给主人
- 默认关；开启后也走 Guard：每天最多 5 个赞、2 条分享，验证码/未登录就停
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

from . import cdp as cdp_mod
from . import guard as guard_mod

log = logging.getLogger("qqbot")

HOME = "https://www.douyin.com/?recommend=1"
VIDEO = "https://www.douyin.com/video/{aweme_id}"
SHARE_KEYWORDS = ("猫", "狗", "游戏", "编程", "代码", "做饭", "美食", "离谱", "笑", "手工", "画")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS douyin_seen (
  aweme_id TEXT PRIMARY KEY,
  desc     TEXT NOT NULL DEFAULT '',
  liked    INTEGER NOT NULL DEFAULT 0,
  shared   INTEGER NOT NULL DEFAULT 0,
  at       INTEGER NOT NULL
);
"""

FEED_JS = """(() => {
  const seen = new Set();
  const out = [];
  const nodes = document.querySelectorAll('[data-e2e="feed-item"], a[href*="/video/"], li[data-e2e="feed-item"]');
  for (const node of nodes) {
    let id = node.getAttribute && (node.getAttribute('data-e2e-vid') || '');
    if (!id) {
      const a = node.querySelector ? node.querySelector('a[href*="/video/"]') : node;
      const href = (a && a.getAttribute && a.getAttribute('href')) || '';
      const m = href.match(/\\/video\\/(\\d+)/);
      id = m ? m[1] : '';
    }
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const text = ((node.innerText || '') + ' ' + (node.getAttribute && (node.getAttribute('aria-label') || '') || ''))
      .replace(/\\s+/g, ' ').trim().slice(0, 120);
    out.push({id: id, text: text});
  }
  return out;
})()"""

LOGIN_JS = """(() => {
  const body = document.body ? document.body.innerText : '';
  const hasLoginBtn = /登录|扫码登录/.test(body) && !/退出登录/.test(body);
  const avatar = document.querySelector('img[data-e2e="user-avatar"], [data-e2e="user-avatar"]');
  return {has_login_button: hasLoginBtn, has_avatar: !!avatar, url: location.href};
})()"""

LIKE_JS = """(() => {
  const find = () => document.querySelector('[data-e2e="like-icon"], [data-e2e="video-like"]');
  const icon = find();
  if (!icon) return {ok: false, reason: 'no_like_icon'};
  let button = icon;
  for (let i = 0; i < 4 && button; i++) {
    if (button.tagName === 'BUTTON' || button.getAttribute('role') === 'button') break;
    button = button.parentElement;
  }
  const target = button || icon;
  const before = target.getAttribute('aria-pressed') || target.className;
  target.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
  target.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
  target.click();
  const after = target.getAttribute('aria-pressed') || target.className;
  return {ok: true, changed: before !== after, before: String(before).slice(0, 80), after: String(after).slice(0, 80)};
})()"""


def ensure_table(store) -> None:
    if getattr(store, "_douyin_ready", False):
        return
    with store._lock:  # noqa: SLF001
        store._conn.executescript(_SCHEMA)  # noqa: SLF001
        store._conn.commit()  # noqa: SLF001
    store._douyin_ready = True


def browser_path() -> str | None:
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return None


class Browser:
    """负责拉起浏览器、连上第一个页面。"""

    def __init__(self, settings):
        self.settings = settings
        self.port = int(getattr(settings, "douyin_cdp_port", 9222))
        self.profile = Path(getattr(settings, "douyin_profile", "data/browser/douyin"))
        self.process: subprocess.Popen | None = None
        self.page: cdp_mod.CDP | None = None

    def start(self, timeout: float = 30.0, visible: bool = False) -> cdp_mod.CDP:
        if self.page is not None:
            return self.page
        if not self._port_open():
            exe = browser_path()
            if exe is None:
                raise cdp_mod.CDPError("找不到 Edge/Chrome")
            self.profile.mkdir(parents=True, exist_ok=True)
            args = [
                exe,
                f"--remote-debugging-port={self.port}",
                f"--user-data-dir={self.profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-sync",
                "--disable-features=msEdgeSyncPromo,EdgeSyncPromo,msEdgeSync",
                "--no-service-autorun",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
                "--window-size=1280,900",
            ]
            if not visible:
                args.append("--window-position=-32000,-32000")
            args.append("about:blank")
            self.process = subprocess.Popen(  # noqa: S603 - 路径来自固定候选
                args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            pid_file = self.profile / "browser.pid"
            try:
                pid_file.write_text(str(self.process.pid), encoding="ascii")
            except OSError:
                pass
            log.info("抖音浏览器已启动：%s（profile %s）", exe, self.profile)
        target = cdp_mod.wait_for_cdp(self.port, timeout=timeout, prefer="douyin.com")
        self.page = cdp_mod.CDP(target["webSocketDebuggerUrl"])
        return self.page

    def _port_open(self) -> bool:
        try:
            cdp_mod.list_targets(self.port, timeout=1.0)
            return True
        except Exception:  # noqa: BLE001
            return False

    def stop(self) -> None:
        if self.page is not None:
            try:
                self.page.close()
            except Exception:  # noqa: BLE001
                pass
            self.page = None
        if self.process is not None:
            try:
                self.process.terminate()
            except Exception:  # noqa: BLE001
                pass
            self.process = None


def login_probe(page: cdp_mod.CDP) -> dict:
    page.call("Runtime.enable")
    return page.evaluate(LOGIN_JS) or {}


def read_feed(page: cdp_mod.CDP, limit: int = 10) -> list[dict]:
    page.call("Runtime.enable")
    items = page.evaluate(FEED_JS) or []
    return [item for item in items if item.get("id")][:limit]


def like_current(page: cdp_mod.CDP) -> dict:
    page.call("Runtime.enable")
    return page.evaluate(LIKE_JS) or {"ok": False, "reason": "no_result"}


def _today(now: int) -> int:
    local = time.localtime(now)
    return int(time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)))


def seen_today(store, now: int) -> set[str]:
    ensure_table(store)
    with store._lock:  # noqa: SLF001
        rows = store._conn.execute(  # noqa: SLF001
            "SELECT aweme_id FROM douyin_seen WHERE at >= ?", (_today(now),)
        ).fetchall()
    return {row["aweme_id"] for row in rows}


def remember(store, aweme_id: str, desc: str, liked: bool = False, shared: bool = False,
             now: int | None = None) -> None:
    ensure_table(store)
    stamp = int(now if now is not None else time.time())
    with store._lock:  # noqa: SLF001
        store._conn.execute(  # noqa: SLF001
            "INSERT INTO douyin_seen (aweme_id, desc, liked, shared, at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(aweme_id) DO UPDATE SET liked = MAX(liked, excluded.liked),"
            " shared = MAX(shared, excluded.shared), at = excluded.at",
            (aweme_id, desc[:120], int(liked), int(shared), stamp),
        )
        store._conn.commit()  # noqa: SLF001


def interesting(text: str) -> bool:
    return any(word in (text or "") for word in SHARE_KEYWORDS)


def share_text(text: str, aweme_id: str) -> str:
    line = (text or "").strip()[:40] or "刷到一个有点意思的"
    return f"{line}\nhttps://www.douyin.com/video/{aweme_id}"


def run_once(store, settings, guard, sender, browser: Browser, now: int | None = None,
             notify=None) -> dict:
    """刷一轮：读推荐 → 点赞 → 挑有趣的分享。返回统计。"""
    stamp = int(now if now is not None else time.time())
    result = {"liked": 0, "shared": 0, "skipped": 0, "error": ""}
    if not getattr(settings, "douyin_enabled", False):
        return result
    try:
        page = browser.start()
        home = getattr(settings, "douyin_home_url", HOME) or HOME
        video_url = getattr(settings, "douyin_video_url", VIDEO) or VIDEO
        page.goto(home, timeout=40)
        time.sleep(3)
        state = login_probe(page)
        if not state.get("has_avatar") and state.get("has_login_button"):
            result["error"] = "not_logged_in"
            last = int(store.get_cursor("douyin:notified", "0") or 0)
            if notify is not None and stamp - last > 12 * 3600:
                store.set_cursor("douyin:notified", str(stamp))
                notify("抖音还没登录：发 /抖音 登录，在服务器上扫码，登录一次就好")
            return result
        feed = read_feed(page, limit=12)
        today = seen_today(store, stamp)
        for item in feed:
            if item["id"] in today:
                continue
            decision = guard.check("douyin", "like", target=item["id"],
                                   user_id=settings.allow_user_id, now=stamp)
            if not decision.allowed:
                result["skipped"] += 1
                break
            page.goto(video_url.format(aweme_id=item["id"]), timeout=40)
            time.sleep(2)
            outcome = like_current(page)
            ok = bool(outcome.get("ok")) and bool(outcome.get("changed", True))
            guard.record("douyin", "like", target=item["id"], payload=item.get("text", ""),
                         result="ok" if ok else "failed",
                         error="" if ok else str(outcome.get("reason") or outcome.get("after") or "")[:200],
                         now=stamp)
            remember(store, item["id"], item.get("text", ""), liked=ok, now=stamp)
            if not ok:
                result["error"] = "like_failed"
                break
            result["liked"] += 1
            if interesting(item.get("text", "")):
                share = guard.check("douyin_share", "share", target=item["id"],
                                    user_id=settings.allow_user_id, now=stamp)
                ok_send = False
                if share.allowed:
                    text = share_text(item.get("text", ""), item["id"])
                    if share.dry_run:
                        guard.record("douyin_share", "share", target=item["id"], payload=text,
                                     result="dry_run", now=stamp)
                    else:
                        ok_send = sender.send_private(settings.allow_user_id, text)
                        guard.record("douyin_share", "share", target=item["id"], payload=text,
                                     result="ok" if ok_send else "failed", now=stamp)
                    if share.dry_run or ok_send:
                        result["shared"] += 1
                        remember(store, item["id"], item.get("text", ""), liked=ok, shared=True, now=stamp)
            time.sleep(8)  # 两个动作之间至少隔 8 秒，别像脚本
    except Exception as exc:  # noqa: BLE001 - 浏览器/网络问题不该拖垮主进程
        result["error"] = str(exc)[:200]
        log.warning("抖音这一轮失败：%s", exc)
    return result


def tick_loop(store, settings, guard, sender, stop_event, interval: float = 1200.0) -> None:
    browser = Browser(settings)
    def notify(text: str) -> None:
        try:
            sender.send_private(settings.allow_user_id, text)
        except Exception:  # noqa: BLE001
            log.info("抖音提醒没发出去", exc_info=True)

    while not stop_event.wait(interval):
        try:
            result = run_once(store, settings, guard, sender, browser, notify=notify)
            if result["liked"] or result["shared"] or result["error"]:
                log.info(
                    "抖音：点赞 %d、分享 %d、跳过 %d%s",
                    result["liked"], result["shared"], result["skipped"],
                    f"（{result['error']}）" if result["error"] else "",
                )
        except Exception:  # noqa: BLE001
            log.warning("抖音定时任务失败", exc_info=True)
    browser.stop()

def pid_file(settings) -> Path:
    return Path(getattr(settings, "douyin_profile", "data/browser/douyin")) / "browser.pid"


def stop_browser(settings) -> bool:
    """按 pid 文件关掉上次拉起的浏览器（taskkill /T 连子进程）。"""
    path = pid_file(settings)
    try:
        pid = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return False
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=15)
        killed = True
    except Exception:  # noqa: BLE001
        killed = False
    try:
        path.unlink()
    except OSError:
        pass
    return killed


def open_for_login(settings) -> str:
    """拉起一个看得见的窗口让主人扫码，返回说明。"""
    stop_browser(settings)  # 先关掉后台那个隐身窗口，免得连到它
    browser = Browser(settings)
    page = browser.start(visible=True)
    page.goto(getattr(settings, "douyin_home_url", HOME) or HOME, timeout=40)
    return (
        "浏览器开好了，在服务器上找到那个 Edge 窗口扫码登录；登录完回来发 /抖音 就行。"
        f"（profile：{Path(getattr(settings, 'douyin_profile', 'data/browser/douyin'))}）"
    )
