"""上下文组装：把历史、记忆、状态拼成模型吃的 messages。

从 engine.py 拆出来的——这块只读不写，调 prompt 的时候只看这一个文件。
"""

from __future__ import annotations

import logging
import time

from . import abilities as abilities_mod
from . import browse as browse_mod
from . import budget as budget_mod
from . import digest as digest_mod
from . import facts as facts_mod
from . import exemplars as exemplars_mod
from . import growth as growth_mod
from . import life as life_mod
from . import memepedia as memepedia_mod
from . import memory_epoch
from . import memes as memes_mod
from . import persona as persona_mod
from . import recap as recap_mod
from . import refusal as refusal_mod
from . import mood as mood_mod
from . import samples as samples_mod
from . import selfknow as selfknow_mod
from . import talkstate as talkstate_mod
from . import turnplan as turnplan_mod
from . import usage as usage_mod
from .formatting import fix_address
from .onebot import IncomingMessage
from .sayings import RECALL_TITLE

log = logging.getLogger("qqbot")


class ContextMixin:

    def _build_messages(
        self, session: str, allow_refusal: bool = True, proactive: bool = False
    ) -> list[dict]:
        window_start = int(self.store.get_cursor(self._window_key(session), "0") or 0)
        limit = self.settings.history_max_turns * 2
        history = self.store.recent_messages_after(session, window_start, limit)
        cutoff = memory_epoch.read(self.settings.base_dir)
        if cutoff:
            history = [row for row in history if int(row.get("created_at") or 0) >= cutoff]

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

        last_user_text = next(
            (str(m["content"]) for m in reversed(selected) if m["role"] == "user"), ""
        )
        mood_values = self.mood.snapshot() if self.mood is not None else {}
        # 主动开口不是"接他这句"，别把上一轮的计划贴上去
        turn_plan = turnplan_mod.make_plan(
            "" if proactive else last_user_text, selected, mood_values
        )

        # system 不再是字符串一路拼：每段声明优先级和配额，由预算统一分配
        sections: list[budget_mod.Section] = [
            budget_mod.Section("人设", self.system_prompt, priority=1000, fixed=True)
        ]
        if getattr(self.settings, "persona_v2_enabled", True):
            overlay = (
                ""
                if proactive
                else persona_mod.block(turn_plan.intent, mood_values, self.settings.base_dir)
            )
            if overlay:
                sections.append(
                    budget_mod.Section("这轮的样子", overlay, priority=94, quota=160)
                )
            if getattr(self.settings, "turnplan_enabled", True) and not proactive:
                sections.append(
                    budget_mod.Section("这一轮", turn_plan.render(), priority=93, quota=180)
                )
            if getattr(self.settings, "relation_enabled", True):
                relation = persona_mod.relation_block(self.store, session)
                if relation:
                    sections.append(
                        budget_mod.Section("关系", relation, priority=78, quota=80)
                    )
        if self.memory is not None:
            context = self.memory.context(last_user_text)
            if context:
                sections.append(
                    budget_mod.Section(
                        "记忆",
                        context,
                        priority=60,
                        quota=int(getattr(self.settings, "card_inject_token_budget", 1500)),
                    )
                )
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
            repeat = life_mod.mentions_today(self.store, session, self.settings)
            life_note = life_mod.snippet(self.settings, repeat=repeat)
            if life_note:
                sections.append(budget_mod.Section("日子", life_note, priority=70, quota=280))

        # 她今天真刷到的东西（只有一条，且是真看过的）
        web_note = browse_mod.block(self.settings)
        if web_note:
            sections.append(budget_mod.Section("刷到", web_note, priority=68, quota=160))

        # 她会什么、不会什么——他问"你有没有这个功能"时，她得有事实可依
        sections.append(budget_mod.Section("能力", abilities_mod.block(self.settings), priority=45, quota=300))

        # 网络用语只给意思，不给"怎么用"：听得懂，但自己不说
        sections.append(budget_mod.Section("梗", memes_mod.block(), priority=40, quota=360))

        # 梗鲸的点子：他提到某个梗/某张图时，能说出台词和出处
        if getattr(self.settings, "memepedia_enabled", True) and self.store is not None:
            last_text = next(
                (str(m["content"]) for m in reversed(selected) if m["role"] == "user"), ""
            )
            meme_note = memepedia_mod.block(self.store, last_text)
            if meme_note:
                sections.append(budget_mod.Section("梗库", meme_note, priority=41, quota=240))

        # 她自己的真数字：模型、用量、跑了多久——问起来不至于瞎编
        mine = selfknow_mod.block(self.store, self.settings)
        if mine:
            sections.append(budget_mod.Section("关于你自己", mine, priority=79, quota=260))

        if self.mood is not None:
            state = self.mood.describe()
            if state:
                sections.append(budget_mod.Section("情绪", state, priority=80, quota=140))

        if self.settings.memory_facts_enabled and self.store is not None:
            last_text = next(
                (str(m["content"]) for m in reversed(selected) if m["role"] == "user"), ""
            )
            budget_chars = getattr(self.settings, "memory_fact_budget", 600)
            known = facts_mod.block(self.store, last_text, max_chars=budget_chars)
            if known:
                sections.append(
                    budget_mod.Section("我知道的事", known, priority=72, quota=budget_chars)
                )

        if self.settings.growth_enabled and self.store is not None:
            persona = growth_mod.persona_block(
                self.store, since=memory_epoch.read(self.settings.base_dir)
            )
            if persona:
                sections.append(budget_mod.Section("你自己", persona, priority=74, quota=320))

        # 主动开口不是"回复他这句请求"，别把拒绝指令塞进去
        self._pending_refusal = None
        if allow_refusal and self.settings.refusal_enabled and self.store is not None:
            last_user_text = next(
                (str(m["content"]) for m in reversed(selected) if m["role"] == "user"), ""
            )
            plan = refusal_mod.choose(
                self.store,
                self.settings,
                self.mood.snapshot() if self.mood is not None else {},
                last_user_text,
                self._rng,
            )
            if plan:
                self._pending_refusal = plan
                sections.append(
                    budget_mod.Section("这轮的心思", plan["plan"], priority=96, quota=220)
                )
            note = refusal_mod.open_note(self.store)
            if note:
                sections.append(budget_mod.Section("压着的事", note, priority=76, quota=160))

        if self.settings.talkstate_enabled:
            talk_note = talkstate_mod.note(
                [
                    {"role": row["role"], "content": row["content"]}
                    for row in self.store.recent_messages(session, 6)
                ]
            )
            if talk_note:
                sections.append(budget_mod.Section("对话状态", talk_note, priority=90, quota=220))

        # 估算有偏差时按真实比例收紧预算：估 1000 实际 1300，那预算也得按 1300 算
        ratio = usage_mod.ratio(self.settings)
        token_budget = int(self.settings.context_token_budget / ratio)
        fitted = budget_mod.fit(sections, token_budget)
        if fitted.trimmed or fitted.dropped:
            log.info(
                "上下文压缩：%d tokens；压缩 %s；丢弃 %s",
                fitted.tokens,
                "、".join(fitted.trimmed) or "无",
                "、".join(fitted.dropped) or "无",
            )
        system = fitted.text

        # 历史也按 token 算：system 占完剩下的才是它的
        cap = budget_mod.history_budget(token_budget, fitted.tokens)

        # 开场样品排在历史前面，占的也是历史的额度；额度不够就整段不要。
        # v2：按这一轮的场景选两组，别再每天用同样八组。
        samples: list[dict] = []
        if getattr(self.settings, "persona_v2_enabled", True):
            pairs = int(getattr(self.settings, "exemplar_pairs", 2) or 0)
            if pairs > 0:
                tag = persona_mod.pick_overlay(turn_plan.intent, mood_values)
                tags = [tag]
                if turn_plan.reference:
                    tags.append("reference")
                seed = int(time.time() // 3600) + len(selected)
                samples = exemplars_mod.messages(
                    self.settings.base_dir, tags, limit=pairs, seed=seed
                )
        if not samples:
            samples = samples_mod.messages(self.settings.sample_pairs)
        if self.mood is not None:
            # 情绪也体现在"样子里"：累的时候多给一组有气无力的，比讲规矩管用
            for said, answered in mood_mod.style_samples(self.mood.snapshot()):
                samples.append({"role": "user", "content": said})
                samples.append({"role": "assistant", "content": answered})
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
        cutoff = memory_epoch.read(self.settings.base_dir)
        if cutoff:
            rows = [row for row in rows if int(row.get("created_at") or 0) >= cutoff]
        if not rows:
            return ""

        lines = [RECALL_TITLE]
        for row in rows:
            stamp = time.strftime("%m-%d", time.localtime(row.get("created_at") or 0))
            text = str(row.get("content") or "").replace("\n", " ")[:80]
            lines.append(f"[{stamp}] {text}")
        return "\n".join(lines)

    def _quoted_text(self, message_id: str) -> str:
        """取被引用那条消息的文本（有图就顺手看一眼）。取不到就当没引用。"""
        if not self.settings.quote_enabled or not message_id:
            return ""
        getter = getattr(self.sender, "get_message", None)
        if getter is None:
            return ""
        text = ""
        detail: dict = {}
        try:
            text = str(getter(str(message_id)) or "")
            if self.settings.vision_enabled:
                richer = getattr(self.sender, "get_message_detail", None)
                detail = richer(str(message_id)) if richer is not None else {}
        except Exception:  # noqa: BLE001 - 引用是加分项，失败不影响对话
            log.exception("取引用消息失败：%s", message_id)
            return ""
        note = ""
        images = (detail or {}).get("images") or ()
        if images:
            # 引用的图只看一张：看多了又慢又贵，他多半也只在指那一张
            quoted = IncomingMessage(
                user_id=0, message_id=str(message_id), text="", raw={}, images=tuple(images[:1])
            )
            try:
                note = self._describe_images(quoted)
            except Exception:  # noqa: BLE001 - 看图失败就当没图
                log.exception("看引用的图失败")
        text = talkstate_mod.phrase(str(text), limit=60)
        if note:
            return f"{text}（图：{note}）" if text else f"（图：{note}）"
        return text

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
