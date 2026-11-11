"""最小 CDP 客户端：够用就好的 WebSocket（RFC 6455）+ Chrome DevTools Protocol。

项目零第三方依赖，标准库没有 WebSocket，所以这里自己实现：
握手、掩码、文本帧、分片不支持（CDP 不用）、ping 回 pong。

只给抖音模块用：启动一个独立 profile 的 Edge/Chrome，用 CDP 在页面里执行 JS。
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import time
import urllib.request
from typing import Any

OPCODE_TEXT = 1
OPCODE_CLOSE = 8
OPCODE_PING = 9
OPCODE_PONG = 10


class CDPError(RuntimeError):
    pass


def _read_exact(sock: socket.socket, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise CDPError("连接被关闭")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def encode_frame(payload: bytes, opcode: int = OPCODE_TEXT, mask: bool = True) -> bytes:
    header = bytearray()
    header.append(0x80 | opcode)
    length = len(payload)
    mask_bit = 0x80 if mask else 0
    if length < 126:
        header.append(mask_bit | length)
    elif length < 65536:
        header.append(mask_bit | 126)
        header += struct.pack("!H", length)
    else:
        header.append(mask_bit | 127)
        header += struct.pack("!Q", length)
    if not mask:
        return bytes(header) + payload
    key = os.urandom(4)
    masked = bytes(byte ^ key[index % 4] for index, byte in enumerate(payload))
    return bytes(header) + key + masked


def decode_frame(read) -> tuple[int, bytes]:
    """read(n) -> bytes。返回 (opcode, payload)。"""
    first, second = read(2)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        (length,) = struct.unpack("!H", read(2))
    elif length == 127:
        (length,) = struct.unpack("!Q", read(8))
    key = read(4) if masked else b""
    payload = read(length) if length else b""
    if masked and key:
        payload = bytes(byte ^ key[index % 4] for index, byte in enumerate(payload))
    return opcode, payload


class WebSocket:
    def __init__(self, url: str, timeout: float = 20.0) -> None:
        if not url.startswith("ws://"):
            raise CDPError("只支持 ws://")
        host_port, _, path = url[5:].partition("/")
        host, _, port = host_port.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET /{path} HTTP/1.1\r\n"
            f"Host: {host_port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("握手失败：连接被关闭")
            head += chunk
        status = head.split(b"\r\n", 1)[0]
        if b"101" not in status:
            raise CDPError(f"握手失败：{status!r}")

    def send(self, text: str) -> None:
        self.sock.sendall(encode_frame(text.encode("utf-8")))

    def recv(self) -> str:
        opcode, payload = decode_frame(self._read)
        if opcode == OPCODE_CLOSE:
            raise CDPError("服务端关闭了连接")
        if opcode == OPCODE_PING:
            self.sock.sendall(encode_frame(payload, opcode=OPCODE_PONG))
            return self.recv()
        return payload.decode("utf-8", "replace")

    def _read(self, size: int) -> bytes:
        return _read_exact(self.sock, size)

    def close(self) -> None:
        try:
            self.sock.sendall(encode_frame(b"", opcode=OPCODE_CLOSE))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class CDP:
    """一条页面连接的同步客户端。"""

    def __init__(self, ws_url: str, timeout: float = 20.0) -> None:
        self.ws = WebSocket(ws_url, timeout=timeout)
        self._id = 0

    def call(self, method: str, params: dict | None = None, timeout: float = 20.0) -> dict:
        self._id += 1
        message_id = self._id
        self.ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CDPError(f"{method} 超时")
            self.ws.sock.settimeout(remaining)
            try:
                raw = self.ws.recv()
            except socket.timeout as exc:
                raise CDPError(f"{method} 超时") from exc
            data = json.loads(raw)
            if data.get("id") != message_id:
                continue  # 事件，跳过
            if "error" in data:
                raise CDPError(f"{method}: {data['error']}")
            return data.get("result") or {}

    def evaluate(self, expression: str, await_promise: bool = True, timeout: float = 20.0) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": True,
            },
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            text = (details.get("exception") or {}).get("description") or details.get("text", "脚本出错")
            raise CDPError(text[:300])
        return (result.get("result") or {}).get("value")

    def goto(self, url: str, timeout: float = 30.0) -> None:
        self.call("Page.navigate", {"url": url}, timeout=timeout)

    def screenshot(
        self,
        clip: dict | None = None,
        quality: int = 72,
        timeout: float = 20.0,
    ) -> bytes:
        """截当前页面，或只截 clip={x,y,width,height,scale} 区域。"""
        params = {
            "format": "jpeg",
            "quality": max(1, min(100, int(quality))),
            "fromSurface": True,
        }
        if clip:
            params["clip"] = clip
        result = self.call("Page.captureScreenshot", params, timeout=timeout)
        raw = result.get("data")
        if not raw:
            raise CDPError("截图没有返回数据")
        return base64.b64decode(raw)

    def close(self) -> None:
        self.ws.close()


def list_targets(port: int, timeout: float = 5.0) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def page_target(port: int, timeout: float = 5.0, prefer: str = "") -> dict:
    """挑一个真正的网页 target；跳过 edge://、about:blank 和弹窗。"""
    targets = [
        target for target in list_targets(port, timeout=timeout)
        if target.get("type") == "page" and target.get("webSocketDebuggerUrl")
    ]
    pages = [t for t in targets if str(t.get("url", "")).startswith(("http://", "https://"))]
    if prefer:
        for target in pages:
            if prefer in str(target.get("url", "")):
                return target
    if pages:
        return pages[0]
    for target in targets:
        if str(target.get("url", "")).startswith("about:blank"):
            return target
    if targets:
        # 只有 edge://newtab 之类也行，调用方会自己导航
        return targets[0]
    raise CDPError("没有找到页面 target")


def wait_for_cdp(port: int, timeout: float = 30.0, prefer: str = "") -> dict:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return page_target(port, prefer=prefer)
        except Exception as exc:  # noqa: BLE001 - 浏览器还没起来
            last_error = exc
            time.sleep(0.5)
    raise CDPError(f"CDP 没起来：{last_error}")
