"""OneBot 11 适配层。

传输方式选择：**HTTP 上报**（协议端主动 POST 事件到本程序），
而不是 WebSocket 长连接——标准库即可实现，无需第三方依赖。

协议端（NapCat / LLOneBot）需要配置：
  上报地址: http://127.0.0.1:8080/event
  API 地址:  http://127.0.0.1:3001
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class IncomingMessage:
    """适配层之上的统一消息形态，引擎不感知 QQ 细节。"""

    user_id: int
    message_id: str
    text: str
    raw: dict[str, Any]


class Sender(Protocol):
    def send_private(self, user_id: int, text: str) -> bool: ...


def extract_text(message: Any) -> str:
    """从 OneBot 消息段中取出文本。

    M1 只关心 text 段；image / face / mface 等在 M3 处理。
    """
    if isinstance(message, str):
        return message
    if not isinstance(message, list):
        return ""
    parts: list[str] = []
    for segment in message:
        if not isinstance(segment, dict):
            continue
        if segment.get("type") != "text":
            continue
        data = segment.get("data") or {}
        parts.append(str(data.get("text", "")))
    return "".join(parts)


def parse_event(payload: Any) -> IncomingMessage | None:
    """把 OneBot 事件转成 IncomingMessage；非私聊文本消息返回 None。"""
    if not isinstance(payload, dict):
        return None
    if payload.get("post_type") != "message":
        return None
    if payload.get("message_type") != "private":
        return None

    try:
        user_id = int(payload.get("user_id"))
    except (TypeError, ValueError):
        return None

    text = extract_text(payload.get("message")).strip()
    if not text:
        return None  # 纯图片/表情消息在 M1 不处理

    return IncomingMessage(
        user_id=user_id,
        message_id=str(payload.get("message_id") or ""),
        text=text,
        raw=payload,
    )


class HttpSender:
    """通过 OneBot HTTP API 发消息。"""

    def __init__(self, api_url: str, token: str = "", timeout: float = 10.0) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def send_private(self, user_id: int, text: str) -> bool:
        payload = {"user_id": user_id, "message": text}
        return self._call("send_private_msg", payload)

    def _call(self, action: str, payload: dict) -> bool:
        url = f"{self.api_url}/{action}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError):
            return False
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return False
        # OneBot 约定：status == "ok" 或 retcode == 0
        if isinstance(data, dict):
            return data.get("status") == "ok" or data.get("retcode") == 0
        return False
