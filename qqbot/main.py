"""程序入口：加载配置、组装依赖、起 HTTP 服务接收 OneBot 上报。"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import (
    ConfigError,
    load_optional_prompt,
    load_settings,
    load_system_prompt,
)
from .collect import StickerCollector
from .engine import Engine
from .images import ImageLibrary, import_seed_library
from .llm import LLMClient
from .logsetup import setup_logging
from .memory import Memory
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
    collector = None
    if settings.image_enabled:
        imported = import_seed_library(store, settings.image_lib_dir, settings.image_dir)
        if imported:
            log.info("已导入内置图 %d 张", imported)
        images = ImageLibrary(store, settings.image_dir)
        log.info("图库：%d 张图 / %d 个 tag", len(images), len(images.tags))
        if settings.image_collect_enabled:
            collector = StickerCollector(
                settings,
                store,
                settings.image_dir,
                llm=llm,
                tag_prompt=load_optional_prompt(settings.tag_prompt_file),
            )

    memory = None
    if settings.memory_enabled:
        memory = Memory(
            settings,
            store,
            llm,
            extract_prompt=load_optional_prompt(settings.extract_prompt_file),
            compact_prompt=load_optional_prompt(settings.compact_prompt_file),
        )
        log.info(
            "记忆已启用：%d 张卡片，阈值 %.2f，半衰期 %.0f 天",
            store.count_cards(),
            settings.card_weight_threshold,
            settings.decay_half_life_days,
        )

    return Engine(
        settings,
        store,
        llm,
        sender,
        load_system_prompt(settings),
        images=images,
        memory=memory,
        collector=collector,
    )


def make_handler(engine: Engine):
    class OneBotHandler(BaseHTTPRequestHandler):
        server_version = "qqbot/0.1"

        def _read_body(self) -> bytes:
            """读请求体。协议端常用 chunked，不实现就会拿到空 body。"""
            if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                chunks: list[bytes] = []
                while True:
                    line = self.rfile.readline(65536).strip()
                    if not line:
                        break
                    try:
                        size = int(line.split(b";")[0], 16)
                    except ValueError:
                        break
                    if size == 0:
                        self.rfile.readline(65536)  # trailer
                        break
                    chunks.append(self.rfile.read(size))
                    self.rfile.read(2)  # 每块后的 CRLF
                return b"".join(chunks)

            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            return self.rfile.read(length) if length > 0 else b""

        def _decode_body(self, raw: bytes) -> bytes:
            encoding = (self.headers.get("Content-Encoding") or "").lower()
            if "gzip" in encoding:
                return gzip.decompress(raw)
            if "deflate" in encoding:
                return zlib.decompress(raw)
            return raw

        def do_POST(self) -> None:  # noqa: N802 (http.server 约定)
            log.debug(
                "收到上报：%s，chunked=%s，Content-Length=%s，Content-Type=%s",
                self.path,
                bool(self.headers.get("Transfer-Encoding")),
                self.headers.get("Content-Length"),
                self.headers.get("Content-Type"),
            )

            # 先把请求体读完再判断路径：否则客户端还在写，连接会被 RST
            raw = self._read_body()

            if self.path.split("?")[0].rstrip("/") != "/event":
                self.send_error(404, "not found")
                return
            if not raw:
                log.warning(
                    "收到空事件体，headers=%s",
                    {
                        "Content-Length": self.headers.get("Content-Length"),
                        "Transfer-Encoding": self.headers.get("Transfer-Encoding"),
                        "Content-Type": self.headers.get("Content-Type"),
                    },
                )
                self.send_error(400, "empty body")
                return

            try:
                payload = json.loads(self._decode_body(raw).decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError, OSError, zlib.error) as exc:
                log.warning("事件体解析失败：%s；前 200 字节=%r", exc, raw[:200])
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
    parser.add_argument(
        "--check-adapter", action="store_true", help="只探测协议端是否在线并已登录"
    )
    args = parser.parse_args(argv)

    base = Path(args.base_dir) if args.base_dir else Path.cwd()
    # 先按默认位置配日志：这样连"配置错误"本身也能落盘
    setup_logging(base / "logs")

    try:
        settings = load_settings(base)
    except ConfigError as exc:
        log.error("配置错误：%s", exc)
        return 2

    log_file = setup_logging(settings.log_dir)

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

    if args.check_adapter:
        info = HttpSender(
            settings.adapter_api_url, settings.adapter_token
        ).login_info()
        if info is None:
            print(
                f"协议端探测：失败（{settings.adapter_api_url} 无响应）\n"
                "  检查：NapCat/LLOneBot 是否已启动、HTTP API 端口是否一致、"
                "ADAPTER_TOKEN 是否正确"
            )
            return 1
        print(
            f"协议端探测：OK（已登录 {info.get('nickname', '?')}"
            f"（{info.get('user_id', '?')}））"
        )
        return 0

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
        "已启动：监听 http://%s:%s/event ，模型 %s（日志 %s）",
        settings.listen_host,
        settings.listen_port,
        settings.model_id,
        log_file,
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
