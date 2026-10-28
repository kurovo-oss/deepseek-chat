"""编排层：白名单 → 幂等 → 指令 → 组 prompt → 调模型 → 落库 → 分段发送。

引擎不碰网络细节，也不碰 SQL：它只依赖 Store / LLMClient / Sender 三个接口，
所以测试里可以全部替换成假实现。
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from .config import Settings
from .formatting import extract_image_tags, split_message, strip_markdown
from .llm import LLMError
from .memory import KIND_LABEL, card_weight
from .onebot import IncomingMessage, Sender, parse_event
from .store import Store

log = logging.getLogger("qqbot")

FALLBACK_REPLY = "唔…我这会儿有点转不过来，等一下再跟我说好不好？"

HELP_TEXT = (
    "我会这些呀：\n"
    "/reset 清空刚才的对话\n"
    "/memory 看看我记得什么\n"
    "/forget 编号 忘掉某件事\n"
    "/pin 编号 把某件事记牢一点\n"
    "/tag 名字 给刚存的表情包打标\n"
    "/img list 看看图库\n"
    "/proactive 让她主动说一句\n"
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
        self._sleep = sleep

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

        # 幂等：同一 qq_msg_id 只处理一次
        if message.message_id and self.store.has_qq_msg_id(message.message_id):
            log.info("重复消息，跳过：qq_msg_id=%s", message.message_id)
            return

        session = str(message.user_id)

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

        self.store.save_message(
            session, "user", text or "[表情]", qq_msg_id=message.message_id or None
        )

        if not text:
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

        self._send_reply(message.user_id, session, self._chat(session))
        if self.memory is not None:
            # 抽取与压缩发生在回复发出之后，不拖慢响应
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
            return self._list_cards()

        if head == "/forget":
            return self._forget(rest)

        if head in ("/pin", "/unpin"):
            return self._set_pin(rest, pinned=head == "/pin")

        if head == "/tag":
            return self._tag_latest(rest)

        if head == "/img":
            return self._img_command(rest)

        if head == "/proactive":
            return self._try_proactive()

        if head in ("/help", "/?"):
            return HELP_TEXT

        return "唔…这个指令我不认识呀。\n" + HELP_TEXT

    # ---------------- 记忆指令 ----------------
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
        for card in scored:
            weight = card_weight(card, now, half_life)
            label = KIND_LABEL.get(str(card.get("kind")), "记忆")
            pinned = "·钉" if card.get("pinned") else ""
            lines.append(f"{card['id']}. [{label}] {card['summary']}（{weight:.2f}{pinned}）")
        return "我记得这些事：\n" + "\n".join(lines)

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

        if sub == "tag":
            if len(parts) < 3 or not parts[1].isdigit():
                return "格式：/img tag 编号 名字，可以给多个名字。"
            image_id = int(parts[1])
            if self.store.get_image(image_id) is None:
                return f"图库里没有第 {image_id} 张呀。"
            tags = self._split_tags(" ".join(parts[2:]))
            self.store.tag_image(image_id, tags)
            return f"好，第 {image_id} 张标成「{'、'.join(tags)}」了。"

        return "图库指令：/img list [tag]、/img tag 编号 名字、/img rm 编号"

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

    # ---------------- 对话 ----------------
    def _chat(self, session: str) -> str:
        messages = self._build_messages(session)
        try:
            result = self.llm.chat(messages)
        except LLMError as exc:
            log.warning("对话失败，改用兜底话术：%s", exc)
            self.store.log_usage(self.settings.model_id, 0, 0, "chat", ok=False)
            return FALLBACK_REPLY
        self.store.log_usage(result.model, result.tokens_in, result.tokens_out, "chat", True)
        if not result.text:
            log.warning("模型返回空内容，改用兜底话术")
            return FALLBACK_REPLY
        return result.text

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
            block = self.memory.block()
            if block:
                system = f"{system}\n\n{block}"

        messages = [{"role": "system", "content": system}]
        messages.extend({"role": m["role"], "content": m["content"]} for m in selected)
        return messages

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
        self, user_id: int, session: str, raw_text: str, sanitize: bool = True
    ) -> bool:
        if sanitize:
            cleaned = strip_markdown(raw_text or "")
        else:
            # 指令回复：只去首尾空白，编号和格式原样保留
            cleaned = (raw_text or "").strip()
        cleaned, tags = extract_image_tags(cleaned)

        image_path = self._pick_image(session, tags)
        parts = split_message(cleaned, self.settings.reply_max_chars)
        if not parts and image_path is None:
            log.warning(
                "回复内容为空（原文 %r，图片标记 %s），改用兜底话术", raw_text[:80], tags
            )
            parts = [FALLBACK_REPLY]

        content = cleaned if cleaned else ("[图片]" if image_path else " ".join(parts))
        record_id = self.store.save_message(
            session,
            "assistant",
            content,
            send_state="pending",
            image_tags=[image_path.name] if image_path else [],
        )

        all_ok = True
        for index, part in enumerate(parts):
            if index:
                self._sleep(self.settings.segment_delay_ms / 1000)
            if not self.sender.send_private(user_id, part):
                all_ok = False

        if image_path is not None:
            if not self.sender.send_private_image(
                user_id, image_path, self.settings.image_send_mode
            ):
                all_ok = False

        if record_id is not None:
            self.store.update_send_state(record_id, "sent" if all_ok else "failed")
        return all_ok

    # ---------------- 主动消息 ----------------
    def send_proactive(self, session: str, text: str) -> bool:
        """她先开口。走和普通回复一样的清洗、分段、配图流程。"""
        log.info("主动开口（%s）：%s", session, text[:60])
        return self._send_reply(int(session), session, text)

    @staticmethod
    def _window_key(session: str) -> str:
        return f"window_start:{session}"
