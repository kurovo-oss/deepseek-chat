"""上下文组装：把历史、记忆、状态拼成模型吃的 messages。

从 engine.py 拆出来的——这块只读不写，调 prompt 的时候只看这一个文件。
"""

from __future__ import annotations

import logging
import time

from . import abilities as abilities_mod
from . import budget as budget_mod
from . import digest as digest_mod
from . import life as life_mod
from . import memes as memes_mod
from . import presence as presence_mod
from . import recap as recap_mod
from . import samples as samples_mod
from . import talkstate as talkstate_mod
from .formatting import fix_address
from .sayings import RECALL_TITLE

log = logging.getLogger("qqbot")


class ContextMixin:

    def _busy_note(self, session: str) -> str:
        """忙完之后回的那一条：先交代一句刚在干嘛。"""
        if not self.settings.presence_enabled or self._pending_count(session) < 2:
            return ""
        activity = presence_mod.describe(self._presence_state())
        return (
            f"【你刚才在忙】你{activity}，手机搁一边了，他连着发了几条。"
            "先说一句刚在干嘛（别太正式、别提手机），再接他的话。"
        )

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

        # system 不再是字符串一路拼：每段声明优先级和配额，由预算统一分配
        sections: list[budget_mod.Section] = [
            budget_mod.Section("人设", self.system_prompt, priority=1000, fixed=True)
        ]
        if self.memory is not None:
            context = self.memory.context()
            if context:
                sections.append(budget_mod.Section("记忆", context, priority=60, quota=800))
        recall = self._recall_section(selected)
        if recall:
            sections.append(budget_mod.Section("回忆", recall, priority=55, quota=450))

        digest = digest_mod.recent_text(self.settings)
        if digest:
            sections.append(budget_mod.Section("摘要", digest, priority=50, quota=350))

        if self.settings.recap_enabled:
            recap_note = recap_mod.note(self.settings, session)
            if recap_note:
                sections.append(budget_mod.Section("前情", recap_note, priority=65, quota=220))

        if self.settings.life_enabled:
            life_note = life_mod.snippet(self.settings)
            if life_note:
                sections.append(budget_mod.Section("日子", life_note, priority=70, quota=280))

        # 她会什么、不会什么——他问"你有没有这个功能"时，她得有事实可依
        sections.append(budget_mod.Section("能力", abilities_mod.block(), priority=45, quota=300))

        # 网络用语只给意思，不给"怎么用"：听得懂，但自己不说
        sections.append(budget_mod.Section("梗", memes_mod.block(), priority=40, quota=360))

        if self.mood is not None:
            state = self.mood.describe()
            if state:
                sections.append(budget_mod.Section("情绪", state, priority=80, quota=140))

        busy_note = self._busy_note(session)
        if busy_note:
            sections.append(budget_mod.Section("忙", busy_note, priority=88, quota=120))

        if self.settings.talkstate_enabled:
            talk_note = talkstate_mod.note(
                [
                    {"role": row["role"], "content": row["content"]}
                    for row in self.store.recent_messages(session, 6)
                ]
            )
            if talk_note:
                sections.append(budget_mod.Section("对话状态", talk_note, priority=90, quota=220))

        fitted = budget_mod.fit(sections, self.settings.context_token_budget)
        if fitted.trimmed or fitted.dropped:
            log.info(
                "上下文压缩：%d tokens；压缩 %s；丢弃 %s",
                fitted.tokens,
                "、".join(fitted.trimmed) or "无",
                "、".join(fitted.dropped) or "无",
            )
        system = fitted.text

        # 历史也按 token 算：system 占完剩下的才是它的
        cap = budget_mod.history_budget(self.settings.context_token_budget, fitted.tokens)

        # 开场样品排在历史前面，占的也是历史的额度；额度不够就整段不要
        samples = samples_mod.messages(self.settings.sample_pairs)
        sample_tokens = sum(budget_mod.estimate_tokens(item["content"]) for item in samples)
        if sample_tokens + 200 > cap:
            samples = []
            sample_tokens = 0
        cap -= sample_tokens

        while selected and sum(budget_mod.estimate_tokens(i["content"]) for i in selected) > cap:
            selected.pop(0)
        while selected and selected[0]["role"] != "user":
            selected.pop(0)

        messages = [{"role": "system", "content": system}, *samples]
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

    def _quoted_text(self, message_id: str) -> str:
        """取被引用那条消息的文本。取不到就当没引用，别让整条回复失败。"""
        if not self.settings.quote_enabled or not message_id:
            return ""
        getter = getattr(self.sender, "get_message", None)
        if getter is None:
            return ""
        try:
            text = getter(str(message_id)) or ""
        except Exception:  # noqa: BLE001 - 引用是加分项，失败不影响对话
            log.exception("取引用消息失败：%s", message_id)
            return ""
        return talkstate_mod.phrase(str(text), limit=60)

    def _image_context_hint(self, session: str, record_id: int) -> str:
        """图片的说明后面跟一句上下文提示：图是接话的，不是让她描述画面。"""
        rows = [
            row for row in self.store.recent_messages(session, 6) if row.get("id") != record_id
        ]
        tail: list[str] = []
        for row in rows:
            content = (row.get("content") or "").strip().replace("\n", " ")
            # 跳过图片本身的说明和占位符：把它们拼进上下文会变成自己引用自己
            if not content or content.startswith("（他发来一张") or content.startswith("[表情]"):
                continue
            who = "他" if row["role"] == "user" else "你"
            tail.append(f"{who}：{content[:50]}")
        hint = "（他在接上文，别只描述画面，直接接话）"
        if tail:
            hint += "刚才：" + "；".join(tail[-2:])
        return hint

    @staticmethod
    def _window_key(session: str) -> str:
        return f"window_start:{session}"
