"""程序入口：加载配置、组装依赖、起 HTTP 服务接收 OneBot 上报。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import ConfigError, load_settings, load_system_prompt
from .engine import Engine
from .images import ImageLibrary
from .llm import LLMClient
from .onebot import HttpSender
from .store import Store

log = logging.getLogger("qqbot")


def build_engine(settings) -> Engine:
    store = Store(settings.db_path)
    llm = LLMClient(
        base_url=settings.model_base_url,
        api_key=settings.model_api_key,
        model=settings.model_id,
        timeout=settings.request_timeout,
    )
    sender = HttpSender(settings.adapter_api_url, settings.adapter_token)

    images = None
    if settings.image_enabled:
        if settings.image_lib_dir.is_dir():
            images = ImageLibrary(settings.image_lib_dir)
            log.info("图库已加载：%d 张，tags=%s", len(images), images.tags)
        else:
            log.warning("图库目录不存在，配图功能关闭：%s", settings.image_lib_dir)

    return Engine(
        settings,
        store,
        llm,
        sender,
        load_system_prompt(settings),
        images=images,
    )


def make_handler(engine: Engine):
    class OneBotHandler(BaseHTTPRequestHandler):
        server_version = "qqbot/0.1"

        def do_POST(self) -> None:  # noqa: N802 (http.server 约定)
            if self.path.split("?")[0].rstrip("/") != "/event":
                self.send_error(404, "not found")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_error(400, "bad json")
                return

            try:
                engine.handle_payload(payload)
            except Exception:  # 单条事件处理失败不能拖垮服务
                log.exception("处理事件失败")

            self.send_response(204)
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?")[0].rstrip("/") == "/health":
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404, "not found")

        def log_message(self, fmt: str, *args) -> None:
            log.debug("%s - %s", self.address_string(), fmt % args)

    return OneBotHandler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qqbot", description="大肥鱼 QQ 机器人")
    parser.add_argument("--base-dir", default=None, help="项目根目录（默认当前目录）")
    parser.add_argument("--check", action="store_true", help="只校验配置并探测模型")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    base = Path(args.base_dir) if args.base_dir else Path.cwd()
    try:
        settings = load_settings(base)
    except ConfigError as exc:
        log.error("配置错误：%s", exc)
        return 2

    if args.check:
        client = LLMClient(
            settings.model_base_url,
            settings.model_api_key,
            settings.model_id,
            timeout=settings.request_timeout,
        )
        ok, detail = client.probe()
        print(f"模型探测：{'OK' if ok else '失败'}（{detail}）")
        return 0 if ok else 1

    engine = build_engine(settings)
    try:
        server = ThreadingHTTPServer(
            (settings.listen_host, settings.listen_port), make_handler(engine)
        )
    except OSError as exc:
        log.error("无法监听 %s:%s — %s", settings.listen_host, settings.listen_port, exc)
        engine.store.close()
        return 3

    log.info(
        "已启动：监听 http://%s:%s/event ，模型 %s",
        settings.listen_host,
        settings.listen_port,
        settings.model_id,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("收到中断，正在退出")
    finally:
        server.server_close()
        engine.store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
