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
from . import facts as facts_mod
from . import douyin as douyin_mod
from . import facts as facts_mod
from . import growth as growth_mod
from . import guard as guard_mod
from . import memepedia as memepedia_mod
from . import qzone as qzone_mod
from . import images as images_mod
from . import browse as browse_mod
from . import stickers as stickers_mod
from . import life as life_mod
from . import voice as voice_mod
from . import projects as projects_mod
from . import search as search_mod
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

        if head in ("/记忆", "/facts"):
            return self._facts_command(rest)

        if head in ("/空间", "/qzone"):
            return self._qzone_command(rest)

        if head in ("/抖音", "/douyin"):
            return self._douyin_command(rest)

        if head in ("/健康", "/health"):
            return self._health_command()

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

        if head in ("/表情", "/sticker"):
            return self._sticker_command(rest, session)
        if head in ("/刷", "/browse"):
            return self._browse_command()
        if head in ("/voice", "/语音"):
            return self._voice_command(rest, session)
        if head == "/life":
            return self._life_command(rest)

        if head in ("/查", "/search"):
            return search_mod.command(rest)
        if head in ("/成长", "/grow", "/反思"):
            return self._growth_command()

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

    def _gate(self):
        gate = getattr(self, "guard", None)
        if gate is None:
            gate = guard_mod.Guard(self.store, self.settings)
            self.guard = gate
        return gate

    def _douyin_command(self, rest: str) -> str:
        rest = (rest or "").strip()
        if rest in ("登录", "login", "扫码"):
            try:
                return douyin_mod.open_for_login(self.settings)
            except Exception as exc:  # noqa: BLE001 - 浏览器起不来要说人话
                return f"浏览器没起来：{exc}"
        if rest in ("停", "关闭", "stop"):
            return "浏览器关掉了。" if douyin_mod.stop_browser(self.settings) else "现在没在跑。"
        gate = self._gate()
        state = gate.status().get("douyin", {})
        lines = [
            f"抖音：{'开' if self.settings.douyin_enabled else '关'}"
            f"（试运行 {'开' if self.settings.douyin_dry_run else '关'}）",
            f"今天点赞 {state.get('used_today', 0)}/{state.get('daily_quota', 0)}，"
            f"分享额度 {self.settings.douyin_share_daily_max}",
        ]
        rows = [row for row in gate.recent(30) if row["module"].startswith("douyin")][:3]
        for row in rows:
            lines.append(f"- {time.strftime('%m-%d %H:%M', time.localtime(row['at']))} {row['module']} {row['result']}")
        return "\n".join(lines)

    def _health_command(self) -> str:
        gate = self._gate()
        state = gate.status()
        lines = ["【健康】"]
        sender = getattr(self, "sender", None)
        if sender is not None and hasattr(sender, "request"):
            try:
                ok, data = sender.request("get_login_info", {})
                nickname = (data or {}).get("nickname") or ""
                lines.append(f"协议端：{'在线' if ok else '离线'}" + (f"（{nickname}）" if nickname else ""))
            except Exception:  # noqa: BLE001
                lines.append("协议端：探测失败")
        else:
            lines.append("协议端：未知")
        space = state.get("qzone", {})
        lines.append(f"空间：今天 {space.get('used_today', 0)}/{space.get('daily_quota', 0)}"
                     + ("，熔断中" if space.get("circuit_open") else ""))
        look = state.get("douyin", {})
        lines.append(f"抖音：{'开' if look.get('enabled') else '关'}，今天 {look.get('used_today', 0)}/{look.get('daily_quota', 0)}")
        try:
            by = facts_mod.stats(self.store).get("by_status") or {}
            lines.append(f"记忆：active {by.get('active', 0)} 条，忘了 {by.get('forgotten', 0)} 条")
        except Exception:  # noqa: BLE001
            pass
        try:
            lines.append(f"梗库：{memepedia_mod.count(self.store)} 条")
        except Exception:  # noqa: BLE001
            pass
        try:
            posts = qzone_mod.last_posts(self.store, 1)
            if posts:
                row = posts[0]
                lines.append(f"最近一条空间：{row['status']}（{time.strftime('%m-%d %H:%M', time.localtime(row['created_at']))}）")
        except Exception:  # noqa: BLE001
            pass
        return "\n".join(lines)

    def _qzone_command(self, rest: str) -> str:
        guard = getattr(self, "guard", None)
        if guard is None:
            guard = guard_mod.Guard(self.store, self.settings)
            self.guard = guard
        rest = (rest or "").strip()
        if rest in ("记录", "历史", "列表"):
            rows = qzone_mod.last_posts(self.store, 5)
            if not rows:
                return "空间还没发过什么呢。"
            lines = ["【最近的空间】"]
            for row in rows:
                when = time.strftime("%m-%d %H:%M", time.localtime(row["created_at"]))
                lines.append(f"- {when} {row['status']}：{row['content'][:40]}")
            return "\n".join(lines)
        matter = qzone_mod.draft(self.store, self.llm, self.settings)
        if not matter["text"]:
            return "今天没什么想写的……"
        if rest in ("发", "发布", "send"):
            result = qzone_mod.publish(
                self.store, guard, self.sender, matter["text"], matter["image"], reason="手动"
            )
            head = {"ok": "发出去了", "dry_run": "试运行：只存了草稿", "denied": "没发", "failed": "发送失败"}[result["status"]]
            return f"{head}（{result['detail']}）\n---\n{matter['text']}"
        image = f"\n配图：{matter['image'].name}" if matter["image"] else "\n不配图"
        return f"【空间草稿】\n{matter['text']}{image}\n（/空间 发 才会真发）"

    def _facts_command(self, rest: str) -> str:
        rest = (rest or "").strip()
        if not rest or rest in ("统计", "stats"):
            data = facts_mod.stats(self.store)
            by = data.get("by_status") or {}
            lines = ["【我记住的事】"]
            lines.append(
                "active %d 条 / 被顶掉 %d / 忘了 %d"
                % (by.get("active", 0), by.get("superseded", 0), by.get("forgotten", 0))
            )
            for who, count in (data.get("by_subject") or {}).items():
                lines.append(f"关于{who}：{count} 条")
            lines.append("用法：/记忆 香菜   /记忆 来源 12   /记忆 更正 12 新的说法   /记忆 忘了 12")
            return "\n".join(lines)

        if rest.startswith("来源") or rest.startswith("看 "):
            fact_id = _first_int(rest)
            if fact_id is None:
                return "要给我一条编号呀，比如 /记忆 来源 12"
            row = facts_mod.get(self.store, fact_id)
            if not row:
                return f"没有 #f{fact_id} 这条。"
            lines = [
                f"#f{fact_id} {row['subject']}{row['predicate']}{row['object']}",
                f"（{row['status']}，置信度 {row['confidence']:.1f}）",
            ]
            if row.get("quote"):
                lines.append(f"原话：“{row['quote']}”")
            if row.get("source_msg_id"):
                lines.append(f"来源消息：{row['source_msg_id']}")
            for event in facts_mod.events(self.store, fact_id):
                lines.append(f"- {event['action']}：{event['note'] or '（无备注）'}")
            return "\n".join(lines)

        if rest.startswith("更正"):
            fact_id = _first_int(rest)
            new_text = rest.split(None, 2)[-1] if len(rest.split(None, 2)) > 2 else ""
            if fact_id is None or not new_text or new_text == rest:
                return "用法：/记忆 更正 12 他其实不讨厌香菜了"
            old_row = facts_mod.get(self.store, fact_id)
            if not old_row:
                return f"没有 #f{fact_id} 这条。"
            new_id = facts_mod.correct(self.store, fact_id, new_text)
            return f"改了：#f{fact_id} → #f{new_id}「{new_text}」（旧的没删，标成被顶掉了）"

        if rest.startswith("忘了") or rest.startswith("忘掉"):
            fact_id = _first_int(rest)
            if fact_id is None:
                return "要给我一条编号呀，比如 /记忆 忘了 12"
            if not facts_mod.forget(self.store, fact_id):
                return f"没有 #f{fact_id} 这条，或者已经忘了。"
            return f"好，#f{fact_id} 我当没这回事了（记录还留着，能找回来）。"

        rows = facts_mod.recall(self.store, rest, limit=5)
        if not rows:
            return f"关于「{rest}」我还没记住什么呢。"
        lines = [f"关于「{rest}」："]
        for row in rows:
            quote = f"，他说“{row['quote']}”" if row.get("quote") else ""
            lines.append(f"- #f{row['id']} {row['subject']}{row['predicate']}{row['object']}{quote}")
        return "\n".join(lines)

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

    def _sticker_command(self, rest: str, session: str) -> str:
        """发一张表情：/表情 委屈，或者 /表情 让她自己按心情挑。"""
        if not (self.settings.image_enabled and self.settings.sticker_chance > 0):
            return "表情包这块关着。"
        name = rest.strip()
        if name and name not in stickers_mod.BUCKETS:
            bucket = stickers_mod.bucket_from_text(name) or "卖萌"
        else:
            bucket = name
        if not bucket:
            values = self.mood.snapshot() if self.mood is not None else {}
            bucket = stickers_mod.bucket_from_mood(values) or "卖萌"
        if not self._send_sticker(int(session), session, bucket):
            return f"「{bucket}」这桶里还没有表情，先往 data\\stickers\\{bucket} 丢两张吧。"
        return f"发了一张「{bucket}」的表情"

    def _browse_command(self) -> str:
        """强制刷一次，看看她真能刷到什么。"""
        if not self.settings.web_enabled:
            return "上网这块关着（WEB_ENABLED=false）。"
        item = browse_mod.collect(self.settings)
        if item is None:
            return "没刷到，可能没网。"
        return f"刷到一条：{item.as_line()}"

    def _voice_command(self, rest: str, session: str) -> str:
        """发一条语音试试：他不用等"情绪到了"才能验收。"""
        if not self.settings.voice_enabled:
            return "语音关着（VOICE_ENABLED=false）。"
        text = rest.strip() or "嗯……我在呢，听着呢"
        path = voice_mod.synthesize(text, self.settings)
        if path is None:
            return "这次没合成出来，可能是没网或者 edge-tts 不在。"
        if not self.sender.send_private_record(int(session), path):
            return "合成好了，但没发出去，看看协议端日志。"
        return "发了一条语音，听得到吗"

    def _growth_command(self) -> str:
        """让她自己说说：最近想通了什么、拒绝过什么。"""
        if self.store is None:
            return "还没有可以翻的东西呢。"
        try:
            beliefs = growth_mod.beliefs(self.store)
            thoughts = growth_mod.self_view(self.store)
            refusals = growth_mod.recent_refusals(self.store, limit=3)
        except Exception:  # noqa: BLE001 - 看看而已，读坏了不算事
            log.exception("读事件流失败")
            return "我这边翻不开小本本，等一下呀。"

        lines: list[str] = []
        if beliefs:
            lines.append("【我已经认定的事】")
            lines.extend(f"- {item}" for item in beliefs if item not in thoughts)
        if thoughts:
            lines.append("【最近想通的】")
            lines.extend(f"- {item}" for item in thoughts)
        if refusals:
            lines.append("【我拒绝过】")
            for row in refusals:
                verdict = {
                    "rejected": "他有点不高兴",
                    "accepted": "他没生气",
                    "neutral": "他没说什么",
                }.get(str(row.get("outcome") or ""), "还没下文")
                ask = str(row.get("ask") or "")[:30]
                lines.append(f"- 他让我「{ask}」，我没做——{verdict}")
        if not lines:
            return "我还没长出什么想法呢，多跟我说说话呀。"
        return "\n".join(lines)

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
        hand = projects_mod.describe(projects_mod.load(self.settings))
        return (text or "今天还没写呢。") + "\n\n" + hand



def _first_int(text: str) -> int | None:
    match = re.search(r"\d+", text or "")
    return int(match.group(0)) if match else None
