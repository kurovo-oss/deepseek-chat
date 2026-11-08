"""首次启动引导：模型连通性 + NapCat 登录状态。

这一层只做本机检查，不碰 QQ 协议细节：
- 模型：复用 LLMClient.probe()，只读测试，不写 .env；
- 协议端：复用 HttpSender.login_info()，离线/已登录一眼能看出来；
- NapCat：读它自己的 webui.json 端口和 token，登录页交给默认浏览器；
- 完成标记放在 data/onboarding.done，之后不再自动弹出。
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import time
import urllib.parse
import webbrowser
from pathlib import Path

from .llm import LLMClient

log = logging.getLogger("qqbot.onboarding")

MARKER_NAME = "onboarding.done"
DEFAULT_WEBUI_PORT = 6099


def _marker_path(settings) -> Path:
    return Path(settings.base_dir) / "data" / MARKER_NAME


def is_finished(settings) -> bool:
    return _marker_path(settings).is_file()


def finish(settings) -> dict:
    path = _marker_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"finished_at": int(time.time())}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return {"ok": True, **payload}


def reset(settings) -> dict:
    """测试和“重新引导”用；正常页面不暴露。"""
    try:
        _marker_path(settings).unlink()
    except FileNotFoundError:
        pass
    return {"ok": True}


def key_hint(key: str) -> str:
    if not key:
        return ""
    if len(key) > 8:
        return key[:3] + "…" + key[-4:]
    return "已设置"


def model_state(settings) -> dict:
    key = str(getattr(settings, "model_api_key", "") or "")
    base = str(getattr(settings, "model_base_url", "") or "")
    model = str(getattr(settings, "model_id", "") or "")
    master = str(getattr(settings, "allow_user_id", "") or "")
    configured = bool(key and base and model)
    return {
        "configured": configured,
        "master_qq": master,
        "master_set": bool(master and master.isdigit()),
        "base_url": base,
        "model_id": model,
        "key_set": bool(key),
        "key_hint": key_hint(key),
    }


def _clean_base_url(value: str) -> str:
    text = (value or "").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("模型地址必须是 http(s) 地址")
    return text


def test_model(
    settings,
    base_url: str | None = None,
    model_id: str | None = None,
    api_key: str | None = None,
    timeout: float = 15.0,
) -> dict:
    """用当前或页面传入的配置真实调一次模型，不写 .env。"""
    base_value = base_url if base_url is not None else str(getattr(settings, "model_base_url", "") or "")
    model_value = model_id if model_id is not None else str(getattr(settings, "model_id", "") or "")
    key_value = api_key if api_key is not None else str(getattr(settings, "model_api_key", "") or "")
    base = _clean_base_url(base_value)
    model = model_value.strip()
    key = key_value.strip()
    if not model:
        raise ValueError("模型名称不能为空")
    if not key:
        raise ValueError("API Key 还没有填写")
    started = time.monotonic()
    client = LLMClient(base, key, model, timeout=max(3.0, min(float(timeout), 60.0)), retries=0)
    ok, detail = client.probe()
    return {
        "ok": bool(ok),
        "detail": str(detail)[:300],
        "latency_ms": int((time.monotonic() - started) * 1000),
        "base_url": base,
        "model_id": model,
    }


def _napcat_root(settings) -> Path | None:
    root = Path(settings.base_dir) / "tools"
    if not root.is_dir():
        return None
    candidates = sorted(
        (path for path in root.glob("NapCat-*") if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _webui_config(settings) -> tuple[Path | None, dict]:
    napcat = _napcat_root(settings)
    if napcat is None:
        return None, {}
    for path in (
        napcat / "napcat" / "config" / "webui.json",
        napcat / "config" / "webui.json",
    ):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return path, data
    return napcat, {}


def napcat_state(settings) -> dict:
    napcat, config = _webui_config(settings)
    port = config.get("port") or DEFAULT_WEBUI_PORT
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = DEFAULT_WEBUI_PORT
    host = str(config.get("host") or "")
    if host not in ("127.0.0.1", "localhost", "::1"):
        host = "127.0.0.1"
    token = str(config.get("token") or "")
    running = False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            running = True
    except OSError:
        running = False
    return {
        "found": napcat is not None,
        "running": running,
        "login_page_available": bool(token),
        "port": port,
        "host": host,
    }


def adapter_state(settings, sender=None) -> dict:
    info = sender.login_info() if sender is not None else None
    if info is None:
        return {"online": False, "nickname": "", "user_id": "", "checked": True}
    return {
        "online": True,
        "nickname": str(info.get("nickname") or ""),
        "user_id": str(info.get("user_id") or ""),
        "checked": True,
    }


def build_state(settings, sender=None) -> dict:
    model = model_state(settings)
    adapter = adapter_state(settings, sender)
    napcat = napcat_state(settings)
    finished = is_finished(settings)
    # 已经能聊天就不再打扰；没配好模型、主人 QQ 或 QQ 没登录时才需要引导。
    needed = not finished and (not model["configured"] or not model["master_set"] or not adapter["online"])
    return {
        "needed": bool(needed),
        "finished": finished,
        "model": model,
        "adapter": adapter,
        "napcat": napcat,
    }


def napcat_login_url(settings) -> str:
    _path, config = _webui_config(settings)
    port = config.get("port") or DEFAULT_WEBUI_PORT
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = DEFAULT_WEBUI_PORT
    host = str(config.get("host") or "")
    if host not in ("127.0.0.1", "localhost", "::1"):
        host = "127.0.0.1"
    token = str(config.get("token") or "")
    if not token:
        raise ValueError("NapCat 还没生成登录 token，请先启动 NapCat")
    query = urllib.parse.urlencode({"token": token})
    return f"http://{host}:{port}/webui?{query}"


def open_napcat_login(settings) -> dict:
    url = napcat_login_url(settings)
    opened = bool(webbrowser.open(url, new=2))
    return {"ok": opened, "message": "已打开浏览器登录页" if opened else "浏览器没有打开登录页"}


def start_napcat(settings) -> dict:
    if napcat_state(settings)["running"]:
        return {"ok": True, "started": False, "message": "NapCat 已经在运行"}
    napcat = _napcat_root(settings)
    if napcat is None:
        raise ValueError("没有找到 tools\\NapCat-*")
    launcher = napcat / "launcher.bat"
    if not launcher.is_file():
        launcher = napcat / "napcat" / "launcher.bat"
    if not launcher.is_file():
        raise ValueError("没有找到 NapCat launcher.bat")
    if os.name != "nt":
        raise ValueError("只有 Windows 支持自动启动 NapCat")

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        ["cmd.exe", "/c", str(launcher)],
        cwd=str(launcher.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    log.info("首次引导：已尝试启动 NapCat（%s）", launcher.parent.name)
    return {"ok": True, "started": True, "message": "NapCat 正在启动，稍等几秒"}
