"""程序入口：加载配置、组装依赖、起 HTTP 服务接收 OneBot 上报。"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import socket
import sys
import threading
import time
import urllib.parse
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import digest as digest_mod
from .config import (
    ConfigError,
    load_optional_prompt,
    load_settings,
    load_system_prompt,
)
from .collect import StickerCollector
from .dispatcher import MessageDispatcher
from .dream import start_dreamer
from . import douyin as douyin_mod
from . import guard as guard_mod
from . import qzone as qzone_mod
from .engine import Engine
from .mood import Mood
from . import stickers as stickers_mod
from .images import ImageLibrary, import_seed_library
from .llm import LLMClient
from .logsetup import setup_logging
from .memory import Memory
from .onebot import HttpSender
from .proactive import start_scheduler
from .replay import start_replayer
from .store import Store
from .watchdog import start_watchdog

log = logging.getLogger("qqbot")

# 上报体上限：正常事件几百字节，上百万字节只可能是打错端口或者被塞东西
MAX_BODY_BYTES = 1_000_000


def model_host(base_url: str) -> str | None:
    """从 API 地址里取主机名，取不到返回 None。"""
    return urllib.parse.urlsplit(base_url or "").hostname or None


def reachable(host: str, port: int = 443, timeout: float = 5.0) -> bool:
    """能不能建立 TCP 连接。开机自启时网络常比登录慢，用它当就绪探测。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_network(
    probe,
    timeout: float = 60.0,
    interval: float = 3.0,
    clock=time.monotonic,
    sleep=time.sleep,
) -> float | None:
    """等网络就绪。返回等待秒数（0 表示立刻就通），超时返回 None。"""
    started = clock()
    deadline = started + timeout
    while True:
        try:
            if probe():
                return max(0.0, clock() - started)
        except Exception:  # noqa: BLE001 - 探测本身出错也当没通
            pass
        if clock() >= deadline:
            return None
        sleep(interval)


def build_engine(settings) -> Engine:
    store = Store(settings.db_path)
    guard = guard_mod.Guard(store, settings)
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
        try:
            stickers_mod.ensure_seeded(settings, store, images)
        except Exception:  # noqa: BLE001 - 表情库坏了不该影响启动
            log.info("表情库初始化失败", exc_info=True)
        if settings.image_collect_enabled:
            collector = StickerCollector(
                settings,
                store,
                settings.image_dir,
                llm=llm,
                tag_prompt=load_optional_prompt(settings.tag_prompt_file),
                guard=guard,
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


def make_handler(dispatcher):
    class OneBotHandler(BaseHTTPRequestHandler):
        server_version = "qqbot/0.1"

        def _read_body(self) -> bytes:
            """读请求体。协议端常用 chunked，不实现就会拿到空 body。

            有上限：正常事件几百字节，几 MB 的 body 只可能是打错端口或者被人塞东西。
            """
            if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                chunks: list[bytes] = []
                total = 0
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
                    total += size
                    if total > MAX_BODY_BYTES:
                        raise ValueError("body too large")
                    chunks.append(self.rfile.read(size))
                    self.rfile.read(2)  # 每块后的 CRLF
                return b"".join(chunks)

            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > MAX_BODY_BYTES:
                raise ValueError("body too large")
            return self.rfile.read(length) if length > 0 else b""

        def _drain_body(self) -> None:
            """拒收后把剩下的请求体读干净。

            不读的话，服务端先关连接、对端还在写，内核回 RST——
            客户端拿到的是连接被重置，而不是我们精心准备的 413。
            （压测里这个偶发失败就是这么来的。）
            """
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return
            remaining = min(length, 4 * MAX_BODY_BYTES)
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)

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
            try:
                raw = self._read_body()
            except ValueError:
                self._drain_body()
                log.warning("事件体超过上限（%d 字节），已拒收", MAX_BODY_BYTES)
                self.send_error(413, "body too large")
                return

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

            # 只入队就回 204：NapCat 的 baseTimeout 是 10 秒，等我们处理完
            # 再应答就会被判超时重推，那种重推会造成同一条消息回两遍。
            try:
                dispatcher.submit(payload)
            except Exception:  # noqa: BLE001 - 投递失败也要回 204，否则触发重推
                log.exception("投递事件失败")

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


def check_model(settings, timeout: float = 15.0) -> tuple[bool, str]:
    """启动自检：key 失效时现在就喊出来。

    真实教训（2026-10-29）：换了 .env 里的 key 但没重启进程，进程还拿着旧 key，
    用户发消息只收到兜底话术，日志里才有 401——不说出来等于没有。
    """
    client = LLMClient(
        settings.model_base_url,
        settings.model_api_key,
        settings.model_id,
        timeout=timeout,
        retries=0,
    )
    try:
        return client.probe()
    except Exception as exc:  # noqa: BLE001 - 自检失败不该拖垮启动
        return False, str(exc)


def _warm_digests(engine, settings) -> None:
    """启动后补最近几天缺的日摘要。失败不影响服务。"""
    try:
        made = digest_mod.ensure_recent(engine.store, engine.llm, settings)
    except Exception:  # noqa: BLE001 - 补摘要失败不该拖垮启动
        log.exception("补日摘要失败")
        return
    log.info("日摘要已补齐：新写 %d 篇", made)


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
    dispatcher = MessageDispatcher(engine)
    try:
        server = ThreadingHTTPServer(
            (settings.listen_host, settings.listen_port), make_handler(dispatcher)
        )
    except OSError as exc:
        log.error("无法监听 %s:%s — %s", settings.listen_host, settings.listen_port, exc)
        engine.store.close()
        return 3

    # 端口已绑定（连接会排队，不会丢），这时才等网络：
    # 开机自启常常比网络先起来，别让第一条消息直接吃兜底话术
    host = model_host(settings.model_base_url)
    if host and settings.network_wait_seconds > 0:
        waited = wait_for_network(
            lambda: reachable(host), timeout=settings.network_wait_seconds
        )
        if waited is None:
            log.warning(
                "网络还没通（已等 %.0f 秒），先照常启动；期间的消息可能失败",
                settings.network_wait_seconds,
            )
        elif waited >= 1:
            log.info("等了 %.0f 秒才等到网络", waited)

    stop_event = threading.Event()
    dispatcher.start(stop_event)

    model_ok, model_detail = check_model(settings)
    if model_ok:
        log.info("模型自检通过：%s", model_detail)
    else:
        log.error(
            "模型自检失败：%s。现在收到的消息只会回兜底话术，请检查 MODEL_API_KEY（改完必须重启）",
            model_detail,
        )

    if settings.adapter_watchdog_seconds > 0:
        start_watchdog(engine.sender, settings.adapter_watchdog_seconds, stop_event)

    start_replayer(engine, engine.store, settings, stop_event)
    if settings.catchup_enabled or settings.recap_enabled:
        threading.Thread(
            target=engine.catchup_loop, args=(stop_event,), name="catchup", daemon=True
        ).start()
    log.info(
        "失败回复重放已启用：%.0f 分钟内的回复，最多补发 %d 次",
        settings.replay_window_minutes,
        settings.replay_max_attempts,
    )
    if settings.proactive_enabled:
        scheduler = start_scheduler(
            engine,
            engine.store,
            settings,
            stop_event,
            prompt=load_optional_prompt(settings.proactive_prompt_file),
            scheduled_prompt=load_optional_prompt(settings.scheduled_prompt_file),
        )
        # /proactive 手动触发，方便立刻验证效果（不用等 72 小时）
        engine.proactive_trigger = scheduler.force
        log.info(
            "主动消息已启用：每天最多 %d 条，%d–%d 点，间隔 ≥%.0f 小时，静默 %.0f 小时触发",
            settings.proactive_max_per_day,
            settings.proactive_start_hour,
            settings.proactive_end_hour,
            settings.proactive_min_gap_hours,
            settings.proactive_idle_hours,
        )

    engine.guard = guard_mod.Guard(engine.store, settings)
    if settings.mood_enabled:
        engine.mood = Mood(engine.store, settings)
        log.info("情绪状态：%s", engine.mood.line())

    if settings.qzone_enabled:
        threading.Thread(
            target=qzone_mod.tick_loop,
            args=(engine.store, engine.llm, settings, engine.guard, engine.sender, stop_event),
            kwargs={"mood": engine.mood},
            name="qzone",
            daemon=True,
        ).start()
        log.info(
            "空间已启用：每天最多 %d 条、配图 %s、试运行 %s",
            settings.qzone_daily_max,
            "开" if settings.qzone_image_enabled else "关",
            "开" if settings.qzone_dry_run else "关",
        )

    if settings.douyin_enabled:
        threading.Thread(
            target=douyin_mod.tick_loop,
            args=(engine.store, settings, engine.guard, engine.sender, stop_event),
            kwargs={"mood": engine.mood, "llm": engine.llm},
            name="douyin",
            daemon=True,
        ).start()
        log.info(
            "抖音已启用：每天最多 %d 个赞、%d 条分享、试运行 %s（profile %s）",
            settings.douyin_daily_max,
            settings.douyin_share_daily_max,
            "开" if settings.douyin_dry_run else "关",
            settings.douyin_profile,
        )

    if settings.dream_enabled or settings.reflect_enabled:
        start_dreamer(engine, engine.store, settings, stop_event)
        log.info(
            "夜间整理已启用：做梦 %s、睡前反省 %s（上次做梦：%s）",
            "开" if settings.dream_enabled else "关",
            "开" if settings.reflect_enabled else "关",
            time.strftime("%m-%d %H:%M", time.localtime(engine.store.last_dream_at() or 0))
            if engine.store.last_dream_at()
            else "还没做过",
        )

    if settings.digest_enabled:
        threading.Thread(
            target=_warm_digests,
            args=(engine, settings),
            name="digest-warmup",
            daemon=True,
        ).start()

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
        stop_event.set()
        server.server_close()
        engine.store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
