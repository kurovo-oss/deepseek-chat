"""编排层：白名单 → 幂等 → 指令 → 组 prompt → 调模型 → 落库 → 分段发送。

引擎不碰网络细节，也不碰 SQL：它只依赖 Store / LLMClient / Sender 三个接口，
所以测试里可以全部替换成假实现。
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import digest as digest_mod
from . import draw as draw_mod
from . import abilities as abilities_mod
from . import budget as budget_mod
from . import life as life_mod
from . import memes as memes_mod
from . import presence as presence_mod
from . import recap as recap_mod
from . import talkstate as talkstate_mod
from . import usage as usage_mod
from . import pc as pc_mod
from . import vision as vision_mod
from .commands import CommandsMixin
from .config import Settings
from .context import ContextMixin
from .export import export_all
from .formatting import (
    extract_draw_tags,
    fix_address,
    extract_image_tags,
    reshape_bubbles,
    split_bubbles,
    split_message,
    strip_markdown,
)
from .images import sniff_image
from .llm import LLMClient, LLMError
from .memory import KIND_LABEL, card_weight
from .onebot import IncomingMessage, Sender, parse_event
from .store import Store

log = logging.getLogger("qqbot")

from .sayings import (  # noqa: F401
    FALLBACK_REPLIES,
    FALLBACK_REPLY,
    HELP_TEXT,
    RECALL_TITLE,
    fallback_reply,
)


def _size_text(size: int) -> str:
    if size >= 1024 * 1024:
        return "%.1f MB" % (size / (1024 * 1024))
    return "%.0f KB" % (size / 1024)



class Engine(ContextMixin, CommandsMixin):
    def __init__(
        self,
        settings: Settings,
        store: Store,
        llm: Any,
        sender: Sender,
        system_prompt: str,
        sleep=time.sleep,
        images: Any = None,
        memory: Any = None,
        collector: Any = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.llm = llm
        self.sender = sender
        self.system_prompt = system_prompt
        self.images = images
        self.memory = memory
        self.collector = collector
        # 主动消息调度器建好后由 main 注入：/proactive 用它手动试一句
        self.proactive_trigger = None
        # 做梦器同样由 main 注入：/dream 手动整理一次
        self.dreamer = None
        # 情绪/驱力状态由 main 注入：没配就当没有，clone 也能跑
        self.mood = None
        # 一条回复可能分好几段发送；主动消息线程和消息线程同时发时，
        # 不加锁就会出现"她的话被劈成两半插进另一条回复中间"
        self._send_lock = threading.Lock()
        self._sleep = sleep
        # 画图是异步的：她说完"我画了"，图过几秒才到
        self._draw_lock = threading.Lock()
        self._last_draw_at = 0.0
        self._life_lock = threading.Lock()
        self._life_day = ""
        # 陪玩：看她看他打游戏
        self._watch_stop = threading.Event()
        self._watch_thread: threading.Thread | None = None
        self._watch_started = 0.0
        self._watch_lines = 0
        self._rng = random
        self._pending_sessions: set[str] = set()
        self._replying: set[str] = set()
        self._catchup_lock = threading.Lock()
        self._fallback_llm: LLMClient | None = None

    # ---------------- 入口 ----------------
    def handle_payload(self, payload: Any) -> bool:
        message = parse_event(payload)
        if message is None:
            info = payload if isinstance(payload, dict) else {}
            # 消息类事件被跳过要看得见（多为群消息或纯图片），其余噪声只记 debug
            level = (
                logging.INFO
                if info.get("post_type") == "message"
                else logging.DEBUG
            )
            log.log(
                level,
                "跳过的上报：post_type=%s message_type=%s user_id=%s",
                info.get("post_type"),
                info.get("message_type"),
                info.get("user_id"),
            )
            return False
        self.handle_message(message)
        return True

    def handle_message(self, message: IncomingMessage) -> None:
        # 白名单：非本人消息一律静默，不回复、不落库
        if message.user_id != self.settings.allow_user_id:
            log.info(
                "忽略非白名单用户 %s（当前只服务 %s）",
                message.user_id,
                self.settings.allow_user_id,
            )
            return

        text = message.text.strip()
        if not text and not message.images:
            return

        session = str(message.user_id)

        # 每天补一次"她今天的日子"，后台生成，不挡回复
        self._ensure_life()

        # 幂等靠"抢占式写入"：先落库占位，写不进去说明这条已经处理过了。
        # 顺序很关键——必须在任何网络 IO（下载表情包、调模型）之前完成。
        # 否则"查重 → 下载 → 写入"之间有个几秒的窗口，NapCat 超时重推就会
        # 让同一条消息被处理两遍（2026-10-29 审查发现的竞态）。
        # 引用回复：被引用的那句话不在消息段里，得单独去协议端取
        extras: list[str] = []
        if message.reply_id:
            quoted = self._quoted_text(message.reply_id)
            if quoted:
                extras.append(f"（他引用了这句：{quoted}）")
        # 引用在前、他的话在后：读起来是"接着这句说的"
        content = "\n".join(part for part in [*extras, text] if part).strip()

        record_id = self.store.save_message(
            session, "user", content or "[表情]", qq_msg_id=message.message_id or None
        )
        if record_id is None:
            log.info("重复消息，跳过：qq_msg_id=%s", message.message_id)
            return

        # 看图要花几秒（下载 + 多模态），必须在幂等占位之后做：
        # 否则 NapCat 超时重推会让同一张图被识别两遍、缓存也白做。
        note = ""
        if message.images and self.settings.vision_enabled:
            note = self._describe_images(message)
            if note:
                hint = self._image_context_hint(session, record_id)
                parts = [*extras, text, note, hint]
                self.store.update_message_content(
                    record_id, "\n".join(part for part in parts if part).strip()
                )

        if self.mood is not None:
            try:
                # 他来了 + 这句话的情绪：两条一起推一把状态
                self.mood.note_message(text or "")
            except Exception:  # noqa: BLE001 - 情绪是附加状态，不能拖垮对话
                log.exception("更新情绪状态失败")

        collected: list[dict] = []
        if message.images and self.collector is not None:
            try:
                collected = self.collector.collect(
                    message, self.store.recent_messages(session, 4)
                )
            except Exception:  # noqa: BLE001 - 收藏失败不能影响对话
                log.exception("收藏表情包失败")
            if collected:
                log.info("收藏了 %d 张表情包", len(collected))

        log.info("收到来自 %s 的消息：%s", message.user_id, text[:60] or "[表情]")

        if not text and not note and not extras:
            self._send_reply(message.user_id, session, self._sticker_ack(collected))
            return

        command_reply = self._handle_command(text, session)
        if command_reply is not None:
            # 指令回复是我们自己写的，不能当模型输出做 Markdown 清洗：
            # 否则 "/img list" 的编号（"7. ..."）会被当成有序列表剥掉
            self._send_reply(
                message.user_id, session, command_reply, sanitize=False, origin="command"
            )
            # /reset 的游标要在"回复本身"落库之后再定，否则重置后的
            # 第一条消息会把 /reset 的应答也带进上下文
            if text.lower().startswith("/reset"):
                top = self.store.max_message_id(session)
                self.store.set_cursor(self._window_key(session), str(top))
            return

        # 她不是客服：在打游戏、在刷抖音的时候，几条才回一条是正常的
        if not self._should_reply_now(session):
            log.info(
                "她这会儿%s，这条先不回（忙完补）",
                presence_mod.describe(self._presence_state()),
            )
            self._pending_sessions.add(session)
            return

        self._replying.add(session)
        try:
            reply = self._chat(session)
            self._send_reply(message.user_id, session, reply, origin="chat")
        finally:
            self._replying.discard(session)
            self._pending_sessions.discard(session)
        if self.memory is not None and reply not in FALLBACK_REPLIES:
            # 抽取与压缩发生在回复发出之后，不拖慢响应。
            # 兜底话术不喂给抽取：那不是她说的话，抽出来的卡片全是垃圾。
            self.memory.note_reply(session, text)

    # ---------------- 指令 ----------------

    # ---------------- 记忆指令 ----------------







    # ---------------- 图库指令 ----------------



    def _clean_archived_images(self) -> tuple[int, int]:
        """把已归档图片的文件和记录真正删掉，返回 (张数, 释放字节)。

        淘汰时只置 `archived`（规范如此：不再发送但保留文件），久了还是会
        占满盘——所以留一个手动回收口，不自动删，免得删掉你还想看的东西。
        """
        if self.images is None:
            return 0, 0

        removed = 0
        freed = 0
        for image in self.store.list_images(status="archived", limit=1000):
            path = self.images.path_of(image)
            if path is not None:
                try:
                    size = path.stat().st_size
                    path.unlink()
                    freed += size
                except OSError:
                    log.warning("回收旧图失败（跳过）：%s", path)
                    continue
            self.store.delete_image(image["id"])
            removed += 1

        if removed:
            log.info("回收归档图片 %d 张，释放 %s", removed, _size_text(freed))
        return removed, freed


    _EVENT_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
    _EVENT_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
    EVENT_DEFAULT_HOUR = 19





    # ---------------- 对话 ----------------
    # ---------------- 忙完补回 ----------------
    def catchup_tick(self, now: float | None = None) -> int:
        """她收工了：把晾着的那几条补回。返回补了几条。"""
        stamp = time.time() if now is None else now
        # 前情摘要要调模型，只在后台线程里做；失败也不影响补回。
        # 放在"忙闲"判断之前：补回可以关，但记忆不该因此断档。
        if self.settings.recap_enabled:
            try:
                recap_mod.tick(self, str(self.settings.allow_user_id), now=stamp)
            except Exception:  # noqa: BLE001 - 摘要坏了不能拖累补回
                log.exception("前情摘要异常")

        if not (self.settings.presence_enabled and self.settings.catchup_enabled):
            return 0
        replayed = 0
        with self._catchup_lock:
            for session in list(self._pending_sessions):
                if session in self._replying:
                    continue
                if self._pending_count(session) == 0:
                    self._pending_sessions.discard(session)
                    continue
                last = next(
                    (row for row in reversed(self.store.recent_messages(session, 4))
                     if row["role"] == "user"),
                    None,
                )
                if last is None:
                    self._pending_sessions.discard(session)
                    continue
                age = stamp - int(last.get("created_at") or stamp)
                if age > self.settings.catchup_max_age_seconds:
                    # 太久以前的话就不追着回了，那已经不是"没看见"
                    self._pending_sessions.discard(session)
                    continue
                if age < self.settings.busy_reply_after_seconds:
                    continue
                self._replying.add(session)
                try:
                    reply = self._chat(session)
                    self._send_reply(int(session), session, reply, origin="chat")
                    if self.memory is not None and reply not in FALLBACK_REPLIES:
                        self.memory.note_reply(session, str(last.get("content") or ""))
                    replayed += 1
                except Exception:  # noqa: BLE001 - 补回失败不能让线程死
                    log.exception("忙完补回失败")
                finally:
                    self._replying.discard(session)
                    self._pending_sessions.discard(session)
        if replayed:
            log.info("忙完补回了 %d 条", replayed)
        return replayed

    def catchup_loop(self, stop_event: threading.Event) -> None:
        # 重启后先把线索引出来：上次进程里没来得及补的那几条，这次接着补
        self._pending_sessions.add(str(self.settings.allow_user_id))
        while not stop_event.is_set():
            try:
                self.catchup_tick()
            except Exception:  # noqa: BLE001 - 补回线程不能死
                log.exception("补回线程异常")
            if stop_event.wait(self.settings.catchup_interval_seconds):
                break

    # ---------------- 她的状态 ----------------
    def _presence_state(self) -> dict:
        return presence_mod.current(
            self.settings.presence_file, time.time(), self._rng
        )

    def _pending_count(self, session: str) -> int:
        """他连发了几条、她还没回。"""
        count = 0
        for row in reversed(self.store.recent_messages(session, 12)):
            if row["role"] == "assistant":
                break
            if row["role"] == "user":
                count += 1
        return count

    def _should_reply_now(self, session: str) -> bool:
        if not self.settings.presence_enabled:
            return True
        # 他发了三条以上，再忙也得回一句，不然像死了
        if self._pending_count(session) >= self.settings.busy_max_pending:
            return True
        state = self._presence_state()
        if not state.get("busy"):
            return True
        return self._rng.random() >= self.settings.busy_skip_chance


    def _fallback_client(self) -> LLMClient | None:
        """备用通道。没配模型就不建，省得白占内存。"""
        if not self.settings.fallback_model_id:
            return None
        if self._fallback_llm is None:
            self._fallback_llm = LLMClient(
                base_url=self.settings.fallback_base_url or self.settings.model_base_url,
                api_key=self.settings.fallback_api_key or self.settings.model_api_key,
                model=self.settings.fallback_model_id,
                timeout=self.settings.request_timeout,
                retries=1,
                sleep=self._sleep,
            )
        return self._fallback_llm

    def _complete(self, messages: list[dict]):
        """主通道 → 备用通道 → None。None 表示"这轮真的说不了话"。"""
        try:
            return self.llm.chat(messages)
        except LLMError as exc:
            log.warning("主通道失败：%s", exc)
        backup = self._fallback_client()
        if backup is None:
            self.store.log_usage(self.settings.model_id, 0, 0, "chat", ok=False)
            return None
        try:
            result = backup.chat(messages)
        except LLMError as exc:
            log.warning("备用通道也失败：%s", exc)
            self.store.log_usage(self.settings.model_id, 0, 0, "chat", ok=False)
            return None
        log.info("切到备用通道：%s", result.model)
        return result

    def _chat(self, session: str) -> str:
        messages = self._build_messages(session)
        result = self._complete(messages)
        if result is None:
            return fallback_reply()
        self.store.log_usage(result.model, result.tokens_in, result.tokens_out, "chat", True)
        # 顺手校准：估了多少、接口报了多少，攒样本给预算用
        estimated = sum(
            budget_mod.estimate_tokens(str(item.get("content") or "")) for item in messages
        )
        usage_mod.record(self.settings, estimated, result.tokens_in)
        if not result.text:
            log.warning("模型返回空内容，改用兜底话术")
            return fallback_reply()
        # 真人不在聊天框里空行；模型爱用它分段，压成单换行
        return re.sub(r"\n{2,}", "\n", result.text)



    # ---------------- 发送 ----------------
    def _pick_image(self, session: str, tags: list[str]) -> Path | None:
        """配图策略：冷却窗口内发过图就直接放弃，不找替代，也不提示。"""
        if not tags or self.images is None or not self.settings.image_enabled:
            return None
        if self.store.has_recent_image(session, self.settings.image_cooldown_messages):
            return None
        for tag in tags:
            path = self.images.resolve(tag, max_bytes=self.settings.image_max_bytes)
            if path is not None:
                return path
        return None

    def _sticker_ack(self, collected: list[dict]) -> str:
        if not collected:
            return "嗯，我看到啦。"
        tags = collected[0].get("tags") or []
        if tags:
            return f"这张我存下啦嘿嘿，记成「{'、'.join(tags)}」了。"
        return "这张我存下啦嘿嘿，回头再给它起个名字。"

    def _send_reply(
        self,
        user_id: int,
        session: str,
        raw_text: str,
        sanitize: bool = True,
        origin: str = "reply",
    ) -> bool:
        if sanitize:
            cleaned = strip_markdown(raw_text or "")
            cleaned = re.sub(r"\n{2,}", "\n", cleaned)  # 不空行，像打字不像排版
        else:
            # 指令回复：只去首尾空白，编号和格式原样保留
            cleaned = (raw_text or "").strip()
        cleaned, tags = extract_image_tags(cleaned)
        cleaned, drawings = extract_draw_tags(cleaned)

        image_path = self._pick_image(session, tags)
        parts = split_bubbles(
            cleaned, self.settings.reply_max_chars, self.settings.reply_max_bubbles
        )
        if (
            origin in ("chat", "proactive")
            and self.settings.reply_reshape
            and cleaned not in FALLBACK_REPLIES
        ):
            # 只对"她自己说的话"随机改条数：指令回复是结构化输出，兜底话术改了就认不出来
            parts = reshape_bubbles(parts, random)
        if not parts and image_path is None and not drawings:
            log.warning(
                "回复内容为空（原文 %r，图片标记 %s），改用兜底话术", raw_text[:80], tags
            )
            parts = [fallback_reply()]

        content = cleaned if cleaned else ("[图片]" if image_path else " ".join(parts) or "[画的图]")
        record_id = self.store.save_message(
            session,
            "assistant",
            content,
            send_state="pending",
            image_tags=[image_path.name] if image_path else [],
            origin=origin,
        )

        all_ok = True
        base_delay = self.settings.segment_delay_ms / 1000
        with self._send_lock:
            for index, part in enumerate(parts):
                if index:
                    # 真人不按固定间隔打字：短句快、长句慢，再加点抖动
                    factor = random.uniform(0.6, 0.9) if len(part) <= 6 else random.uniform(1.0, 1.7)
                    self._sleep(base_delay * factor)
                if not self.sender.send_private(user_id, part):
                    all_ok = False

            if image_path is not None:
                if not self.sender.send_private_image(
                    user_id, image_path, self.settings.image_send_mode
                ):
                    all_ok = False
            elif not tags:
                # 她有时候会随手甩个表情包，不需要理由
                sticker = self._maybe_sticker(session)
                if sticker is not None:
                    if self.sender.send_private_image(
                        user_id, sticker, self.settings.image_send_mode
                    ):
                        self.store.save_message(
                            session,
                            "assistant",
                            "[表情包]",
                            image_tags=[sticker.name],
                            origin="sticker",
                        )
                    else:
                        all_ok = False

        if record_id is not None:
            self.store.update_send_state(record_id, "sent" if all_ok else "failed")
        for prompt in drawings[:1]:
            self._schedule_draw(user_id, session, prompt)
        return all_ok

    def _react_to(self, fact: str, limit: int = 30) -> str:
        """让"她"对一件系统事实说一句话。不是他说的，所以单独走一次模型。"""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": (
                    f"（系统提示，这不是他说的话：{fact}。"
                    f"你就顺着这个说一句，口语，{limit} 字以内，"
                    "别复述数字，别解释，别问他问题。）"
                ),
            },
        ]
        try:
            result = self.llm.chat(messages, temperature=0.85, max_tokens=500)
        except Exception:  # noqa: BLE001 - 她说不出来也别把命令搞挂
            log.exception("评论电脑状态失败")
            return ""
        return (result.text or "").strip()

    def _watch_command(self, rest: str, session: str) -> str:
        """陪玩：隔一会儿看一眼屏幕，随口说一句。"""
        if not self.settings.watch_enabled:
            return "陪玩这块关着（WATCH_ENABLED=false）。"
        action = rest.strip()
        alive = self._watch_thread is not None and self._watch_thread.is_alive()
        if action in ("停", "停吧", "不看了", "关", "结束"):
            if not alive:
                return "本来就没在看。"
            self._watch_stop.set()
            return "行，不看了。"
        if action in ("状态", "?", "？"):
            if not alive:
                return "没在看。"
            left = max(0, int(self.settings.watch_minutes * 60 - (time.time() - self._watch_started)))
            return f"看着呢，还剩 {left // 60} 分钟，已经念叨 {self._watch_lines} 句了。"
        if alive:
            return "已经在看着了，专心打你的。"
        self._watch_stop.clear()
        self._watch_started = time.time()
        self._watch_lines = 0
        self._watch_thread = threading.Thread(
            target=self._watch_loop, args=(int(session),), daemon=True
        )
        self._watch_thread.start()
        return "好，我搬个小板凳坐你旁边了。"

    def _watch_say(self, user_id: int, session: str, text: str) -> None:
        with self._send_lock:
            ok = self.sender.send_private(user_id, text)
        self.store.save_message(session, "assistant", text, send_state="sent" if ok else "failed",
                                origin="watch")

    def _watch_tick(self, session: str, said: list[str], last_hash: str) -> tuple[str, str] | None:
        """看一眼，说一句。画面跟上次差不多就不吭声。"""
        path = pc_mod.screenshot(self.settings.screen_dir)
        pc_mod.prune(self.settings.screen_dir, self.settings.screen_keep)
        data = pc_mod.shrink(path)
        digest = pc_mod.frame_hash(data)
        if not pc_mod.changed(last_hash, digest):
            return None
        if not self.settings.vision_enabled:
            return None
        note = vision_mod.note_for(self.store, self.llm, data)
        if not note:
            return None
        fact = (
            f"你正坐在他旁边看他玩电脑游戏，屏幕上：{note}。"
            "说一句你此刻的想法，像女朋友坐旁边随口说的：可以吐槽、可以心疼他、可以看热闹，"
            "也可以突然讲一句你自己的事。20 字以内，别问问题，别说「看不懂」。"
        )
        if said:
            fact += "你刚才已经说过：" + "；".join(said[-3:]) + "。换一句新的，别重复。"
        line = self._react_to(fact, limit=22)
        if not line:
            return None
        if line in said:
            return None
        return line, digest

    def _watch_loop(self, user_id: int) -> None:
        session = str(user_id)
        said: list[str] = []
        last_hash = ""
        deadline = time.time() + self.settings.watch_minutes * 60
        try:
            while not self._watch_stop.is_set():
                if time.time() >= deadline or self._watch_lines >= self.settings.watch_max_lines:
                    break
                try:
                    result = self._watch_tick(session, said, last_hash)
                except Exception:  # noqa: BLE001 - 看一眼失败不能让整个陪玩停下
                    log.exception("陪玩看一眼失败")
                    result = None
                if result is not None:
                    line, last_hash = result
                    self._watch_lines += 1
                    said.append(line)
                    self._watch_say(user_id, session, line)
                wait = self.settings.watch_interval_seconds * random.uniform(0.8, 1.25)
                if self._watch_stop.wait(wait):
                    break
        finally:
            log.info("陪玩结束：说了 %s 句", self._watch_lines)

    def _screen_command(self, session: str) -> str:
        """截一张屏：先发给他，再让她说一句。"""
        if not self.settings.screen_enabled:
            return "看屏幕这块关着（SCREEN_ENABLED=false）。"
        try:
            path = pc_mod.screenshot(self.settings.screen_dir)
        except Exception as exc:  # noqa: BLE001 - 截屏失败原因要原样告诉他
            log.exception("截屏失败")
            return f"截不了屏：{exc}"
        pc_mod.prune(self.settings.screen_dir, self.settings.screen_keep)

        comment = ""
        if self.settings.vision_enabled:
            try:
                data = pc_mod.shrink(path)
                note = vision_mod.note_for(self.store, self.llm, data)
                if note:
                    comment = self._react_to(f"你刚瞄了一眼他的电脑屏幕，上面是：{note}")
            except Exception:  # noqa: BLE001 - 看图失败也要把图发出去
                log.exception("看她截的屏失败")

        try:
            ok = self.sender.send_private_image(
                int(session), path, self.settings.image_send_mode
            )
            if not ok:
                log.warning("截图没发出去：%s", path)
        except Exception:  # noqa: BLE001
            log.exception("发截图失败")

        return comment or "看到了。"

    def _pc_command(self, rest: str) -> str:
        """电脑状态：默认让她说一句，`/pc 详细` 给原始数据。"""
        try:
            data = pc_mod.status()
        except Exception as exc:  # noqa: BLE001
            log.exception("读电脑状态失败")
            return f"看不到你的电脑：{exc}"
        summary = pc_mod.describe(data)
        if rest.strip() in ("详细", "数据", "全"):
            return summary or "什么都没读到。"
        comment = self._react_to(f"你刚看了一眼他的电脑状态：{summary}，他这会儿在电脑前")
        return comment or summary or "看不到。"

    def _maybe_sticker(self, session: str) -> Path | None:
        """随手甩个表情包。概率很低，且刚发过图就不发。"""
        chance = float(getattr(self.settings, "sticker_chance", 0.0) or 0.0)
        if chance <= 0 or self.images is None or not self.settings.image_enabled:
            return None
        if random.random() >= chance:
            return None
        if self.store.has_recent_image(session, self.settings.image_cooldown_messages):
            return None
        tags = [
            item.strip()
            for item in str(getattr(self.settings, "sticker_tags", "")).split(",")
            if item.strip()
        ]
        random.shuffle(tags)
        for tag in tags:
            path = self.images.resolve(tag, max_bytes=self.settings.image_max_bytes)
            if path is not None:
                return path
        return None


    # ---------------- 生活线 ----------------
    def _ensure_life(self) -> None:
        """每天补一次"她今天的日子"。生成要好几秒，放后台，别挡着回消息。"""
        if not self.settings.life_enabled:
            return
        day = time.strftime("%Y-%m-%d")
        with self._life_lock:
            if self._life_day == day:
                return
            self._life_day = day
        if life_mod.snippet(self.settings):
            life_mod.prune(self.settings)
            return
        threading.Thread(target=self._write_life, daemon=True).start()

    def _write_life(self) -> None:
        try:
            context = ""
            if self.memory is not None:
                journal = self.memory.journal()
                if journal:
                    context = "最近的记忆：" + journal[:300]
            digest = digest_mod.recent_text(self.settings)
            if digest:
                context = (context + "\n" + digest[:400]).strip()
            life_mod.today(self.settings, self.llm, context=context)
            life_mod.prune(self.settings)
        except Exception:  # noqa: BLE001 - 生活线是加分项
            log.exception("生成生活线失败")

    # ---------------- 画图 ----------------
    def _schedule_draw(self, user_id: int, session: str, prompt: str) -> None:
        """排队画一张图。太频繁就跳过——她说"我画了"不该变成批量出图。"""
        if not self.settings.draw_enabled:
            return
        now = time.time()
        with self._draw_lock:
            if now - self._last_draw_at < self.settings.draw_min_interval_seconds:
                log.info("画图太频繁，这次跳过：%s", prompt[:40])
                return
            self._last_draw_at = now
        threading.Thread(
            target=self._draw_and_send,
            args=(user_id, session, prompt),
            daemon=True,
        ).start()

    def _draw_and_send(self, user_id: int, session: str, prompt: str) -> None:
        try:
            data = draw_mod.generate(self.settings, self.llm, prompt)
            if not data:
                log.info("画图没成功：%s", prompt[:40])
                return
            path = draw_mod.save(self.settings.draw_dir, data)
            if path is None:
                return
            ok = self.sender.send_private_image(
                user_id, path, self.settings.image_send_mode
            )
            self.store.save_message(
                session,
                "assistant",
                "[图画好了]" if ok else "[图没发出去]",
                image_tags=[path.name],
                origin="draw",
            )
            log.info("画好一张图：%s", path.name)
        except Exception:  # noqa: BLE001 - 画图挂了不能影响聊天
            log.exception("画图异常")



    # ---------------- 看图 ----------------
    def _describe_images(self, message: IncomingMessage) -> str:
        """把这次消息里的图看明白，拼成一句话塞进上下文。"""
        parts: list[str] = []
        for image in message.images[:3]:
            try:
                data = self._fetch_image(image)
            except Exception:  # noqa: BLE001 - 下载失败就跳过这张
                log.exception("下载图片失败")
                data = b""
            if not data or len(data) > self.settings.image_max_bytes:
                continue
            self._save_inbox(data)
            sticker = image.sub_type == 1
            try:
                note = vision_mod.note_for(self.store, self.llm, data, sticker=sticker)
            except Exception:  # noqa: BLE001 - 看图失败不能拖垮对话
                log.exception("看图失败")
                note = ""
            if note:
                parts.append(vision_mod.sentence(note, sticker=sticker))
        return "\n".join(parts)

    def _fetch_image(self, image: Any) -> bytes:
        url = (getattr(image, "url", "") or "").strip()
        if url.startswith(("http://", "https://")):
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.read(self.settings.image_max_bytes + 1)
        local = Path(getattr(image, "file", "") or "")
        if local.is_file():
            return local.read_bytes()[: self.settings.image_max_bytes + 1]
        return b""

    def _save_inbox(self, data: bytes) -> Path | None:
        """收件箱：他发来的图留一份，方便回看（也占用自己的盘，不进 C 盘）。"""
        sniffed = sniff_image(data)
        if not sniffed:
            return None
        digest = hashlib.sha256(data).hexdigest()
        day = time.strftime("%Y-%m-%d", time.localtime())
        target = self.settings.inbox_dir / day / f"{digest[:16]}{sniffed[1]}"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(data)
        except OSError:
            log.exception("写入收件箱失败")
            return None
        return target

    # ---------------- 主动消息 ----------------
    def send_proactive(self, session: str, text: str) -> bool:
        """她先开口。走和普通回复一样的清洗、分段、配图流程。"""
        log.info("主动开口（%s）：%s", session, text[:60])
        return self._send_reply(int(session), session, text, origin="proactive")

    def resend(self, user_id: int, text: str) -> bool:
        """重放一条发送失败的回复。

        只补文本、不补图片：历史行里只存了文件名，拼路径容易发错图，
        宁可少一张图，也不能把别人的图发出去。
        """
        # 补发不参与随机并条/切分：存下来是什么样就补什么样，不然条数对不上
        parts = split_bubbles(
            text, self.settings.reply_max_chars, self.settings.reply_max_bubbles
        ) or [text]
        with self._send_lock:
            for index, part in enumerate(parts):
                if index:
                    self._sleep(self.settings.segment_delay_ms / 1000)
                if not self.sender.send_private(user_id, part):
                    return False
        return True

