"""HTTP 层联调测试：用真实 socket 验证上报端点与发送客户端。

这一层容易被忽略：单元测试全绿，但协议端连不上往往就挂在这里。
"""

import base64
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from qqbot.main import make_handler
from qqbot.onebot import HttpSender


class FakeEngine:
    def __init__(self):
        self.payloads = []

    def handle_payload(self, payload):
        self.payloads.append(payload)


@pytest.fixture
def server():
    engine = FakeEngine()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(engine))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", engine
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _post(url, payload, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    return urllib.request.urlopen(request, timeout=5)


def test_event_endpoint_accepts_and_dispatches(server):
    base, engine = server
    event = {
        "post_type": "message",
        "message_type": "private",
        "user_id": 100,
        "message_id": 7,
        "message": [{"type": "text", "data": {"text": "你好"}}],
    }
    with _post(f"{base}/event", event) as response:
        assert response.status == 204
    assert engine.payloads == [event]


def test_health_endpoint(server):
    base, _ = server
    with urllib.request.urlopen(f"{base}/health", timeout=5) as response:
        assert response.status == 200
        assert response.read() == b"ok"


def test_bad_json_returns_400(server):
    base, engine = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(f"{base}/event", None, raw=b"{not json")
    assert exc.value.code == 400
    assert engine.payloads == []


def test_unknown_path_returns_404(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(f"{base}/nope", {})
    assert exc.value.code == 404


def test_engine_exception_does_not_break_server():
    class BoomEngine:
        def handle_payload(self, payload):
            raise RuntimeError("boom")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(BoomEngine()))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with _post(f"{base}/event", {}) as response:
            assert response.status == 204
        with urllib.request.urlopen(f"{base}/health", timeout=5) as response:
            assert response.status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


# ---------------- 发送端 ----------------
class _OkHandler(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.received.append(json.loads(self.rfile.read(length)))
        body = json.dumps({"status": "ok", "retcode": 0}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def ok_server():
    _OkHandler.received = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _OkHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def test_sender_posts_private_message(ok_server):
    sender = HttpSender(ok_server)
    assert sender.send_private(100, "你好呀") is True
    assert _OkHandler.received == [{"user_id": 100, "message": "你好呀"}]


def test_sender_returns_false_when_unreachable():
    # 指向一个几乎不可能开放的端口
    sender = HttpSender("http://127.0.0.1:1", timeout=1)
    assert sender.send_private(100, "你好") is False


def test_sender_posts_image_as_file_url(ok_server, tmp_path):
    image = tmp_path / "cat.png"
    image.write_bytes(b"fake")

    assert HttpSender(ok_server).send_private_image(100, image, mode="path") is True

    message = _OkHandler.received[-1]["message"]
    assert message.startswith("[CQ:image,file=file:///")
    assert "cat.png" in message


def test_sender_posts_image_as_base64(ok_server, tmp_path):
    image = tmp_path / "cat.png"
    image.write_bytes(b"fake")

    assert HttpSender(ok_server).send_private_image(100, image, mode="base64") is True

    message = _OkHandler.received[-1]["message"]
    assert message.startswith("[CQ:image,file=base64://")
    assert base64.b64encode(b"fake").decode() in message
