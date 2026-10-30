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
from . import life as life_mod
from . import pc as pc_mod
from . import slang as slang_mod
from . import vision as vision_mod
from .config import Settings
from .export import export_all
from .formatting import (
    extract_draw_tags,
    fix_address,
    extract_image_tags,
    split_bubbles,
    split_message,
    strip_markdown,
)
from .images import sniff_image
from .llm import LLMError
from .memory import KIND_LABEL, card_weight
from .onebot import IncomingMessage, Sender, parse_event
from .store import Store

log = logging.getLogger("qqbot")

FALLBACK_REPLIES = (
    "唔…我这会儿脑子有点转不过来，等一下再说好不好？",
    "等下，刚刚走神了，你再说一遍",
    "啊…我这边卡了一下，等我一下",
    "唔，我好像没听清，再说一次嘛",
)
FALLBACK_REPLY = FALLBACK_REPLIES[0]  # 兼容旧引用
RECALL_TITLE = "# 他以前说过的原话"


def fallback_reply() -> str:
    """兜底话术也要换着说：同一句连着出现四次，比不说话还像机器人。"""
    return random.choice(FALLBACK_REPLIES)


def _size_text(size: int) -> str:
    if size >= 1024 * 1024:
        return "%.1f MB" % (size / (1024 * 1024))
    return "%.0f KB" % (size / 1024)

HELP_TEXT = (
    "我会这些呀：\n"
    "/reset 清空刚才的对话\n"
    "/memory 看看我记得什么（add 加一条、edit 改一条、ok 编号 确认）\n"
    "/forget 编号 忘掉某件事\n"
    "/pin 编号 把某件事记牢一点\n"
    "/journal 看看记忆本\n"
    "/dream 手动整理一次记忆\n"
    "/mood 看看我现在的心情\n"
    "/life 看她今天在干嘛（/life 换 重新生成）\n"
    "/screen 让她看一眼你的屏幕\n"
    "/pc 让她看看你电脑的状态（/pc 详细 看原始数据）\n"
    "/digest 看今天/昨天/这周留下了什么\n"
    "/export 把聊天和记忆导成一个文件\n"
    "/tag 名字 给刚存的表情包打标\n"
    "/img list 看看图库\n"
    "/img clean 清掉回收来的旧图\n"
    "/img retag 给没标签的表情包补标\n"
    "/draw 一只猫 让她画一张（测画图用）\n"
    "/proactive 让她主动说一句\n"
    "/events 看她记下的事\n"
    "/event 2026-11-04 09:00 面试 记一件事\n"
    "/status 运行状态"
)


class Engine:
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
        record_id = self.store.save_message(
            session, "user", text or "[表情]", qq_msg_id=message.message_id or None
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
                enriched = f"{text}\n{note}".strip() if text else note
                self.store.update_message_content(record_id, enriched)

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

        if not text and not note:
            self._send_reply(message.user_id, session, self._sticker_ack(collected))
            return

        command_reply = self._handle_command(text, session)
        if command_reply is not None:
            # 指令回复是我们自己写的，不能当模型输出做 Markdown 清洗：
            # 否则 "/img list" 的编号（"7. ..."）会被当成有序列表剥掉
            self._send_reply(
                message.user_id, session, command_reply, sanitize=False
            )
            # /reset 的游标要在"回复本身"落库之后再定，否则重置后的
            # 第一条消息会把 /reset 的应答也带进上下文
            if text.lower().startswith("/reset"):
                top = self.store.max_message_id(session)
                self.store.set_cursor(self._window_key(session), str(top))
            return

        reply = self._chat(session)
        self._send_reply(message.user_id, session, reply)
        if self.memory is not None and reply not in FALLBACK_REPLIES:
            # 抽取与压缩发生在回复发出之后，不拖慢响应。
            # 兜底话术不喂给抽取：那不是她说的话，抽出来的卡片全是垃圾。
            self.memory.note_reply(session, text)

    # ---------------- 指令 ----------------
    def _handle_command(self, text: str, session: str) -> str | None:
        if not text.startswith("/"):
            return None

        head, _, rest = text.partition(" ")
        head = head.lower()
        rest = rest.strip()

        if head == "/reset":
            return "好，我把刚才的对话忘掉啦。\n不过你的事我还是记得的呀。"

        if head == "/status":
            tokens_in, tokens_out = self.store.usage_total()
            return (
                "运行状态\n"
                f"模型：{self.settings.model_id}\n"
                f"消息：{self.store.count_messages(session)} 条\n"
                f"卡片：{self.store.count_cards()} 张\n"
                f"tokens：{tokens_in} in / {tokens_out} out"
            )

        if head == "/memory":
            return self._memory_command(rest)

        if head == "/forget":
            return self._forget(rest)

        if head in ("/pin", "/unpin"):
            return self._set_pin(rest, pinned=head == "/pin")

        if head == "/journal":
            journal = self.memory.journal() if self.memory is not None else ""
            if not journal:
                return "我还没整理过记忆呢，夜里做梦的时候会写下来。"
            return "我记着的这些：\n" + journal

        if head == "/dream":
            return self._dream_now()

        if head == "/digest":
            return self._digest_command(rest)

        if head == "/export":
            return self._export_command()

        if head == "/screen":
            return self._screen_command(session)

        if head == "/pc":
            return self._pc_command(rest)

        if head == "/life":
            return self._life_command(rest)

        if head == "/mood":
            if self.mood is None:
                return "我现在没什么特别的感觉。"
            return self.mood.line()

        if head == "/tag":
            return self._tag_latest(rest)

        if head == "/img":
            return self._img_command(rest)

        if head == "/draw":
            prompt = rest.strip()
            if not prompt:
                return "格式：/draw 一只猫在窗台上打瞌睡"
            if not self.settings.draw_enabled:
                return "画图关着（DRAW_ENABLED=false）。"
            self._schedule_draw(int(session), session, prompt)
            return "好，我画一张，画好直接发你。"

        if head == "/proactive":
            return self._try_proactive()

        if head == "/events":
            return self._list_events()

        if head == "/event":
            return self._event_command(rest)

        if head in ("/help", "/?"):
            return HELP_TEXT

        return "唔…这个指令我不认识呀。\n" + HELP_TEXT

    # ---------------- 记忆指令 ----------------
    def _memory_command(self, rest: str) -> str:
        """记忆本：查看 / 手动加一条 / 改一条。

        自动抽取会记错、会漏；能改才是"记忆本"，不能改只是个公告板。
        """
        raw = (rest or "").strip()
        if not raw:
            return self._list_cards()

        sub, _, args = raw.partition(" ")
        sub = sub.lower()

        if sub == "add":
            content = args.strip()
            if not content:
                return "要记什么呀？发 /memory add 他不吃香菜"
            card_id = self.store.add_card(
                kind="fact", summary=content, importance=0.6
            )
            return f"记下来啦（第 {card_id} 条）：{content}"

        if sub in ("ok", "confirm"):
            target = args.strip()
            if not target.isdigit():
                return "格式：/memory ok 编号"
            card = self.store.get_card(int(target))
            if card is None:
                return f"我这儿没有第 {target} 条记忆呀。"
            self.store.set_confirmed(int(target), True)
            return f"好，这条我记牢了：{card['summary']}"

        if sub in ("edit", "set"):
            target, _, content = args.strip().partition(" ")
            if not target.isdigit() or not content.strip():
                return "格式：/memory edit 编号 新的内容"
            if self.store.get_card(int(target)) is None:
                return f"我这儿没有第 {target} 条记忆呀。"
            summary = content.strip()
            self.store.update_card(int(target), summary=summary)
            return f"改好了：{summary}"

        return self._list_cards()

    def _list_cards(self) -> str:
        cards = self.store.list_cards(status="active", limit=300)
        if not cards:
            return "我还没记住什么呢，多聊聊就记住啦。"

        now = time.time()
        half_life = self.settings.decay_half_life_days
        scored = sorted(
            cards, key=lambda card: card_weight(card, now, half_life), reverse=True
        )[:20]

        lines = []
        has_pending = False
        for card in scored:
            weight = card_weight(card, now, half_life)
            label = KIND_LABEL.get(str(card.get("kind")), "记忆")
            pinned = "·钉" if card.get("pinned") else ""
            pending = "" if card.get("confirmed", True) else "·待确认"
            has_pending = has_pending or bool(pending)
            lines.append(
                f"{card['id']}. [{label}] {card['summary']}（{weight:.2f}{pinned}{pending}）"
            )
        text = "我记得这些事：\n" + "\n".join(lines)
        if has_pending:
            text += "\n\n带·待确认的是我猜的，发 /memory ok 编号 我才会当真。"
        return text

    def _forget(self, rest: str) -> str:
        if not rest:
            return "要忘掉哪件事呀？把编号或关键词告诉我。"

        target, _, flag = rest.partition(" ")
        confirmed = flag.strip().lower() == "confirm"

        if target.isdigit():
            card = self.store.get_card(int(target))
            if card is None:
                return f"我这儿没有第 {target} 条记忆呀。"
            if not confirmed:
                return f"要删掉「{card['summary']}」吗？\n确定就发 /forget {target} confirm"
            self.store.delete_card(int(target))
            return "删掉了，这件事我不记得了。"

        # 关键词删除是批量操作，必须二次确认
        if not confirmed:
            return f"确认忘掉所有关于「{target}」的记忆，就发 /forget {target} confirm"
        removed = self.store.delete_cards_matching(target)
        if removed:
            return f"删掉 {removed} 条关于「{target}」的记忆了。"
        return f"我好像没有关于「{target}」的记忆呀。"

    def _dream_now(self) -> str:
        """手动整理一次记忆（夜里也会自动做，这是调试用的入口）。"""
        dreamer = self.dreamer
        if dreamer is None:
            return "做梦还没准备好呢。"
        try:
            result = dreamer.dream()
        except Exception as exc:  # noqa: BLE001 - 手动触发也不能崩
            log.exception("手动做梦失败")
            return f"这次没整理成：{exc}"

        if not result.get("ok"):
            return f"这次没整理成：{result.get('note') or '不知道怎么了'}"
        if not result.get("messages"):
            return "这会儿没什么要整理的呢。"
        return (
            f"整理完啦：看了 {result['messages']} 条消息，"
            f"新记 {result['added']} 条、合并 {result['merged']} 条、"
            f"调了 {result['reweight']} 条权重，记忆本 {result['chars']} 字。"
        )

    def _digest_command(self, rest: str) -> str:
        keyword = rest.strip().lower()
        if keyword in ("周", "一周", "上周", "week"):
            return digest_mod.week_text(self.settings)

        today = digest_mod.day_of(time.time())
        if keyword in ("昨天", "yesterday"):
            target = today - timedelta(days=1)
        elif keyword in ("", "今天", "today"):
            target = today
        else:
            return "用法：/digest（今天）、/digest 昨天、/digest 周"

        text = digest_mod.read(self.settings, target)
        if text is None:
            try:
                text = digest_mod.generate(self.store, self.llm, self.settings, target)
            except Exception as exc:  # noqa: BLE001 - 生成失败也别崩
                log.exception("写日摘要失败")
                return f"这天没写出来：{exc}"
        if text is None:
            return f"{target.strftime('%m-%d')} 这天没说什么呢。"
        return text

    def _export_command(self) -> str:
        try:
            path, stats = export_all(self.store, self.settings)
        except Exception as exc:  # noqa: BLE001 - 导出失败不影响聊天
            log.exception("导出失败")
            return f"没导出成：{exc}"
        return (
            f"导出好了：{path}\n"
            f"消息 {stats['messages']} 条、卡片 {stats['cards']} 张、"
            f"事件 {stats['events']} 个、摘要 {stats['summaries']} 篇。"
        )

    def _set_pin(self, rest: str, pinned: bool) -> str:
        target = rest.strip()
        if not target.isdigit():
            return "要钉哪一条呀？先发 /memory 看看编号。"
        card = self.store.get_card(int(target))
        if card is None:
            return f"我这儿没有第 {target} 条记忆呀。"
        self.store.set_pinned(int(target), pinned)
        if pinned:
            return f"记住了，「{card['summary']}」我会一直记着。"
        return f"「{card['summary']}」不用一直记着了。"

    # ---------------- 图库指令 ----------------
    @staticmethod
    def _split_tags(raw: str) -> list[str]:
        return [tag.lower() for tag in re.split(r"[\s,，、]+", raw or "") if tag.strip()][:5]

    def _tag_latest(self, rest: str) -> str:
        latest = self.store.latest_image(source="user")
        if latest is None:
            return "我还没存过表情包呀，先发一张给我？"
        tags = self._split_tags(rest)
        if not tags:
            current = "、".join(latest["tags"]) or "无标签"
            return (
                f"最近存的是第 {latest['id']} 张，现在是：{current}。\n"
                f"要打标就发 /tag 名字，可以给多个。"
            )
        self.store.tag_image(latest["id"], tags)
        return f"好，第 {latest['id']} 张标成「{'、'.join(tags)}」了。"

    def _img_command(self, rest: str) -> str:
        parts = (rest or "").split()
        sub = parts[0].lower() if parts else "list"

        if sub in ("list", "ls"):
            tag = parts[1].lower() if len(parts) > 1 else None
            rows = self.store.list_images(tag=tag, limit=10)
            if not rows:
                return "图库里没有这张呀。" if tag else "图库还是空的呢。"
            lines = [
                f"{row['id']}. {'、'.join(row['tags']) or '无标签'}"
                f"（用 {row['use_count']} 次{'，内置' if row['source'] == 'builtin' else ''}）"
                for row in rows
            ]
            return "图库里这些：\n" + "\n".join(lines)

        if sub == "rm":
            if len(parts) < 2 or not parts[1].isdigit():
                return "要删哪一张？发 /img rm 编号。"
            image_id = int(parts[1])
            if self.store.get_image(image_id) is None:
                return f"图库里没有第 {image_id} 张呀。"
            self.store.delete_image(image_id)
            return f"第 {image_id} 张删掉了。"

        if sub == "clean":
            removed, freed = self._clean_archived_images()
            if not removed:
                return "图库没有要回收的旧图呢。"
            return f"收回 {removed} 张旧图（{_size_text(freed)}），磁盘松快点了。"

        if sub == "tag":
            if len(parts) < 3 or not parts[1].isdigit():
                return "格式：/img tag 编号 名字，可以给多个名字。"
            image_id = int(parts[1])
            if self.store.get_image(image_id) is None:
                return f"图库里没有第 {image_id} 张呀。"
            tags = self._split_tags(" ".join(parts[2:]))
            self.store.tag_image(image_id, tags)
            return f"好，第 {image_id} 张标成「{'、'.join(tags)}」了。"

        if sub in {"retag", "补标"}:
            if self.collector is None or not self.settings.vision_enabled:
                return "现在看不到图（看图关着），补标做不了。"
            checked, tagged, failed = self.collector.retag_all(
                self.settings.image_dir, limit=20
            )
            if not checked:
                return "图库里没有没标签的图，都标过了。"
            return f"看了 {checked} 张，补上 {tagged} 张标签，{failed} 张没把握。"

        return (
            "图库指令：/img list [tag]、/img tag 编号 名字、/img rm 编号、"
            "/img clean、/img retag（给没标签的补标）"
        )

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

    def _try_proactive(self) -> str:
        hook = self.proactive_trigger
        if hook is None:
            return "主动消息没开呀。"
        status = hook()
        return {
            "sent": "好，那我自己先开口了。",
            "skipped": "唔，我想了想，这次还是不说了。",
            "failed": "想说话，但是没发出去。",
        }.get(status, "试了一下。")

    _EVENT_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
    _EVENT_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
    EVENT_DEFAULT_HOUR = 19

    def _list_events(self) -> str:
        events = self.store.list_events(status="pending", limit=10)
        if not events:
            return "我还没记下什么要做的事呀。"
        lines = ["我记着这些："]
        for index, event in enumerate(events, 1):
            stamp = datetime.fromtimestamp(int(event["trigger_at"])).strftime(
                "%m-%d %H:%M"
            )
            lines.append(f"{index}. {stamp} {event['content']}")
        lines.append("发 /event rm 编号 可以删掉。")
        return "\n".join(lines)

    def _event_command(self, rest: str) -> str:
        rest = (rest or "").strip()
        if not rest:
            return "用法：/event 2026-11-04 09:00 面试 ｜ /event now 试试 ｜ /event rm 1"
        head, _, tail = rest.partition(" ")
        head = head.strip()
        tail = tail.strip()

        if head == "rm":
            return self._remove_event(tail)
        if head == "now":
            if not tail:
                return "要告诉我是什么事呀。"
            self.store.add_scheduled_event(tail[:40], int(time.time()))
            return f"好，记下了：{tail}"
        return self._add_event(head, tail)

    def _remove_event(self, raw_index: str) -> str:
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            return "要给我编号呀，用 /events 看。"
        events = self.store.list_events(status="pending", limit=50)
        if not 1 <= index <= len(events):
            return "没有这个编号呀。"
        event = events[index - 1]
        self.store.delete_proactive_event(int(event["id"]))
        return f"好，删掉「{event['content']}」了。"

    def _add_event(self, date_token: str, remainder: str) -> str:
        match = self._EVENT_DATE_RE.match(date_token)
        if not match:
            return "日期要写成 2026-11-04 这样呀。"
        year, month, day = (int(part) for part in match.groups())

        hour, minute = self.EVENT_DEFAULT_HOUR, 0
        first = remainder.split(" ", 1)[0] if remainder else ""
        time_match = self._EVENT_TIME_RE.match(first)
        if time_match:
            hour, minute = int(time_match.group(1)), int(time_match.group(2))
            remainder = remainder.split(" ", 1)[1].strip() if " " in remainder else ""

        if not remainder:
            return "还要告诉我是什么事呀。"

        try:
            moment = datetime(year, month, day, hour, minute)
        except ValueError:
            return "这个日期不太对呀。"

        self.store.add_scheduled_event(remainder[:40], int(moment.timestamp()))
        return f"好，我记住了：{moment:%m-%d %H:%M} {remainder}"

    # ---------------- 对话 ----------------
    def _chat(self, session: str) -> str:
        messages = self._build_messages(session)
        try:
            result = self.llm.chat(messages)
        except LLMError as exc:
            log.warning("对话失败，改用兜底话术：%s", exc)
            self.store.log_usage(self.settings.model_id, 0, 0, "chat", ok=False)
            return fallback_reply()
        self.store.log_usage(result.model, result.tokens_in, result.tokens_out, "chat", True)
        if not result.text:
            log.warning("模型返回空内容，改用兜底话术")
            return fallback_reply()
        # 真人不在聊天框里空行；模型爱用它分段，压成单换行
        return re.sub(r"\n{2,}", "\n", result.text)

    def _build_messages(self, session: str) -> list[dict]:
        window_start = int(self.store.get_cursor(self._window_key(session), "0") or 0)
        limit = self.settings.history_max_turns * 2
        history = self.store.recent_messages_after(session, window_start, limit)

        budget = self.settings.context_hard_char_budget
        selected: list[dict] = []
        used = 0
        for item in reversed(history):
            cost = len(item["content"])
            if selected and used + cost > budget:
                break
            selected.append(item)
            used += cost
        selected.reverse()

        # 保证窗口从用户消息开始，避免出现"助手自问自答"
        while selected and selected[0]["role"] != "user":
            selected.pop(0)

        system = self.system_prompt
        if self.memory is not None:
            context = self.memory.context()
            if context:
                system = f"{system}\n\n{context}"
        recall = self._recall_section(selected)
        if recall:
            system = f"{system}\n\n{recall}"

        digest = digest_mod.recent_text(self.settings)
        if digest:
            system = f"{system}\n\n{digest}"

        if self.settings.life_enabled:
            life_note = life_mod.snippet(self.settings)
            if life_note:
                system = f"{system}\n\n{life_note}"

        if self.settings.slang_enabled:
            system = f"{system}\n\n{slang_mod.block()}"

        if self.mood is not None:
            state = self.mood.describe()
            if state:
                system = f"{system}\n\n{state}"

        messages = [{"role": "system", "content": system}]
        for item in selected:
            content = item["content"]
            # 她自己说过的旧话里可能还带"爸爸"，改掉再喂回去，否则模型会学着叫
            if item["role"] != "user":
                content = fix_address(content)
            messages.append({"role": item["role"], "content": content})
        return messages

    def _recall_section(self, history: list[dict]) -> str:
        """从原话里捞几句相关的。

        卡片记的是"结论"，这句补的是"他当时怎么说的"——
        Paramecium 那套思路：原文才是唯一真相，摘要只是索引。
        """
        if not self.settings.recall_enabled or not history:
            return ""
        last_user = next((m for m in reversed(history) if m.get("role") == "user"), None)
        if last_user is None:
            return ""
        try:
            rows = self.store.recall_messages(
                str(last_user.get("content") or ""),
                limit=self.settings.recall_max_messages,
                exclude_ids={m.get("id") for m in history},
            )
        except Exception:  # noqa: BLE001 - 召回是加分项，失败就当没想起来
            log.exception("原话召回失败")
            return ""
        if not rows:
            return ""

        lines = [RECALL_TITLE]
        for row in rows:
            stamp = time.strftime("%m-%d", time.localtime(row.get("created_at") or 0))
            text = str(row.get("content") or "").replace("\n", " ")[:80]
            lines.append(f"[{stamp}] {text}")
        return "\n".join(lines)

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

    def _life_command(self, rest: str) -> str:
        """看看/重写她今天的日子。写这个是为了能马上验证生活线有没有跑。"""
        if not self.settings.life_enabled:
            return "生活线关着（LIFE_ENABLED=false）。"
        if rest.strip() in ("换", "重来", "刷新"):
            path = Path(self.settings.life_dir) / (time.strftime("%Y-%m-%d") + ".md")
            try:
                path.unlink()
            except OSError:
                pass
            with self._life_lock:
                self._life_day = ""
            text = life_mod.today(self.settings, self.llm)
            return ("今天改成这样了：\n" + text) if text else "没写出来，回头再试。"
        text = life_mod.snippet(self.settings)
        return text or "今天还没写呢。"

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

    @staticmethod
    def _window_key(session: str) -> str:
        return f"window_start:{session}"
