"""本地控制台：只监听 127.0.0.1，给主人看状态、改人格、改配置。

设计约束：
- 同进程线程：能直接读 engine.mood / guard，暂停开关立即生效；
- 标准库实现：不引 Web 框架，静态文件从 qqbot/ui/ 读；
- 密钥只写不读：GET 只返回 has_key / 尾号；
- 所有写操作要求同源 + 登录 Cookie，配置改动先备份 .env。
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import socket
import sys
import threading
import time
import urllib.parse
from collections import Counter, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config as config_mod
from . import onboarding as onboarding_mod
from . import persona_templates
from . import proactive_mute
from .mood import mood_line
from .runtime import MODULES, Runtime

log = logging.getLogger("qqbot.webui")

SESSION_COOKIE = "dafeiyu_ui"
MAX_BODY = 200_000
UI_DIR = Path(__file__).with_name("ui")
PERSONA_MIN_CHARS = 200
PERSONA_MAX_CHARS = 40_000

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

# 只允许改这些，不开放任意键
SETTING_KEYS = {
    "master_qq": "ALLOW_USER_ID",
    "model_base_url": "MODEL_BASE_URL",
    "model_id": "MODEL_ID",
    "model_api_key": "MODEL_API_KEY",
    "request_timeout": "REQUEST_TIMEOUT",
    "proactive_enabled": "PROACTIVE_ENABLED",
    "proactive_max_per_day": "PROACTIVE_MAX_PER_DAY",
    "proactive_quiet_start_hour": "PROACTIVE_QUIET_START_HOUR",
    "proactive_quiet_end_hour": "PROACTIVE_QUIET_END_HOUR",
    "qzone_enabled": "QZONE_ENABLED",
    "qzone_dry_run": "QZONE_DRY_RUN",
    "qzone_daily_max": "QZONE_DAILY_MAX",
    "douyin_enabled": "DOUYIN_ENABLED",
    "douyin_dry_run": "DOUYIN_DRY_RUN",
    "douyin_daily_max": "DOUYIN_DAILY_MAX",
    "douyin_share_daily_max": "DOUYIN_SHARE_DAILY_MAX",
}

_SECRET_FIELDS = {"model_api_key"}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _env_quote(value: object) -> str:
    text = str(value)
    if text == "":
        return '""'
    if re.search(r"[\s#\"']", text):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def _write_env(path: Path, updates: dict[str, str]) -> None:
    """保留注释和顺序，只替换指定键。"""
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = old.splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        match = re.match(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*)$", line)
        if match and match.group(2) in updates:
            key = match.group(2)
            out.append(f"{match.group(1)}{key}={_env_quote(updates[key])}")
            seen.add(key)
        else:
            out.append(line)
    if seen != set(updates):
        if out and out[-1].strip():
            out.append("")
        for key, value in updates.items():
            if key not in seen:
                out.append(f"{key}={_env_quote(value)}")
    backup = path.with_name(path.name + ".bak")
    if path.exists():
        shutil.copy2(path, backup)
    _atomic_write(path, "\n".join(out) + "\n")


def _today_start(now: int) -> int:
    lt = time.localtime(now)
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))


def _persona_paths(settings) -> tuple[Path, Path, Path]:
    base = Path(settings.base_dir)
    return (
        Path(settings.system_prompt_file),
        base / "data" / "persona" / "versions",
        base / "data" / "persona" / "versions.json",
    )


def read_persona(settings) -> dict:
    path, versions_dir, index_path = _persona_paths(settings)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    versions = []
    try:
        entries = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        entries = []
    for item in entries[:30]:
        if isinstance(item, dict) and item.get("file"):
            versions.append(
                {
                    "file": item["file"],
                    "at": item.get("at", 0),
                    "chars": item.get("chars", 0),
                    "note": item.get("note", ""),
                }
            )
    return {
        "text": text,
        "chars": len(text),
        "tokens": max(1, int(len(text) / 1.7)),
        "updated_at": int(path.stat().st_mtime) if path.exists() else 0,
        "versions": versions,
        "warnings": persona_warnings(text),
    }


def persona_warnings(text: str) -> list[str]:
    warnings = []
    checks = (
        (("不编", "不要编", "别编", "禁止编"), "缺少“不编造”边界"),
        (("线下",), "缺少“不线下”边界"),
        (("做不到",), "缺少能力边界"),
    )
    for words, label in checks:
        if not any(word in text for word in words):
            warnings.append(label)
    return warnings


def save_persona(settings, engine, text: str, note: str = "") -> dict:
    text = (text or "").strip()
    if len(text) < PERSONA_MIN_CHARS:
        raise ValueError(f"人格太短（至少 {PERSONA_MIN_CHARS} 字）")
    if len(text) > PERSONA_MAX_CHARS:
        raise ValueError(f"人格太长（最多 {PERSONA_MAX_CHARS} 字）")
    missing = persona_warnings(text)
    if missing:
        raise ValueError("缺少安全边界：要写清“不编造 / 不线下 / 能力边界”三条，不能只写一条")
    path, versions_dir, index_path = _persona_paths(settings)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    version = f"{stamp}-{int(time.time() * 1000) % 1000:03d}"
    versions_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(versions_dir / f"{version}.md", text)
    try:
        entries = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        entries = []
    entries.insert(
        0,
        {
            "file": f"{version}.md",
            "at": int(time.time()),
            "chars": len(text),
            "note": (note or "")[:120],
        },
    )
    _atomic_write(index_path, json.dumps(entries[:50], ensure_ascii=False, indent=2))
    _atomic_write(path, text)
    engine.system_prompt = text
    return {"ok": True, "version": version, "chars": len(text), "warnings": persona_warnings(text)}


def settings_payload(settings) -> dict:
    env_path = Path(settings.base_dir) / ".env"
    has_key = bool(settings.model_api_key)
    hint = ""
    if has_key:
        key = settings.model_api_key
        hint = key[:3] + "…" + key[-4:] if len(key) > 8 else "已设置"
    return {
        "master_qq": str(settings.allow_user_id),
        "model": {
            "base_url": settings.model_base_url,
            "id": settings.model_id,
            "api_key_set": has_key,
            "api_key_hint": hint,
            "timeout": settings.request_timeout,
        },
        "behavior": {
            "proactive_enabled": bool(settings.proactive_enabled),
            "proactive_max_per_day": settings.proactive_max_per_day,
            "proactive_quiet_start_hour": settings.proactive_quiet_start_hour,
            "proactive_quiet_end_hour": settings.proactive_quiet_end_hour,
            "qzone_enabled": bool(settings.qzone_enabled),
            "qzone_dry_run": bool(settings.qzone_dry_run),
            "qzone_daily_max": settings.qzone_daily_max,
            "douyin_enabled": bool(settings.douyin_enabled),
            "douyin_dry_run": bool(settings.douyin_dry_run),
            "douyin_daily_max": settings.douyin_daily_max,
            "douyin_share_daily_max": settings.douyin_share_daily_max,
        },
        "env_path": str(env_path),
        "env_exists": env_path.exists(),
    }


def _as_bool(value, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{name} 必须是布尔值")


def _as_int(value, name: str, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是整数") from exc
    if not low <= number <= high:
        raise ValueError(f"{name} 必须在 {low}~{high} 之间")
    return number


def _as_text(value, name: str, max_len: int = 300) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须是字符串")
    text = value.strip()
    if not text or len(text) > max_len:
        raise ValueError(f"{name} 不能为空，且不超过 {max_len} 字")
    return text


def save_settings(settings, payload: dict) -> dict:
    updates: dict[str, str] = {}
    for field, raw in payload.items():
        if field not in SETTING_KEYS:
            continue
        if field == "master_qq":
            text = _as_text(raw, "主人 QQ", 20)
            if not text.isdigit() or not 5 <= len(text) <= 12:
                raise ValueError("主人 QQ 必须是 5~12 位数字")
            updates["ALLOW_USER_ID"] = text
        elif field == "model_base_url":
            text = _as_text(raw, "Base URL", 500).rstrip("/")
            parsed = urllib.parse.urlsplit(text)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError("Base URL 必须是 http(s) 地址")
            updates["MODEL_BASE_URL"] = text
        elif field == "model_id":
            text = _as_text(raw, "Model ID", 100)
            if not re.fullmatch(r"[A-Za-z0-9._:/-]+", text):
                raise ValueError("Model ID 只能包含字母、数字和 . _ : / -")
            updates["MODEL_ID"] = text
        elif field == "model_api_key":
            if raw in ("", None):
                continue
            text = _as_text(raw, "API Key", 300)
            updates["MODEL_API_KEY"] = text
        elif field == "request_timeout":
            updates["REQUEST_TIMEOUT"] = str(_as_int(raw, "超时", 5, 300))
        elif field in ("proactive_enabled", "qzone_enabled", "qzone_dry_run", "douyin_enabled", "douyin_dry_run"):
            updates[SETTING_KEYS[field]] = "true" if _as_bool(raw, field) else "false"
        elif field == "proactive_max_per_day":
            updates["PROACTIVE_MAX_PER_DAY"] = str(_as_int(raw, "主动消息上限", 0, 50))
        elif field == "proactive_quiet_start_hour":
            updates["PROACTIVE_QUIET_START_HOUR"] = str(_as_int(raw, "免打扰开始时间", 0, 23))
        elif field == "proactive_quiet_end_hour":
            updates["PROACTIVE_QUIET_END_HOUR"] = str(_as_int(raw, "免打扰结束时间", 0, 23))
        elif field == "qzone_daily_max":
            updates["QZONE_DAILY_MAX"] = str(_as_int(raw, "空间上限", 0, 20))
        elif field == "douyin_daily_max":
            updates["DOUYIN_DAILY_MAX"] = str(_as_int(raw, "抖音点赞上限", 0, 100))
        elif field == "douyin_share_daily_max":
            updates["DOUYIN_SHARE_DAILY_MAX"] = str(_as_int(raw, "抖音分享上限", 0, 50))
    if not updates:
        raise ValueError("没有可保存的字段")
    quiet_start = int(updates.get("PROACTIVE_QUIET_START_HOUR", getattr(settings, "proactive_quiet_start_hour", 23)))
    quiet_end = int(updates.get("PROACTIVE_QUIET_END_HOUR", getattr(settings, "proactive_quiet_end_hour", 8)))
    if quiet_start == quiet_end:
        raise ValueError("免打扰开始和结束不能是同一个小时")
    env_path = Path(settings.base_dir) / ".env"
    _write_env(env_path, updates)
    return {"ok": True, "saved": sorted(updates), "restart_required": True}


def tail_log(settings, limit: int = 200) -> list[str]:
    path = Path(settings.log_dir) / "bot.log"
    if not path.exists():
        path = Path(settings.base_dir) / "logs" / "bot.log"
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return list(deque(handle, maxlen=max(1, min(limit, 5000))))
    except OSError:
        return []


def _check_port(host: str, port: int, timeout: float = 0.4) -> dict:
    host = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return {"host": host, "port": int(port), "ok": True, "detail": "可以连接"}
    except OSError as exc:
        return {"host": host, "port": int(port), "ok": False, "detail": str(exc)[:120] or "连接失败"}


def _url_port(url: str, default: int) -> tuple[str, int]:
    try:
        parsed = urllib.parse.urlsplit(url)
        return parsed.hostname or "127.0.0.1", int(parsed.port or default)
    except (ValueError, AttributeError):
        return "127.0.0.1", default


def diagnostics_payload(settings) -> dict:
    adapter_host, adapter_port = _url_port(getattr(settings, "adapter_api_url", ""), 3001)
    napcat = onboarding_mod.napcat_state(settings)
    ports = [
        {"name": "控制台", **_check_port(getattr(settings, "ui_host", "127.0.0.1"), int(settings.ui_port))},
        {"name": "事件上报", **_check_port(getattr(settings, "listen_host", "127.0.0.1"), int(settings.listen_port))},
        {"name": "OneBot API", **_check_port(adapter_host, adapter_port)},
        {"name": "NapCat WebUI", **_check_port("127.0.0.1", int(napcat.get("port") or 6099))},
    ]
    log_path = Path(settings.log_dir) / "bot.log"
    if not log_path.exists():
        log_path = Path(settings.base_dir) / "logs" / "bot.log"
    return {
        "ports": ports,
        "model": {
            "configured": bool(settings.model_api_key and settings.model_base_url and settings.model_id),
            "base_url": settings.model_base_url,
            "id": settings.model_id,
            "key_hint": onboarding_mod.key_hint(settings.model_api_key),
        },
        "log": {
            "path": str(log_path),
            "exists": log_path.exists(),
            "bytes": log_path.stat().st_size if log_path.exists() else 0,
            "updated_at": int(log_path.stat().st_mtime) if log_path.exists() else 0,
        },
        "data_dir": str(settings.base_dir),
        "python": sys.version.split()[0],
    }


def export_diagnostics(settings, guard=None) -> dict:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    lines = [
        "大肥鱼诊断包",
        f"导出时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}",
        f"数据目录：{settings.base_dir}",
        f"模型：{settings.model_id} / {settings.model_base_url}",
        f"API Key：{onboarding_mod.key_hint(settings.model_api_key) or '未设置'}",
        "",
        "## 进程日志（最后 2000 行）",
        "",
    ]
    lines.extend(line.rstrip("\n") for line in tail_log(settings, 2000))
    lines.extend(["", "## 审计（最近 200 条）", ""])
    if guard is not None and hasattr(guard, "recent"):
        try:
            for item in guard.recent(200):
                lines.append(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(item.get('at') or 0)))} "
                    f"{item.get('module', '')}/{item.get('action', '')} "
                    f"target={item.get('target', '')} result={item.get('result', '')} "
                    f"{item.get('error', '')}"
                )
        except Exception:  # noqa: BLE001
            lines.append("（审计读取失败）")
    else:
        lines.append("（没有审计记录）")
    content = "\n".join(lines) + "\n"
    return {
        "filename": f"dafeiyu-diagnostics-{stamp}.txt",
        "content": content,
        "bytes": len(content.encode("utf-8")),
    }


def build_status(engine, runtime: Runtime | None = None, now: int | None = None, adapter: dict | None = None) -> dict:
    stamp = int(now if now is not None else time.time())
    settings = engine.settings
    store = engine.store
    start = _today_start(stamp)
    try:
        rows = store.messages_between(start, stamp + 1, limit=3000)
    except Exception:  # noqa: BLE001 - 控制台不能因为统计失败整页挂
        rows = []
    roles = Counter(row.get("role") for row in rows)
    origins = Counter(row.get("origin") for row in rows)
    try:
        proactive_today = store.count_proactive_since(start)
    except Exception:  # noqa: BLE001
        proactive_today = 0
    session = str(settings.allow_user_id)
    try:
        last_bot_at = store.last_message_time(session, "assistant")
    except Exception:  # noqa: BLE001
        last_bot_at = None
    try:
        last_user_at = store.last_message_time(session, "user")
    except Exception:  # noqa: BLE001
        last_user_at = None

    if adapter is None:
        adapter = {"online": False, "nickname": "", "user_id": "", "checked": False}

    mood_values: dict = {}
    mood_text = ""
    if getattr(engine, "mood", None) is not None:
        try:
            mood_values = engine.mood.snapshot(stamp)
            mood_text = mood_line(mood_values)
        except Exception:  # noqa: BLE001
            mood_values = {}
    guard_status: dict = {}
    if getattr(engine, "guard", None) is not None:
        try:
            guard_status = engine.guard.status(stamp)
        except Exception:  # noqa: BLE001
            guard_status = {}
    recent = []
    try:
        recent = store.recent_messages(str(settings.allow_user_id), 8)
    except Exception:  # noqa: BLE001
        recent = []
    persona = read_persona(settings)

    alerts = []
    qzone = guard_status.get("qzone", {})
    if qzone.get("circuit_open"):
        alerts.append({"level": "bad", "title": "空间已熔断", "detail": "连续失败，30 分钟后重试"})
    if qzone and qzone.get("enabled") and qzone.get("used_today", 0) >= (qzone.get("daily_quota") or 0):
        alerts.append({"level": "warn", "title": "空间今日额度已用完", "detail": f"{qzone.get('used_today')} / {qzone.get('daily_quota')}"})
    douyin = guard_status.get("douyin", {})
    if douyin.get("circuit_open"):
        alerts.append({"level": "bad", "title": "抖音已熔断", "detail": "连续失败，30 分钟后重试"})
    if not persona["text"]:
        alerts.append({"level": "bad", "title": "人格文件为空", "detail": "prompts/system.md 读不到"})
    if runtime is not None and runtime.emergency:
        alerts.append({"level": "bad", "title": "控制台已停机", "detail": "回复和所有主动动作都停了"})

    return {
        "now": stamp,
        "pid": os.getpid(),
        "uptime": max(0, int(time.time() - PROCESS_STARTED)),
        "master_qq": str(settings.allow_user_id),
        "model": settings.model_id,
        "adapter": adapter,
        "last": {
            "bot_at": last_bot_at,
            "user_at": last_user_at,
        },
        "mood": {
            "values": mood_values,
            "line": mood_text,
        },
        "today": {
            "messages": len(rows),
            "user": roles.get("user", 0),
            "bot": roles.get("assistant", 0),
            "proactive": proactive_today,
            "proactive_max": settings.proactive_max_per_day,
            "qzone": origins.get("qzone", 0),
            "douyin": origins.get("douyin", 0) + origins.get("douyin_share", 0),
        },
        "guard": guard_status,
        "proactive": {
            "enabled": bool(settings.proactive_enabled),
            "max_per_day": settings.proactive_max_per_day,
            "quiet_start_hour": settings.proactive_quiet_start_hour,
            "quiet_end_hour": settings.proactive_quiet_end_hour,
            "muted_today": bool(runtime.proactive_muted()) if runtime is not None else False,
            "muted_until": runtime.proactive_mute_until() if runtime is not None else 0,
        },
        "runtime": runtime.snapshot() if runtime is not None else {"emergency": False, "paused": {}},
        "persona": {
            "chars": persona["chars"],
            "tokens": persona["tokens"],
            "updated_at": persona["updated_at"],
            "warnings": persona["warnings"],
        },
        "messages": [_message_row(row) for row in recent],
        "alerts": alerts,
    }


def _message_row(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "role": row.get("role"),
        "content": row.get("content") or "",
        "origin": row.get("origin") or "reply",
        "created_at": row.get("created_at") or 0,
        "send_state": row.get("send_state") or "",
    }


def _card_row(card: dict) -> dict:
    return {
        "id": card.get("id"),
        "kind": card.get("kind") or "fact",
        "summary": card.get("summary") or "",
        "detail": card.get("detail") or "",
        "importance": float(card.get("importance") or 0),
        "confirmed": bool(card.get("confirmed", True)),
        "pinned": bool(card.get("pinned")),
        "hit_count": int(card.get("hit_count") or 0),
        "created_at": int(card.get("created_at") or 0),
        "updated_at": int(card.get("updated_at") or 0),
    }


PROCESS_STARTED = time.time()


class WebUI:
    def __init__(self, engine, settings, runtime: Runtime | None = None) -> None:
        self.engine = engine
        self.settings = settings
        self.runtime = runtime
        self.base = Path(settings.base_dir)
        self.token = self._load_token()
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self._adapter_lock = threading.Lock()
        self._adapter_cache: dict = {}
        self._adapter_checked_at = 0.0

    def _load_token(self) -> str:
        path = self.base / "data" / "ui-token"
        try:
            token = path.read_text(encoding="ascii").strip()
        except OSError:
            token = ""
        if len(token) < 24:
            token = secrets.token_urlsafe(24)
            _atomic_write(path, token + "\n")
        return token

    def start(self):
        host = getattr(self.settings, "ui_host", "127.0.0.1")
        port = int(getattr(self.settings, "ui_port", 8090))
        try:
            self.httpd = ThreadingHTTPServer((host, port), make_handler(self))
        except OSError as exc:
            log.warning("控制台没起来（%s:%s）：%s", host, port, exc)
            return None
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="webui", daemon=True)
        self.thread.start()
        log.info("控制台：http://%s:%s/?token=%s", host, port, self.token)
        return self

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    # ---------------- API ----------------

    def _audit(self, action: str, target: str = "", result: str = "ok", error: str = "") -> None:
        guard = getattr(self.engine, "guard", None)
        if guard is None or not hasattr(guard, "record"):
            return
        try:
            guard.record("ui", action, target=target, result=result, error=error)
        except Exception:  # noqa: BLE001 - 审计失败不能影响页面动作
            log.info("控制台审计写入失败", exc_info=True)

    def _adapter_status(self) -> dict:
        """查一次 QQ 协议端登录态，缓存 5 秒，避免总览轮询把协议端打满。"""
        now = time.monotonic()
        with self._adapter_lock:
            if self._adapter_cache and now - self._adapter_checked_at < 5.0:
                return dict(self._adapter_cache)
            sender = getattr(self.engine, "sender", None)
            try:
                api_url = getattr(sender, "api_url", "") if sender is not None else ""
                if api_url:
                    host, port = _url_port(str(api_url), 3001)
                    if not _check_port(host, port, timeout=0.25)["ok"]:
                        state = {"online": False, "nickname": "", "user_id": "", "checked": True}
                    else:
                        state = onboarding_mod.adapter_state(self.settings, sender)
                else:
                    state = onboarding_mod.adapter_state(self.settings, sender)
            except Exception:  # noqa: BLE001 - 状态查不到就显示离线，不挡页面
                log.info("QQ 状态检查失败", exc_info=True)
                state = {"online": False, "nickname": "", "user_id": "", "checked": True}
            self._adapter_cache = state
            self._adapter_checked_at = now
            return dict(state)

    def api_status(self) -> dict:
        return build_status(self.engine, self.runtime, adapter=self._adapter_status())


    def api_messages(self, limit: int = 50) -> dict:
        rows = self.engine.store.recent_messages(str(self.settings.allow_user_id), max(1, min(limit, 100)))
        return {"messages": [_message_row(row) for row in rows]}

    def api_memory(self, query: str = "", status: str = "active") -> dict:
        status = status if status in ("active", "archived") else "active"
        store = self.engine.store
        cards = [_card_row(card) for card in store.list_cards(status=status, limit=300)]
        needle = str(query or "").strip().casefold()
        if needle:
            cards = [
                card
                for card in cards
                if needle in card["summary"].casefold() or needle in card["detail"].casefold()
            ]
        cards.sort(key=lambda card: (not card["pinned"], -card["updated_at"], -card["id"]))
        return {
            "items": cards,
            "query": str(query or ""),
            "counts": {
                "active": store.count_cards(status="active"),
                "archived": store.count_cards(status="archived"),
            },
            "total": len(cards),
        }

    def api_memory_update(self, body: dict) -> dict:
        store = self.engine.store
        card_id = _as_int(body.get("id"), "记忆编号", 1, 10**9)
        card = store.get_card(card_id)
        if card is None:
            raise ValueError("这条记忆已经不在了")
        updates: dict = {}
        if "summary" in body:
            updates["summary"] = _as_text(body.get("summary"), "记忆内容", 500)
        if "detail" in body:
            detail = body.get("detail")
            if not isinstance(detail, str):
                raise ValueError("备注必须是字符串")
            if len(detail) > 2000:
                raise ValueError("备注最多 2000 字")
            updates["detail"] = detail.strip()
        if "importance" in body:
            try:
                importance = float(body.get("importance"))
            except (TypeError, ValueError) as exc:
                raise ValueError("重要度必须是数字") from exc
            if not 0.0 <= importance <= 1.0:
                raise ValueError("重要度必须在 0~1 之间")
            updates["importance"] = importance
        if updates:
            store.update_card(card_id, **updates)
        if "pinned" in body:
            store.set_pinned(card_id, _as_bool(body.get("pinned"), "pinned"))
        if "confirmed" in body:
            store.set_confirmed(card_id, _as_bool(body.get("confirmed"), "confirmed"))
        self._audit("edit_memory", target=str(card_id), result="ok")
        return {"ok": True, "item": _card_row(store.get_card(card_id) or {})}

    def api_memory_correct(self, body: dict) -> dict:
        """“你记错了”：用主人给的新说法替换旧卡片，不保留错误内容。"""
        store = self.engine.store
        card_id = _as_int(body.get("id"), "记忆编号", 1, 10**9)
        card = store.get_card(card_id)
        if card is None:
            raise ValueError("这条记忆已经不在了")
        summary = _as_text(body.get("summary"), "新的说法", 500)
        # 纠错时旧的备注也可能跟着错，一并清掉，避免下一条消息还带着旧说法。
        store.update_card(card_id, summary=summary, detail="")
        self._audit("correct_memory", target=str(card_id))
        return {"ok": True, "item": _card_row(store.get_card(card_id) or {})}

    def api_memory_delete(self, body: dict) -> dict:
        card_id = _as_int(body.get("id"), "记忆编号", 1, 10**9)
        if not self.engine.store.delete_card(card_id):
            raise ValueError("这条记忆已经不在了")
        self._audit("delete_memory", target=str(card_id))
        return {"ok": True}

    def api_memory_preference(self, body: dict) -> dict:
        """手动记一条相处偏好；这是用户明确给的，直接算确认。"""
        summary = _as_text(body.get("summary"), "偏好内容", 300)
        card_id = self.engine.store.add_card(
            kind="preference",
            summary=summary,
            detail="用户在面板手动添加的相处偏好",
            importance=0.8,
            confirmed=True,
        )
        self._audit("add_preference", target=str(card_id))
        return {"ok": True, "card_id": card_id}

    def api_feedback(self, body: dict) -> dict:
        """对话反馈：good 只记信号；bad 带备注时落一张偏好卡。"""
        store = self.engine.store
        message_id = _as_int(body.get("message_id"), "消息编号", 1, 10**9)
        message = store.get_message(message_id)
        if message is None:
            raise ValueError("这条消息已经不在了")
        if message.get("role") != "assistant":
            raise ValueError("只能对她说的话做反馈")
        if str(message.get("session_id") or "") != str(self.settings.allow_user_id):
            raise ValueError("这条消息不属于当前会话")
        rating = str(body.get("rating") or "").strip().lower()
        if rating not in ("good", "bad"):
            raise ValueError("反馈只能是 good 或 bad")
        note = body.get("note")
        if note is None:
            note = ""
        if not isinstance(note, str):
            raise ValueError("备注必须是字符串")
        note = note.strip()[:200]
        card_id = None
        if note:
            card_id = store.add_card(
                kind="preference",
                summary=note,
                detail=f"来自对话 #{message_id} 的反馈",
                importance=0.8,
                source_from=message_id,
                source_to=message_id,
                confirmed=True,
            )
        feedback_id = store.add_feedback(message_id, rating, note=note, card_id=card_id)
        from . import goals as goals_mod

        try:
            goals_mod.record_feedback(store, rating)
        except Exception:  # noqa: BLE001 - 目标进度是附加信息
            log.info("记录目标反馈结果失败", exc_info=True)
        self._audit("feedback", target=str(message_id), result=rating)
        return {"ok": True, "feedback_id": feedback_id, "card_id": card_id}

    def api_goals(self) -> dict:
        store = self.engine.store
        items = store.list_goals(limit=100)
        counts = {
            "candidate": store.count_goals(status="candidate"),
            "active": store.count_goals(status="active"),
            "paused": store.count_goals(status="paused"),
            "dropped": store.count_goals(status="dropped"),
        }
        actions = store.list_goal_actions(status="waiting_approval", limit=50)
        from . import autonomy as autonomy_mod

        breaker = autonomy_mod.breaker(store)
        recent_auto = [
            item
            for item in store.list_goal_actions(limit=200)
            if item.get("approval") in ("auto", "shadow")
            and item.get("status") in ("shadow", "blocked", "sending", "done", "failed")
        ][:20]
        return {
            "items": items,
            "actions": actions,
            "counts": counts,
            "autonomy_enabled": bool(getattr(self.settings, "autonomy_enabled", False)),
            "autonomy_mode": getattr(self.settings, "autonomy_mode", "confirm"),
            "autonomy": {
                "enabled": bool(getattr(self.settings, "autonomy_enabled", False)),
                "mode": getattr(self.settings, "autonomy_mode", "confirm"),
                "breaker": breaker,
                "paused": autonomy_mod.paused(store),
                "shadow_started": autonomy_mod.shadow_started(store),
                "shadow_ready": autonomy_mod.shadow_ready(store, self.settings, int(time.time())),
                "auto_actions": recent_auto,
                "policy_min_score": float(
                    getattr(self.settings, "autonomy_policy_min_score", 0.65)
                ),
            },
        }

    def api_autonomy_reset(self, body: dict) -> dict:
        from . import autonomy as autonomy_mod

        autonomy_mod.clear_breaker(self.engine.store)
        self._audit("reset_autonomy_breaker")
        return {"ok": True, "autonomy": {"breaker": {}}}

    def api_autonomy_pause(self, body: dict) -> dict:
        from . import autonomy as autonomy_mod

        value = bool(body.get("paused", True))
        autonomy_mod.set_paused(self.engine.store, value)
        self._audit("pause_autonomy" if value else "resume_autonomy")
        return {"ok": True, "paused": value}

    def api_goal_update(self, body: dict) -> dict:
        goal_id = _as_int(body.get("id"), "目标编号", 1, 10**9)
        goal = self.engine.store.get_goal(goal_id)
        if goal is None:
            raise ValueError("这个目标已经不在了")
        status = str(body.get("status") or "").strip().lower()
        allowed = {"candidate", "active", "paused", "done", "dropped"}
        if status and status not in allowed:
            raise ValueError("目标状态不正确")
        updates = {"status": status} if status else {}
        if status == "done":
            updates["progress"] = 1.0
        if "progress" in body:
            try:
                updates["progress"] = max(0.0, min(1.0, float(body["progress"])))
            except (TypeError, ValueError) as exc:
                raise ValueError("进度必须是 0~1 的数字") from exc
        if "autonomy" in body:
            autonomy = str(body["autonomy"]).strip().lower()
            if autonomy not in ("confirm", "auto"):
                raise ValueError("目标自主模式只能是“需确认”或“自动”")
            if autonomy == "auto" and not getattr(self.settings, "autonomy_enabled", False):
                raise ValueError("先打开 AUTONOMY_ENABLED，才能允许目标自动执行")
            updates["autonomy"] = autonomy
        if "parent_id" in body:
            parent_id = body.get("parent_id")
            if parent_id in (None, "", 0):
                updates["parent_id"] = None
            else:
                parent_id = _as_int(parent_id, "父目标编号", 1, 10**9)
                if parent_id == goal_id:
                    raise ValueError("目标不能成为自己的父目标")
                if self.engine.store.get_goal(parent_id) is None:
                    raise ValueError("父目标不存在")
                updates["parent_id"] = parent_id
        if "due_at" in body:
            try:
                updates["due_at"] = int(body["due_at"]) if body["due_at"] not in (None, "") else None
            except (TypeError, ValueError) as exc:
                raise ValueError("到期时间必须是时间戳") from exc
        self.engine.store.update_goal(goal_id, **updates)
        self._audit("update_goal", target=str(goal_id), result=status or "autonomy")
        return {"ok": True, "item": self.engine.store.get_goal(goal_id)}

    def _action_service(self):
        from .goal_actions import GoalActionService

        return GoalActionService(self.engine)

    def api_goal_tools(self) -> dict:
        from .goal_actions import GoalActionService

        return {"items": GoalActionService.available_tools()}

    def api_goal_note(self, body: dict) -> dict:
        goal_id = _as_int(body.get("id"), "目标编号", 1, 10**9)
        result = self._action_service().save_note(goal_id)
        self._audit("save_goal_note", target=str(goal_id))
        return result

    def api_goal_prepare(self, body: dict) -> dict:
        goal_id = _as_int(body.get("id"), "目标编号", 1, 10**9)
        action = self._action_service().prepare(goal_id)
        self._audit("prepare_goal_action", target=str(goal_id))
        return {"ok": True, "action": action}

    def api_goal_approve(self, body: dict) -> dict:
        action_id = _as_int(body.get("id"), "动作编号", 1, 10**9)
        action = self._action_service().approve(action_id)
        self._audit("approve_goal_action", target=str(action_id))
        return {"ok": True, "action": action}

    def api_goal_cancel(self, body: dict) -> dict:
        action_id = _as_int(body.get("id"), "动作编号", 1, 10**9)
        action = self._action_service().cancel(action_id)
        self._audit("cancel_goal_action", target=str(action_id))
        return {"ok": True, "action": action}

    def api_tasks(self) -> dict:
        store = self.engine.store
        items = store.list_tasks(limit=100)
        return {
            "items": items,
            "counts": {
                status: sum(1 for item in items if item.get("status") == status)
                for status in ("pending", "running", "done", "failed", "cancelled")
            },
        }

    def api_task_create(self, body: dict) -> dict:
        title = _as_text(body.get("title"), "任务内容", 300)
        description = body.get("description") or ""
        if not isinstance(description, str):
            raise ValueError("任务备注必须是字符串")
        try:
            minutes = int(body.get("due_minutes") or 15)
        except (TypeError, ValueError) as exc:
            raise ValueError("due_minutes 必须是整数") from exc
        if not 1 <= minutes <= 10080:
            raise ValueError("due_minutes 必须在 1~10080 之间")
        now = int(time.time())
        task = self.engine.store.add_task(
            title=title,
            description=description[:2000],
            kind="manual",
            due_at=now + minutes * 60,
        )
        self._audit("create_task", target=str(task))
        return {"ok": True, "item": self.engine.store.get_task(task)}

    def api_task_cancel(self, body: dict) -> dict:
        task_id = _as_int(body.get("id"), "任务编号", 1, 10**9)
        if self.engine.store.get_task(task_id) is None:
            raise ValueError("这个任务已经不在了")
        self.engine.store.update_task(task_id, status="cancelled", notify_status="cancelled")
        self._audit("cancel_task", target=str(task_id))
        return {"ok": True, "item": self.engine.store.get_task(task_id)}

    def api_style_suggestions(self) -> dict:
        return {
            "items": self.engine.store.list_style_suggestions(status="suggested", limit=20)
        }

    def api_style_review(self, body: dict) -> dict:
        from . import style_review as style_review_mod

        created = style_review_mod.review(
            self.engine.store, self.engine.llm, self.settings
        )
        self._audit("style_review", result=f"{len(created)} suggestions")
        return {"ok": True, "created": created}

    def api_style_apply(self, body: dict) -> dict:
        from . import style_review as style_review_mod

        suggestion_id = _as_int(body.get("id"), "建议编号", 1, 10**9)
        result = style_review_mod.apply(self.engine.store, suggestion_id)
        self._audit("apply_style", target=str(suggestion_id))
        return result

    def api_style_dismiss(self, body: dict) -> dict:
        from . import style_review as style_review_mod

        suggestion_id = _as_int(body.get("id"), "建议编号", 1, 10**9)
        result = style_review_mod.dismiss(self.engine.store, suggestion_id)
        self._audit("dismiss_style", target=str(suggestion_id))
        return result

    def api_goal_review(self, body: dict) -> dict:
        """手动让她想一次；只写候选目标，不执行任何动作。"""
        from . import goals as goals_mod

        created = goals_mod.review(
            self.engine.store, self.engine.llm, self.settings
        )
        self._audit("goal_review", result=f"{len(created)} candidates")
        return {"ok": True, "created": created, "items": self.engine.store.list_goals(limit=100)}

    def api_persona(self) -> dict:
        return read_persona(self.settings)

    def api_persona_templates(self) -> dict:
        return {"items": persona_templates.list_templates(self.settings)}

    def api_persona_preview(self, body: dict) -> dict:
        template = persona_templates.render(self.settings, str(body.get("template_id") or ""))
        template["warnings"] = persona_warnings(template["text"])
        return template

    def api_persona_apply(self, body: dict) -> dict:
        template = persona_templates.render(self.settings, str(body.get("template_id") or ""))
        result = save_persona(
            self.settings,
            self.engine,
            template["text"],
            note=f"模板：{template['name']}",
        )
        result["template"] = {"id": template["id"], "name": template["name"]}
        self._audit("apply_persona_template", target=template["id"], result="ok")
        return result

    def api_persona_restore(self) -> dict:
        text = persona_templates.default_text(self.settings)
        result = save_persona(self.settings, self.engine, text, note="恢复默认")
        self._audit("restore_persona", target="default", result="ok")
        return result

    def api_save_persona(self, body: dict) -> dict:
        result = save_persona(self.settings, self.engine, str(body.get("text", "")), str(body.get("note", "")))
        self._audit("publish_persona", target=str(result.get("version", "")), result="ok")
        return result

    def api_settings(self) -> dict:
        return settings_payload(self.settings)

    def api_save_settings(self, body: dict) -> dict:
        result = save_settings(self.settings, body)
        self._audit("save_settings", target=",".join(result.get("saved", [])), result="ok")
        return result

    def api_logs(self, limit: int = 200) -> dict:
        return {"lines": tail_log(self.settings, limit)}

    def api_audit(self, limit: int = 40) -> dict:
        guard = getattr(self.engine, "guard", None)
        rows = guard.recent(max(1, min(limit, 200))) if guard is not None else []
        return {"items": rows}

    def api_diagnostics(self) -> dict:
        return diagnostics_payload(self.settings)

    def api_export_diagnostics(self) -> dict:
        guard = getattr(self.engine, "guard", None)
        result = export_diagnostics(self.settings, guard)
        self._audit("export_diagnostics", target=result["filename"])
        return result

    def api_runtime(self, body: dict) -> dict:
        if self.runtime is None:
            raise ValueError("运行时控制未启用")
        if "emergency" in body:
            on = _as_bool(body["emergency"], "emergency")
            self.runtime.set_emergency(on)
            self._audit("emergency", target="on" if on else "off")
        elif "resume_all" in body and body["resume_all"]:
            self.runtime.resume_all()
            self._audit("resume_all")
        elif "module" in body:
            module = str(body["module"])
            if module not in MODULES:
                raise ValueError("未知模块")
            paused = _as_bool(body.get("paused"), "paused")
            self.runtime.set_paused(module, paused)
            self._audit("pause" if paused else "resume", target=module)
        else:
            raise ValueError("缺少动作")
        return self.runtime.snapshot()

    def api_proactive_mute(self, body: dict) -> dict:
        if self.runtime is None:
            raise ValueError("运行时控制未启用")
        muted = _as_bool(body.get("muted"), "muted")
        if muted:
            if not self.settings.proactive_enabled:
                raise ValueError("主动消息总开关是关闭的，先在配置里打开")
            payload = proactive_mute.set_today(self.settings)
            self.runtime.set_proactive_mute(int(payload["until"]))
            self._audit("mute_today", target="proactive")
        else:
            proactive_mute.clear(self.settings)
            self.runtime.set_proactive_mute(0)
            self._audit("unmute_today", target="proactive")
        return {"ok": True, "muted": muted, "runtime": self.runtime.snapshot()}


    def api_onboarding(self) -> dict:
        return onboarding_mod.build_state(self.settings, getattr(self.engine, "sender", None))

    def api_test_model(self, body: dict) -> dict:
        result = onboarding_mod.test_model(
            self.settings,
            base_url=body.get("base_url"),
            model_id=body.get("model_id"),
            api_key=body.get("model_api_key"),
        )
        self._audit("test_model", target=str(result.get("model_id", "")), result="ok" if result.get("ok") else "failed")
        return result

    def api_start_napcat(self) -> dict:
        result = onboarding_mod.start_napcat(self.settings)
        self._audit("start_napcat", target="local", result="ok" if result.get("ok") else "failed")
        return result

    def api_open_napcat(self) -> dict:
        result = onboarding_mod.open_napcat_login(self.settings)
        self._audit("open_napcat", target="local", result="ok" if result.get("ok") else "failed")
        return result

    def api_finish_onboarding(self) -> dict:
        result = onboarding_mod.finish(self.settings)
        self._audit("finish_onboarding")
        return result


def make_handler(app: WebUI):

    class Handler(BaseHTTPRequestHandler):
        server_version = "dafeiyu-ui/0.1"

        # ---------- auth ----------
        def _cookie_token(self) -> str:
            raw = self.headers.get("Cookie") or ""
            for part in raw.split(";"):
                name, _, value = part.strip().partition("=")
                if name == SESSION_COOKIE:
                    return value
            return ""

        def _query_token(self) -> str:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            return (query.get("token") or [""])[0]

        def _authed(self) -> bool:
            token = self._cookie_token() or self.headers.get("X-UI-Token") or ""
            return secrets.compare_digest(token, app.token)

        def _redirect_login(self) -> None:
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _check_auth(self) -> bool:
            if self._authed():
                return True
            query_token = self._query_token()
            if query_token and secrets.compare_digest(query_token, app.token):
                path = urllib.parse.urlsplit(self.path).path or "/"
                self.send_response(302)
                self.send_header("Location", path)
                self.send_header(
                    "Set-Cookie",
                    f"{SESSION_COOKIE}={app.token}; Path=/; HttpOnly; SameSite=Strict",
                )
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
            if self.path.split("?")[0] in ("/", "/index.html"):
                self._serve_login()
                return False
            self._json({"error": "unauthorized"}, 401)
            return False

        def _serve_login(self) -> None:
            body = LOGIN_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _same_origin(self) -> bool:
            # 原生 Windows 控制台用 X-UI-Token 证明身份，没有浏览器 Origin；
            # token 已经是最强的本地凭证，单独放行。
            header_token = self.headers.get("X-UI-Token") or ""
            if header_token and secrets.compare_digest(header_token, app.token):
                return True
            origin = self.headers.get("Origin") or ""
            host = self.headers.get("Host") or ""
            if origin:
                return origin == f"http://{host}"
            return (self.headers.get("Sec-Fetch-Site") or "") in ("same-origin", "none")

        # ---------- responses ----------
        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, exc: Exception, status: int = 400) -> None:
            self._json({"error": str(exc)[:300]}, status)

        def _read_json(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > MAX_BODY:
                raise ValueError("请求体太大")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("请求体不是 JSON") from exc
            if not isinstance(data, dict):
                raise ValueError("请求体必须是对象")
            return data

        def _serve_static(self) -> None:
            path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
            rel = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
            target = (UI_DIR / rel).resolve()
            try:
                target.relative_to(UI_DIR.resolve())
            except ValueError:
                self.send_error(404)
                return
            if not target.is_file():
                self.send_error(404)
                return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", MIME.get(target.suffix.lower(), "application/octet-stream"))
            self.send_header("Cache-Control", "no-cache" if target.suffix == ".html" else "public, max-age=300")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        # ---------- routes ----------
        def do_GET(self) -> None:  # noqa: N802
            if not self._check_auth():
                return
            path = urllib.parse.urlsplit(self.path).path
            try:
                if path == "/api/status":
                    self._json(app.api_status())
                elif path == "/api/messages":
                    limit = int((urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("limit") or ["50"])[0])
                    self._json(app.api_messages(limit))
                elif path == "/api/memory":
                    query = (urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("q") or [""])[0]
                    status = (urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("status") or ["active"])[0]
                    self._json(app.api_memory(query, status))
                elif path == "/api/persona":
                    self._json(app.api_persona())
                elif path == "/api/persona/templates":
                    self._json(app.api_persona_templates())
                elif path == "/api/settings":
                    self._json(app.api_settings())
                elif path == "/api/logs":
                    limit = int((urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("limit") or ["200"])[0])
                    self._json(app.api_logs(limit))
                elif path == "/api/audit":
                    self._json(app.api_audit())
                elif path == "/api/diagnostics":
                    self._json(app.api_diagnostics())
                elif path == "/api/diagnostics/export":
                    self._json(app.api_export_diagnostics())
                elif path == "/api/onboarding":
                    self._json(app.api_onboarding())
                elif path == "/api/goals":
                    self._json(app.api_goals())
                elif path == "/api/styles":
                    self._json(app.api_style_suggestions())
                elif path == "/api/tasks":
                    self._json(app.api_tasks())
                elif path == "/api/goals/tools":
                    self._json(app.api_goal_tools())
                elif path == "/health":
                    self._json({"ok": True})
                else:
                    self._serve_static()
            except Exception as exc:  # noqa: BLE001
                log.exception("控制台 GET 失败")
                self._error(exc, 500)

        def do_PUT(self) -> None:  # noqa: N802
            if not self._check_auth():
                return
            if not self._same_origin():
                self._error(ValueError("跨站请求被拒绝"), 403)
                return
            path = urllib.parse.urlsplit(self.path).path
            try:
                body = self._read_json()
                if path == "/api/persona":
                    self._json(app.api_save_persona(body))
                elif path == "/api/memory/update":
                    self._json(app.api_memory_update(body))
                elif path == "/api/memory/correct":
                    self._json(app.api_memory_correct(body))
                elif path == "/api/memory/delete":
                    self._json(app.api_memory_delete(body))
                elif path == "/api/memory/preference":
                    self._json(app.api_memory_preference(body))
                elif path == "/api/feedback":
                    self._json(app.api_feedback(body))
                elif path == "/api/goals/update":
                    self._json(app.api_goal_update(body))
                elif path == "/api/goals/autonomy/reset":
                    self._json(app.api_autonomy_reset(body))
                elif path == "/api/goals/autonomy/pause":
                    self._json(app.api_autonomy_pause(body))
                elif path == "/api/goals/review":
                    self._json(app.api_goal_review(body))
                elif path == "/api/goals/action/prepare":
                    self._json(app.api_goal_prepare(body))
                elif path == "/api/goals/note":
                    self._json(app.api_goal_note(body))
                elif path == "/api/goals/action/approve":
                    self._json(app.api_goal_approve(body))
                elif path == "/api/goals/action/cancel":
                    self._json(app.api_goal_cancel(body))
                elif path == "/api/styles/review":
                    self._json(app.api_style_review(body))
                elif path == "/api/tasks/create":
                    self._json(app.api_task_create(body))
                elif path == "/api/tasks/cancel":
                    self._json(app.api_task_cancel(body))
                elif path == "/api/styles/apply":
                    self._json(app.api_style_apply(body))
                elif path == "/api/styles/dismiss":
                    self._json(app.api_style_dismiss(body))
                elif path == "/api/persona/preview":
                    self._json(app.api_persona_preview(body))
                elif path == "/api/persona/apply":
                    self._json(app.api_persona_apply(body))
                elif path == "/api/persona/restore":
                    self._json(app.api_persona_restore())
                elif path == "/api/settings":
                    self._json(app.api_save_settings(body))
                elif path == "/api/runtime":
                    self._json(app.api_runtime(body))
                elif path == "/api/proactive/mute":
                    self._json(app.api_proactive_mute(body))
                elif path == "/api/onboarding/test-model":
                    self._json(app.api_test_model(body))
                elif path == "/api/onboarding/start-napcat":
                    self._json(app.api_start_napcat())
                elif path == "/api/onboarding/open-napcat":
                    self._json(app.api_open_napcat())
                elif path == "/api/onboarding/finish":
                    self._json(app.api_finish_onboarding())
                else:
                    self._json({"error": "not found"}, 404)
            except ValueError as exc:
                self._error(exc, 400)
            except Exception as exc:  # noqa: BLE001
                log.exception("控制台 PUT 失败")
                self._error(exc, 500)

        def do_POST(self) -> None:  # noqa: N802
            self.do_PUT()

        def log_message(self, fmt: str, *args) -> None:
            log.debug("%s - %s", self.address_string(), fmt % args)

    return Handler


LOGIN_HTML = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>大肥鱼 · 控制台</title>
<style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(900px 600px at 70% 0,rgba(90,215,255,.18),transparent 60%),#05111f;color:#eaf6ff;font:14px/1.6 "Microsoft YaHei UI",system-ui;font-family:"Microsoft YaHei UI",system-ui}
.box{width:min(380px,90vw);padding:28px;border:1px solid rgba(126,200,255,.2);border-radius:20px;background:rgba(12,36,58,.72);backdrop-filter:blur(18px);box-shadow:0 20px 60px rgba(0,0,0,.4)}
h1{margin:0 0 4px;font-size:20px}p{margin:0 0 18px;color:#8fb3cc;font-size:12px}
input{width:100%;box-sizing:border-box;height:42px;padding:0 12px;border-radius:11px;border:1px solid rgba(126,200,255,.2);background:rgba(3,14,24,.5);color:#eaf6ff;outline:none}
button{margin-top:12px;width:100%;height:42px;border:0;border-radius:11px;background:linear-gradient(135deg,#b9f0ff,#5ad7ff);color:#04202c;font-weight:700;cursor:pointer}
code{color:#5ad7ff;font-size:11px}
</style>
<div class="box"><h1>大肥鱼控制台</h1><p>输入启动日志里的访问 token，只在本机使用。</p>
<input id="t" placeholder="粘贴 token" autofocus><button onclick="location.href='/?token='+encodeURIComponent(document.getElementById('t').value.trim())">进入</button>
<p style="margin-top:14px">token 在 <code>data/ui-token</code>，启动日志也会打印一次。</p></div>
</html>"""


def start_webui(engine, settings, runtime: Runtime | None = None):
    if not getattr(settings, "ui_enabled", True):
        return None
    return WebUI(engine, settings, runtime).start()
