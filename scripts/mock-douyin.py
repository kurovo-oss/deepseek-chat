"""本地假抖音：给 CDP 集成验证用（不联网、不登录）。

路由：
- `/`           推荐流，三条卡片（111 猫、222 云、333 游戏）
- `/video/<id>` 视频页，带一个能切换 aria-pressed 的点赞按钮
- `/health`     探活
"""

from __future__ import annotations

import http.server
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765

FEED = {
    "111": "一只橘猫趴在键盘上睡着了",
    "222": "随手拍的云",
    "333": "游戏高光时刻",
}

FEED_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>假抖音</title></head>
<body>
<img data-e2e="user-avatar" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" />
<div id="feed">
{cards}
</div>
</body></html>"""

CARD = """<div data-e2e="feed-item" data-e2e-vid="{id}">
  <a href="/video/{id}">{text}</a>
</div>"""

DETAIL = """<!doctype html><html><head><meta charset="utf-8"><title>{text}</title></head>
<body>
<img data-e2e="user-avatar" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" />
<h1>{text}</h1>
<button id="like" aria-pressed="false" onclick="this.setAttribute('aria-pressed','true')">
  <span data-e2e="like-icon">♥</span>
</button>
</body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 安静点
        pass

    def do_GET(self):
        if self.path == "/health":
            body = b"ok"
            self.send_response(200)
        elif self.path.startswith("/video/"):
            aweme = self.path.rsplit("/", 1)[-1]
            body = DETAIL.format(text=FEED.get(aweme, "视频")).encode("utf-8")
            self.send_response(200)
        elif self.path in ("/", "/index.html"):
            cards = "".join(CARD.format(id=k, text=v) for k, v in FEED.items())
            body = FEED_HTML.format(cards=cards).encode("utf-8")
            self.send_response(200)
        else:
            body = b"not found"
            self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"mock douyin on http://127.0.0.1:{PORT}", flush=True)
    server.serve_forever()
