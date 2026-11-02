"""她回的那些斜杠指令。

从 engine.py 拆出来的：指令是读写记忆的入口，和聊天主流程无关，
单独一个文件，坏了也好定位。
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import digest as digest_mod
from . import images as images_mod
from . import life as life_mod
from .export import export_all
from .memory import KIND_LABEL, card_weight
from .sayings import HELP_TEXT
from . import usage as usage_mod

log = logging.getLogger("qqbot")


class CommandsMixin:

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
                f"tokens：{tokens_in} in / {tokens_out} out\n"
                f"上下文：{usage_mod.describe(self.settings)}（预算 {self.settings.context_token_budget}）"
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

        if head == "/watch":
            return self._watch_command(rest, session)

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
