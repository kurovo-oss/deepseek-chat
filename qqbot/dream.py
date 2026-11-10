"""做梦（M2 P3）：夜里把这一天的对话揉进记忆。

产出两样东西：

1. **记忆本**（`data/journal.md`）——一段读得下去的叙事，进 system prompt，
   让卡片之外的"连贯记忆"也能被带出来；
2. **卡片操作**——只允许 `add` / `merge` / `reweight`，**永远不 delete**。

几条规矩：

- 只处理「上次成功做梦之后」的消息，不重复嚼旧饭；
- 一次模型调用，解析失败就整轮放弃（下次补跑），不做半截；
- 每个操作都校验：id 必须存在、summary 截断、importance 夹在 0~1；
- 没新消息也记一条 dream_log（ok=1），免得每分钟重试一次空轮。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from .memory import crossed_day

from . import facts as facts_mod
from . import growth as growth_mod
from . import memory_epoch
from . import reflect as reflect_mod

log = logging.getLogger("qqbot")

OP_ADD = "add"
OP_MERGE = "merge"
OP_REWEIGHT = "reweight"
ALLOWED_KINDS = ("identity", "preference", "fact", "event", "chat")
MAX_SUMMARY = 40
MAX_JOURNAL = 4000
MAX_CARDS_INLINE = 200
# 一次做梦最多吃这么多消息：超过就分批，游标按 message id 前进。
# 以前一次只取 400 条又把 last_dream_at 推到当前时间，第 401 条之后永远进不了梦。
DREAM_BATCH = 400
MAX_DREAM_BATCHES = 10
DREAM_CURSOR = "dream_message_id"

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _parse_hhmm(spec: str) -> tuple[int, int]:
    try:
        hour, minute = str(spec).strip().split(":")[:2]
        return int(hour) % 24, int(minute) % 60
    except (ValueError, IndexError):
        log.warning("DREAM_AT 写法看不懂（%r），按 23:30 处理", spec)
        return 23, 30


def _today_at(spec: str, now: int) -> int:
    """今天这个钟点的时间戳。"""
    hour, minute = _parse_hhmm(spec)
    moment = datetime.fromtimestamp(now).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    return int(moment.timestamp())


def parse_dream(raw: str) -> dict | None:
    """从模型输出里抠出 JSON；抠不出来返回 None（整轮放弃）。"""
    text = (raw or "").strip()
    if not text:
        return None
    match = _JSON_BLOCK.search(text)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


class Dreamer:
    def __init__(self, store, llm, settings, clock=time.time) -> None:
        self.store = store
        self.llm = llm
        self.settings = settings
        self._clock = clock
        self._prompt = ""

    # ---------------- 触发 ----------------
    def due(self, now: int | None = None) -> bool:
        """今天还该不该做梦。

        两种该做：今天的目标钟点过了还没做；或者距离上次超过 20 小时
        （说明整晚没开机，启动时补一次）。
        """
        if not self.settings.dream_enabled:
            return False
        stamp = int(now if now is not None else self._clock())
        last = self.store.last_dream_at()
        if last is None:
            return True
        target = _today_at(self.settings.dream_at, stamp)
        if last >= target:
            return False
        return stamp >= target or (stamp - last) >= 20 * 3600

    # ---------------- 主体 ----------------
    def _cursor_after(self) -> int:
        """上次处理到哪条消息。老库没有游标时，用上次做梦时间反推。"""
        try:
            after = int(self.store.get_cursor(DREAM_CURSOR, "0") or 0)
        except (TypeError, ValueError):
            after = 0
        if after > 0:
            return after
        last = self.store.last_dream_at()
        if last is None:
            return 0
        return self.store.last_message_id_before(last)

    def dream(self, now: int | None = None) -> dict:
        """做一次梦，返回结果摘要（也用于 /dream 回复）。"""
        stamp = int(now if now is not None else self._clock())
        journal_file = self.settings.journal_file
        dream_id = self.store.start_dream(stamp)

        after_id = self._cursor_after()
        old_journal = self._read_journal()
        current_journal = old_journal
        counts = {OP_ADD: 0, OP_MERGE: 0, OP_REWEIGHT: 0}
        seen = 0
        batches = 0

        cutoff = memory_epoch.read(self.settings.base_dir)
        while batches < MAX_DREAM_BATCHES:
            messages = self.store.messages_after_id(after_id, DREAM_BATCH)
            if not messages:
                break
            if cutoff:
                raw = messages
                messages = [row for row in raw if int(row.get("created_at") or 0) >= cutoff]
                if not messages:
                    # 旧消息不再参与新的记忆生成，但游标要往前走，避免每晚重扫。
                    after_id = int(raw[-1]["id"])
                    self.store.set_cursor(DREAM_CURSOR, str(after_id))
                    continue

            cards = self.store.list_cards(status="active", limit=MAX_CARDS_INLINE)
            prompt = (
                self._load_prompt()
                .replace("{{JOURNAL}}", current_journal or "（还没有）")
                .replace("{{CARDS}}", _format_cards(cards))
                .replace("{{MESSAGES}}", _format_messages(messages))
            )

            try:
                # max_tokens 必须给足，而且要比"写多少字"大很多：
                # deepseek-flash 是推理模型，推理 token 也算在这个额度里。
                # 真实踩过两次——1600 全被推理吃掉，finish_reason=length、正文 0 字。
                result = self.llm.chat(
                    [{"role": "user", "content": prompt}],
                    temperature=0.4,
                    max_tokens=6000,
                    purpose="dream",
                )
            except Exception as exc:  # noqa: BLE001 - 做梦失败不该影响任何东西
                log.warning("做梦失败（模型调用）：%s", exc)
                self.store.finish_dream(
                    dream_id, finished_at=stamp, messages_seen=seen,
                    ok=False, note=str(exc)[:200],
                )
                return {"ok": False, "messages": seen, "note": str(exc)[:80]}

            raw = getattr(result, "text", "") or ""
            data = parse_dream(raw)
            if data is None:
                note = "模型没返回正文（推理占满额度）" if not raw.strip() else "输出不是 JSON"
                log.warning("做梦失败（%s）：%r", note, raw[:200])
                self.store.finish_dream(
                    dream_id, finished_at=stamp, messages_seen=seen,
                    ok=False, note=note,
                )
                return {"ok": False, "messages": seen, "note": note}

            batch_counts = self._apply(data.get("ops"), stamp)
            for key in counts:
                counts[key] += batch_counts[key]

            journal = str(data.get("journal") or "").strip()[:MAX_JOURNAL]
            if journal:
                current_journal = journal
                self._write_journal(journal_file, current_journal)

            after_id = int(messages[-1]["id"])
            self.store.set_cursor(DREAM_CURSOR, str(after_id))
            seen += len(messages)
            batches += 1
            if len(messages) < DREAM_BATCH:
                break

        if seen == 0:
            self.store.finish_dream(
                dream_id, finished_at=stamp, ok=True, note="没有新消息"
            )
            log.info("做梦：没有新消息，跳过")
            return {"ok": True, "messages": 0, "added": 0, "merged": 0,
                    "reweight": 0, "chars": 0, "note": "没有新消息"}

        journal_chars = len(current_journal) if current_journal != old_journal else 0
        self.store.finish_dream(
            dream_id,
            finished_at=stamp,
            messages_seen=seen,
            cards_added=counts[OP_ADD],
            cards_merged=counts[OP_MERGE],
            cards_reweight=counts[OP_REWEIGHT],
            journal_chars=journal_chars,
            ok=True,
        )
        log.info(
            "做梦完成：%d 条消息（%d 批），新增 %d，合并 %d，调权 %d，记忆本 %d 字",
            seen,
            batches,
            counts[OP_ADD],
            counts[OP_MERGE],
            counts[OP_REWEIGHT],
            journal_chars,
        )
        return {
            "ok": True,
            "messages": seen,
            "added": counts[OP_ADD],
            "merged": counts[OP_MERGE],
            "reweight": counts[OP_REWEIGHT],
            "chars": journal_chars,
        }

    # ---------------- 应用操作 ----------------
    def _apply(self, ops, now: int) -> dict:
        counts = {OP_ADD: 0, OP_MERGE: 0, OP_REWEIGHT: 0}
        if not isinstance(ops, list):
            return counts

        existing = None
        for item in ops[:20]:  # 一轮最多 20 条操作，防模型吐一大堆
            if not isinstance(item, dict):
                continue
            op = str(item.get("op") or "").strip().lower()

            if op == OP_ADD:
                summary = str(item.get("summary") or "").strip()[:MAX_SUMMARY]
                if not summary:
                    continue
                kind = str(item.get("kind") or "fact").strip().lower()
                if kind not in ALLOWED_KINDS:
                    kind = "fact"
                if existing is None:
                    existing = {
                        card["summary"]: card
                        for card in self.store.list_cards(limit=500)
                    }
                prior = existing.get(summary)
                if prior is not None:  # 已经是知道的事，不重复加
                    if crossed_day(prior.get("created_at"), now):
                        # 隔天又被总结出来一次，可以转正
                        self.store.set_confirmed(int(prior["id"]), True)
                        log.info("记忆转正：%s（隔天又总结出来一次）", summary)
                    continue
                card_id = self.store.add_card(
                    kind=kind,
                    summary=summary,
                    detail=str(item.get("detail") or "")[:200],
                    importance=_clamp(item.get("importance"), 0.5),
                    confirmed=False,  # 做梦推出来的，等她慢慢确认
                )
                existing[summary] = self.store.get_card(card_id) or {
                    "id": card_id,
                    "summary": summary,
                    "created_at": now,
                }
                counts[OP_ADD] += 1
                continue

            if op == OP_MERGE:
                source = _as_int(item.get("from"))
                target = _as_int(item.get("into"))
                if source is None or target is None:
                    continue
                if self.store.merge_cards(source, target):
                    counts[OP_MERGE] += 1
                continue

            if op == OP_REWEIGHT:
                card_id = _as_int(item.get("id"))
                if card_id is None or self.store.get_card(card_id) is None:
                    continue
                self.store.update_card(
                    card_id, importance=_clamp(item.get("importance"), 0.5)
                )
                counts[OP_REWEIGHT] += 1

        return counts

    # ---------------- 记忆本 ----------------
    def _read_journal(self) -> str:
        try:
            return self.settings.journal_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    @staticmethod
    def _write_journal(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text + "\n", encoding="utf-8")
        tmp.replace(path)  # 原子替换：写一半断电也不会留下半截记忆本

    def _load_prompt(self) -> str:
        if self._prompt:
            return self._prompt
        try:
            self._prompt = self.settings.dream_prompt_file.read_text(encoding="utf-8")
        except OSError:
            log.warning("做梦提示词读不到：%s", self.settings.dream_prompt_file)
            self._prompt = "只输出 JSON：{{\"journal\": \"\", \"ops\": []}}\n{{JOURNAL}}\n{{CARDS}}\n{{MESSAGES}}"
        return self._prompt

    # ---------------- 线程 ----------------
    def run(self, stop_event: threading.Event) -> None:
        interval = self.settings.dream_check_seconds
        while not stop_event.is_set():
            try:
                if self.settings.dream_enabled and self.due():
                    self.dream()
                # 睡前反省：一天一次，写完顺手把旧事件淡忘掉。
                # 关掉做梦也得跑——人格不能跟着做梦一起停
                bedtime(self.store, self.llm, self.settings)
            except Exception:  # noqa: BLE001 - 做梦线程不能死
                log.exception("做梦线程异常")
            if stop_event.wait(interval):
                break


def _clamp(value, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return max(0.0, min(1.0, number))


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_cards(cards: list[dict]) -> str:
    if not cards:
        return "（还没有卡片）"
    return "\n".join(
        f"{card['id']}. [{card.get('kind', 'fact')}] "
        f"{card['summary']}（重要度 {card.get('importance', 0.5):.2f}，"
        f"被想起 {card.get('hit_count', 0)} 次）"
        for card in cards
    )


def _format_messages(messages: list[dict]) -> str:
    lines = []
    for item in messages:
        text = str(item.get("content") or "").replace("\n", " ")[:200]
        if item.get("role") == "user" and text.strip().startswith("/"):
            continue  # 指令不是聊天内容：“他也会用 /reset”这种叙事很怪
        who = "他" if item.get("role") == "user" else "我"
        lines.append(f"{who}：{text}")
    return "\n".join(lines) or "（这一天没有聊天，只有指令）"


def bedtime(store, llm, settings) -> bool:
    """睡前反省 + 抽事实 + 淡忘。跟 DREAM_ENABLED 无关，单独可测。"""
    did = False
    if reflect_mod.due(store, settings):
        reflect_mod.reflect(store, llm, settings)
        growth_mod.maintain(store)
        did = True
    if facts_mod.due(store, settings):
        facts_mod.extract(store, llm, settings)
        facts_mod.maintain(store)
        did = True
    return did


def start_dreamer(engine, store, settings, stop_event: threading.Event) -> threading.Thread:
    dreamer = Dreamer(store, engine.llm, settings)
    engine.dreamer = dreamer
    thread = threading.Thread(
        target=dreamer.run, args=(stop_event,), name="dreamer", daemon=True
    )
    thread.start()
    return thread
