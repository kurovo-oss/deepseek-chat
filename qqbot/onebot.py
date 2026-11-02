"""OneBot 11 适配层。

传输方式选择：**HTTP 上报**（协议端主动 POST 事件到本程序），
而不是 WebSocket 长连接——标准库即可实现，无需第三方依赖。

协议端（NapCat / LLOneBot）需要配置：
  上报地址: http://127.0.0.1:8080/event
  API 地址:  http://127.0.0.1:3001
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class IncomingImage:
    """OneBot image 段。sub_type=1 表示表情包。"""

    url: str = ""
    file: str = ""
    sub_type: int = 0
    summary: str = ""


@dataclass(frozen=True)
class IncomingMessage:
    """适配层之上的统一消息形态，引擎不感知 QQ 细节。"""

    user_id: int
    message_id: str
    text: str
    raw: dict[str, Any]
    images: tuple[IncomingImage, ...] = ()
    reply_id: str = ""  # 引用回复：被引用那条的消息 id


class Sender(Protocol):
    def send_private(self, user_id: int, text: str) -> bool: ...

    def send_private_image(self, user_id: int, image: Path, mode: str = "path") -> bool: ...

    def get_message(self, message_id: str) -> str: ...


def extract_text(message: Any) -> str:
    """从 OneBot 消息段中取出文本。"""
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


def extract_reply_id(message: Any) -> str:
    """取出引用回复的 message_id；没有就返回空串。"""
    if not isinstance(message, list):
        return ""
    for segment in message:
        if not isinstance(segment, dict) or segment.get("type") != "reply":
            continue
        data = segment.get("data") or {}
        return str(data.get("id") or "")
    return ""


def extract_images(message: Any) -> tuple[IncomingImage, ...]:
    """取出消息里的图片段；非数组消息（CQ 字符串）不解析图片。"""
    if not isinstance(message, list):
        return ()
    found: list[IncomingImage] = []
    for segment in message:
        if not isinstance(segment, dict) or segment.get("type") != "image":
            continue
        data = segment.get("data") or {}
        try:
            sub_type = int(data.get("sub_type") or 0)
        except (TypeError, ValueError):
            sub_type = 0
        found.append(
            IncomingImage(
                url=str(data.get("url") or ""),
                file=str(data.get("file") or ""),
                sub_type=sub_type,
                summary=str(data.get("summary") or ""),
            )
        )
    return tuple(found)


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
    images = extract_images(payload.get("message"))
    reply_id = extract_reply_id(payload.get("message"))
    if not text and not images and not reply_id:
        return None

    return IncomingMessage(
        user_id=user_id,
        message_id=str(payload.get("message_id") or ""),
        text=text,
        raw=payload,
        images=images,
        reply_id=reply_id,
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

    def send_private_image(self, user_id: int, image: Path | bytes, mode: str = "path") -> bool:
        """发图。mode=path 用 file:// URL（协议端与程序同机可用），
        mode=base64 走 base64://（协议端在容器里、看不到宿主路径时用）。"""
        if mode == "base64":
            data = image if isinstance(image, bytes) else Path(image).read_bytes()
            encoded = base64.b64encode(data).decode("ascii")
            cq = f"[CQ:image,file=base64://{encoded}]"
        else:
            cq = f"[CQ:image,file={Path(image).as_uri()}]"
        return self._call("send_private_msg", {"user_id": user_id, "message": cq})

    def get_message(self, message_id: str) -> str:
        """取一条历史消息的文本（引用回复要用）。取不到返回空串。"""
        ok, data = self._request("get_msg", {"message_id": message_id})
        if not ok:
            return ""
        message = data.get("data")
        if isinstance(message, dict):
            message = message.get("message")
        return extract_text(message).strip()

    def login_info(self) -> dict[str, Any] | None:
        """调 get_login_info，确认协议端在线且已登录。不可达返回 None。"""
        ok, data = self._request("get_login_info", {})
        if not ok:
            return None
        info = data.get("data")
        return info if isinstance(info, dict) else {}

    def friend_list(self) -> list[dict[str, Any]] | None:
        """好友列表——真正打到 QQ 内核的接口，用来看会话是否还活着。

        注意不能用 get_status：QQ 内核断网时它照样返回 online=true，
        只有走内核的接口才会露出马脚。
        """
        ok, data = self._request("get_friend_list", {})
        if not ok:
            return None
        items = data.get("data")
        return items if isinstance(items, list) else None

    def _request(self, action: str, payload: dict) -> tuple[bool, dict[str, Any]]:
        url = f"{self.api_url}/{action}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return False, {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return False, {}
        if not isinstance(data, dict):
            return False, {}
        # OneBot 约定：status == "ok" 或 retcode == 0
        ok = data.get("status") == "ok" or data.get("retcode") == 0
        return ok, data

    def _call(self, action: str, payload: dict) -> bool:
        ok, _data = self._request(action, payload)
        return ok
