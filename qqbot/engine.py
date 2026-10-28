"""编排层：白名单 → 幂等 → 指令 → 组 prompt → 调模型 → 落库 → 分段发送。

引擎不碰网络细节，也不碰 SQL：它只依赖 Store / LLMClient / Sender 三个接口，
所以测试里可以全部替换成假实现。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .config import Settings
from .formatting import extract_image_tags, split_message, strip_markdown
from .llm import LLMError
from .onebot import IncomingMessage, Sender, parse_event
from .store import Store

FALLBACK_REPLY = "唔…我这会儿有点转不过来，等一下再跟我说好不好？"

KIND_LABEL = {"fact": "事实", "preference": "偏好", "event": "事件"}

HELP_TEXT = "我会这些呀：\n/reset 清空刚才的对话\n/memory 看看我记得什么\n/forget 关键词 忘掉某件事\n/status 运行状态"


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
    ) -> None:
        self.settings = settings
        self.store = store
        self.llm = llm
        self.sender = sender
        self.system_prompt = system_prompt
        self.images = images
        self._sleep = sleep

    # ---------------- 入口 ----------------
    def handle_payload(self, payload: Any) -> bool:
        message = parse_event(payload)
        if message is None:
            return False
        self.handle_message(message)
        return True

    def handle_message(self, message: IncomingMessage) -> None:
        # 白名单：非本人消息一律静默，不回复、不落库
        if message.user_id != self.settings.allow_user_id:
            return

        text = message.text.strip()
        if not text:
            return

        # 幂等：同一 qq_msg_id 只处理一次
        if message.message_id and self.store.has_qq_msg_id(message.message_id):
            return

        session = str(message.user_id)
        self.store.save_message(
            session, "user", text, qq_msg_id=message.message_id or None
        )

        command_reply = self._handle_command(text, session)
        if command_reply is not None:
            self._send_reply(message.user_id, session, command_reply)
            # /reset 的游标要在"回复本身"落库之后再定，否则重置后的
            # 第一条消息会把 /reset 的应答也带进上下文
            if text.lower().startswith("/reset"):
                top = self.store.max_message_id(session)
                self.store.set_cursor(self._window_key(session), str(top))
            return

        self._send_reply(message.user_id, session, self._chat(session))

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
                f"记忆：{self.store.count_memories()} 条\n"
                f"tokens：{tokens_in} in / {tokens_out} out"
            )

        if head == "/memory":
            memories = self.store.list_memories(limit=20)
            if not memories:
                return "我还没记住什么呢，多聊聊就记住啦。"
            lines = [
                f"{i}. [{KIND_LABEL.get(m['kind'], m['kind'])}] {m['content']}"
                for i, m in enumerate(memories, start=1)
            ]
            return "我记得这些事：\n" + "\n".join(lines)

        if head == "/forget":
            if not rest:
                return "要忘掉哪件事呀？跟我说个关键词嘛。"
            removed = self.store.delete_memories_matching(rest)
            if removed:
                return f"唔…删掉 {removed} 条关于「{rest}」的记忆了。"
            return f"我好像没有关于「{rest}」的记忆呀。"

        if head in ("/help", "/?"):
            return HELP_TEXT

        return "唔…这个指令我不认识呀。\n" + HELP_TEXT

    # ---------------- 对话 ----------------
    def _chat(self, session: str) -> str:
        messages = self._build_messages(session)
        try:
            result = self.llm.chat(messages)
        except LLMError:
            self.store.log_usage(self.settings.model_id, 0, 0, "chat", ok=False)
            return FALLBACK_REPLY
        self.store.log_usage(result.model, result.tokens_in, result.tokens_out, "chat", True)
        return result.text or FALLBACK_REPLY

    def _build_messages(self, session: str) -> list[dict]:
        window_start = int(self.store.get_cursor(self._window_key(session), "0") or 0)
        limit = self.settings.history_max_turns * 2
        history = self.store.recent_messages_after(session, window_start, limit)

        budget = self.settings.window_char_budget
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

        messages = [{"role": "system", "content": self.system_prompt}]
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
            path = self.images.resolve(tag)
            if path is None:
                continue
            try:
                if path.stat().st_size > self.settings.image_max_bytes:
                    continue
            except OSError:
                continue
            return path
        return None

    def _send_reply(self, user_id: int, session: str, raw_text: str) -> None:
        cleaned = strip_markdown(raw_text or "")
        cleaned, tags = extract_image_tags(cleaned)

        image_path = self._pick_image(session, tags)
        parts = split_message(cleaned, self.settings.reply_max_chars)
        if not parts and image_path is None:
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

    @staticmethod
    def _window_key(session: str) -> str:
        return f"window_start:{session}"
