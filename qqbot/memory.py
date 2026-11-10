"""长期记忆的纯逻辑层：权重、选卡、注入块、抽取结果解析。

这一层不做 IO（不碰 SQLite、不调网络），所以单测不需要任何假实现。
带 IO 的编排放在文件末尾的 Memory 类里。
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from datetime import datetime
from difflib import SequenceMatcher

from .formatting import fix_address
from . import memory_epoch

log = logging.getLogger("qqbot")

KIND_ORDER = ("identity", "preference", "fact", "event", "chat")
KIND_LABEL = {
    "identity": "身份",
    "preference": "偏好",
    "fact": "事实",
    "event": "事件",
    "chat": "琐事",
}
VALID_KINDS = frozenset(KIND_ORDER)
ALLOWED_OPS = frozenset({"add", "update"})

DEFAULT_IMPORTANCE = 0.5
MAX_OPS = 5
MAX_EVENTS = 2
DEFAULT_DATE_HOUR = 19
DEDUPE_RATIO = 0.8
MAX_SUMMARY_CHARS = 80
MAX_DETAIL_CHARS = 400
BLOCK_TITLE = "# 你记得的事（关于对方）"
PENDING_TITLE = "# 听来的、还没核实的（别当事实说）"
DAY_SECONDS = 86400


# ---------------------------------------------------------------- 基础工具
def _clamp(value, lo: float = 0.0, hi: float = 1.0, default: float = DEFAULT_IMPORTANCE) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(lo, min(hi, number))


def parse_timestamp(value) -> int | None:
    """接受 epoch 秒、'YYYY-MM-DD'、'YYYY/MM/DD'；其余一律当未知。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return int(datetime.strptime(text[:10], fmt).timestamp())
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- 权重
def card_weight(card: dict, now: float, half_life_days: float = 30.0) -> float:
    """使用频率 × 重要程度 × 时间衰减。

    钉住或 identity 类恒为 1.0——"她是 agent 创造的"这种事永远不该淡忘。
    """
    if card.get("pinned") or card.get("kind") == "identity":
        return 1.0

    importance = _clamp(card.get("importance"))
    anchor = card.get("occurred_at") or card.get("created_at") or now
    age_days = max(0.0, (float(now) - float(anchor)) / DAY_SECONDS)
    recency = 0.5 ** (age_days / max(0.001, half_life_days))

    hits = max(0, int(card.get("hit_count") or 0))
    freq = min(1.0, math.log1p(hits) / math.log1p(10))

    return importance * (0.6 + 0.4 * freq) * (0.2 + 0.8 * recency)


def _terms(text: str) -> set[str]:
    """中文按二元片段、英文按整词，够用来做轻量相关性。"""
    terms: set[str] = set()
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text or ""):
        for index in range(len(chunk) - 1):
            terms.add(chunk[index:index + 2])
        if len(chunk) == 1:
            terms.add(chunk)
    terms.update(
        word.lower() for word in re.findall(r"[A-Za-z0-9_]{2,}", text or "")
    )
    return terms


def select_cards(
    cards: list[dict],
    now: float,
    threshold: float = 0.15,
    char_budget: int = 2500,
    half_life_days: float = 30.0,
    query: str = "",
) -> list[dict]:
    """按权重降序取卡，直到字符预算用完。返回带 `_weight` 的副本。

    给了 query 时做一次轻量重排：和这句话有交集的卡往前放，
    但只占 30% 权重，避免一句闲聊把"她是谁"挤掉。
    """
    wanted = _terms(query) if query else set()
    scored = []
    for card in cards:
        weight = card_weight(card, now, half_life_days)
        if weight < threshold:
            continue
        score = weight
        if wanted:
            summary_terms = _terms(str(card.get("summary") or ""))
            overlap = len(wanted & summary_terms) / max(1, len(wanted))
            score = weight * (0.7 + 0.3 * min(1.0, overlap * 2))
        scored.append((score, weight, card))

    scored.sort(
        key=lambda pair: (
            pair[0],
            pair[1],
            _clamp(pair[2].get("importance")),
            pair[2].get("occurred_at") or pair[2].get("created_at") or 0,
        ),
        reverse=True,
    )

    selected: list[dict] = []
    used = 0
    for score, weight, card in scored:
        cost = len(str(card.get("summary") or "")) + 6
        if selected and used + cost > char_budget:
            break
        item = dict(card)
        item["_weight"] = weight
        item["_score"] = score
        selected.append(item)
        used += cost
    return selected


def build_block(cards: list[dict]) -> str:
    """生成注入用的记忆区块；没有卡片时返回空串（不占 token）。"""
    lines = [BLOCK_TITLE]
    for card in cards:
        summary = str(card.get("summary") or "").strip()
        if not summary:
            continue
        label = KIND_LABEL.get(str(card.get("kind")), "记忆")
        lines.append(f"[{label}] {summary}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def build_pending_block(cards: list[dict]) -> str:
    """待确认的卡片单独成块：标注清楚"这是猜的"，别让模型当事实用。"""
    lines = [PENDING_TITLE]
    for card in cards:
        summary = str(card.get("summary") or "").strip()
        if summary:
            lines.append(f"- {summary}")
    if len(lines) == 1:
        return ""
    lines.append("（这些都是听来的，可能不准，别当确定的事说）")
    return "\n".join(lines)


# ---------------------------------------------------------------- 抽取结果
def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left or "", right or "").ratio()


def find_duplicate(kind: str, summary: str, cards: list[dict], ratio: float = DEDUPE_RATIO):
    """同 kind 下找一条足够像的卡；找不到返回 None。"""
    best = None
    best_score = 0.0
    for card in cards:
        if str(card.get("kind")) != str(kind):
            continue
        score = similarity(summary, str(card.get("summary") or ""))
        if score >= ratio and score > best_score:
            best, best_score = card, score
    return best


def crossed_day(created_at: int | None, now: float) -> bool:
    """两次观察是不是隔了不同的日历天。

    "隔天又提到一次"才算数：同一天里翻来覆去说，可能只是随口一提。
    """
    if not created_at:
        return False
    first = datetime.fromtimestamp(int(created_at)).date()
    today = datetime.fromtimestamp(int(now)).date()
    return today > first


def load_json_object(raw: str) -> dict | None:
    """从模型输出里挖出第一个完整 JSON 对象，容忍 ```json 围栏与前后废话。"""
    text = (raw or "").strip()
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _normalize_entities(value) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text[:20])
        if len(result) >= 5:
            break
    return result


def parse_ops(raw: str, existing_ids=(), max_ops: int = MAX_OPS) -> list[dict]:
    """把模型输出解析成受控的操作列表。

    删改权限不在模型手里：`delete` / `archive` 之类一律丢弃。
    """
    data = load_json_object(raw)
    if not data:
        return []
    items = data.get("cards")
    if not isinstance(items, list):
        return []

    existing = {int(i) for i in existing_ids}
    ops: list[dict] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        op = str(item.get("op") or "add").strip().lower()
        if op not in ALLOWED_OPS:
            log.info("忽略不被允许的记忆操作：%s", op)
            continue

        kind = str(item.get("kind") or "").strip().lower()
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue

        if op == "add" and kind not in VALID_KINDS:
            continue
        if op == "update" and kind not in VALID_KINDS:
            kind = ""  # 只改摘要时允许不带 kind

        normalized = {
            "op": op,
            "kind": kind,
            "summary": summary[:MAX_SUMMARY_CHARS],
            "detail": str(item.get("detail") or "").strip()[:MAX_DETAIL_CHARS],
            "entities": _normalize_entities(item.get("entities")),
            "occurred_at": parse_timestamp(item.get("occurred_at")),
            "importance": _clamp(item.get("importance")),
        }

        if op == "update":
            try:
                card_id = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            if card_id not in existing:
                continue
            normalized["id"] = card_id

        ops.append(normalized)
        if len(ops) >= max_ops:
            break

    return ops


DUE_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
)


def parse_due_at(raw) -> int | None:
    """把模型给的时间字符串解析成时间戳；解析不出来返回 None。

    解析不了就丢掉——**猜时间比不提醒更糟**（会在错误的日子开口）。
    "是不是已经过去了"由调用方判断，这里只负责解析。
    """
    text = str(raw or "").strip().replace("Z", "")
    if not text:
        return None
    for fmt in DUE_FORMATS:
        try:
            moment = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d":
            moment = moment.replace(hour=DEFAULT_DATE_HOUR)
        return int(moment.timestamp())
    return None


def parse_events(raw: str, now: int, max_events: int = MAX_EVENTS) -> list[dict]:
    """解析模型给出的未来事件，只保留能落到具体时间的。

    这是主动消息里唯一"会自己找上门"的部分，宁可漏，不可错。
    """
    data = load_json_object(raw)
    if not data:
        return []
    items = data.get("events")
    if not isinstance(items, list):
        return []

    events: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue
        due_at = parse_due_at(item.get("due_at"))
        if due_at is None:
            log.info("丢弃解析不出时间的未来事件：%s", summary[:40])
            continue
        if due_at < now - 86400:
            continue  # 已经过去的事，不提醒
        events.append({"summary": summary[:MAX_SUMMARY_CHARS], "due_at": due_at})
        if len(events) >= max_events:
            break
    return events


# ---------------------------------------------------------------- 编排（带 IO）
class Memory:
    """把纯逻辑接到 Store 与 LLM 上：注入、抽取、压缩。"""

    def __init__(
        self,
        settings,
        store,
        llm,
        extract_prompt: str = "",
        compact_prompt: str = "",
        clock=time.time,
    ) -> None:
        self.settings = settings
        self.store = store
        self.llm = llm
        self.extract_prompt = extract_prompt
        self.compact_prompt = compact_prompt
        self._clock = clock

    # ---------------- 注入 ----------------
    def journal(self) -> str:
        """记忆本：做梦重写出来的叙事段落。

        卡片是"关键词式"的结构化记忆，记忆本是"讲得下去"的那部分。
        没做过梦就是空的，不算错误。
        """
        path = self.settings.journal_file
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
        budget = self.settings.journal_char_budget
        return text[:budget] if budget > 0 else ""

    def context(self, query: str = "") -> str:
        """进 system prompt 的全部记忆：卡片块 + 记忆本。"""
        parts = [self.block(query)]
        journal = self.journal()
        if journal:
            parts.append("【记忆本】\n" + journal)
        return fix_address("\n\n".join(part for part in parts if part))

    def block(self, query: str = "") -> str:
        if not self.settings.memory_enabled:
            return ""
        now = self._clock()
        cards = self.store.list_cards(status="active", limit=300)
        confirmed = [card for card in cards if card.get("confirmed", True)]
        pending = [card for card in cards if not card.get("confirmed", True)]

        parts: list[str] = []
        selected = select_cards(
            confirmed,
            now=now,
            threshold=self.settings.card_weight_threshold,
            char_budget=self.settings.card_inject_char_budget,
            half_life_days=self.settings.decay_half_life_days,
            query=query,
        )
        if selected:
            self.store.touch_cards([card["id"] for card in selected], int(now))
            parts.append(build_block(selected))

        # 待确认卡默认不进上下文：模型很容易把“听来的”当成事实。
        # 需要旧行为时显式打开 MEMORY_PENDING_INJECT=1。
        if getattr(self.settings, "memory_pending_inject", False):
            pending_selected = select_cards(
                pending,
                now=now,
                threshold=0.0,
                char_budget=400,
                half_life_days=self.settings.decay_half_life_days,
            )
            if pending_selected:
                parts.append(build_pending_block(pending_selected[:4]))

        return "\n\n".join(part for part in parts if part)

    # ---------------- 抽取 ----------------
    def note_reply(self, session: str, user_text: str = "") -> None:
        """回复发出后的维护：先抽取，再按需压缩。任何异常都不该影响对话。"""
        if not self.settings.memory_enabled:
            return
        try:
            self.extract(session, user_text)
        except Exception:  # noqa: BLE001 - 记忆是附加功能，绝不能拖垮主流程
            log.exception("记忆抽取失败")
        try:
            self.compact(session)
        except Exception:  # noqa: BLE001
            log.exception("上下文压缩失败")

    def _should_skip(self, user_text: str) -> bool:
        text = (user_text or "").strip()
        return text.startswith("/") or len(text) < 8

    def extract(self, session: str, user_text: str = "") -> int:
        if not self.settings.extract_enabled or not self.extract_prompt:
            return 0
        if self._should_skip(user_text):
            return 0

        cards = self.store.list_cards(status="active", limit=80)
        recent = self.store.recent_messages(session, 6)
        cutoff = memory_epoch.read(self.settings.base_dir)
        if cutoff:
            recent = [row for row in recent if int(row.get("created_at") or 0) >= cutoff]
        if not recent:
            return 0

        payload = _format_extract_input(cards, recent, int(self._clock()))
        try:
            result = self.llm.chat(
                [
                    {"role": "system", "content": self.extract_prompt},
                    {"role": "user", "content": payload},
                ],
                temperature=0.0,
                max_tokens=600,
                purpose="extract",
            )
        except Exception as exc:  # noqa: BLE001 - LLMError 及其它都只记日志
            log.warning("抽取调用失败：%s", exc)
            return 0

        now = int(self._clock())
        ops = parse_ops(result.text, [card["id"] for card in cards])
        message_ids = [int(row["id"]) for row in recent if row.get("id")]
        applied = self._apply_ops(
            ops,
            cards,
            source_from=min(message_ids) if message_ids else None,
            source_to=max(message_ids) if message_ids else None,
        )
        if applied:
            log.info("记忆抽取：应用 %d 条操作", applied)

        registered = self._apply_events(parse_events(result.text, now))
        if registered:
            log.info("记忆抽取：登记 %d 个未来事件", registered)
        return applied

    def _apply_events(self, events: list[dict]) -> int:
        registered = 0
        for event in events:
            self.store.add_scheduled_event(event["summary"], event["due_at"])
            registered += 1
        return registered

    def _apply_ops(
        self,
        ops: list[dict],
        cards: list[dict],
        source_from: int | None = None,
        source_to: int | None = None,
    ) -> int:
        applied = 0
        now = int(self._clock())
        for op in ops:
            if op["op"] == "add":
                duplicate = find_duplicate(op["kind"], op["summary"], cards)
                if duplicate is not None:
                    if duplicate.get("pinned"):
                        continue
                    self.store.update_card(
                        duplicate["id"],
                        summary=op["summary"],
                        importance=op["importance"],
                        updated_at=now,
                    )
                    if crossed_day(duplicate.get("created_at"), now):
                        # 隔天又独立提到一次，就不只是说岔了——转正
                        self.store.set_confirmed(int(duplicate["id"]), True)
                        log.info("记忆转正：%s（隔天又提到一次）", op["summary"])
                    applied += 1
                    continue
                self.store.add_card(
                    kind=op["kind"],
                    summary=op["summary"],
                    detail=op["detail"],
                    entities=op["entities"],
                    occurred_at=op["occurred_at"],
                    importance=op["importance"],
                    source_from=source_from,
                    source_to=source_to,
                    confirmed=False,  # 抽出来的先按"听来的"算，确认了才当真
                )
                applied += 1
                continue

            target = self.store.get_card(op["id"])
            if target is None or target.get("pinned"):
                continue
            fields = {
                "summary": op["summary"],
                "importance": op["importance"],
                "updated_at": now,
            }
            if op["kind"]:
                fields["kind"] = op["kind"]
            if op["detail"]:
                fields["detail"] = op["detail"]
            if op["entities"]:
                fields["entities"] = op["entities"]
            if op["occurred_at"]:
                fields["occurred_at"] = op["occurred_at"]
            if self.store.update_card(op["id"], **fields):
                applied += 1
        return applied

    # ---------------- 压缩 ----------------
    def compact(self, session: str) -> bool:
        if not self.compact_prompt:
            return False

        window_key = f"window_start:{session}"
        window_start = int(self.store.get_cursor(window_key, "0") or 0)
        history = self.store.recent_messages_after(session, window_start, limit=400)
        cutoff = memory_epoch.read(self.settings.base_dir)
        if cutoff:
            history = [row for row in history if int(row.get("created_at") or 0) >= cutoff]
        total = sum(len(str(item.get("content") or "")) for item in history)
        if total <= self.settings.context_soft_char_budget:
            return False

        target = total / 2
        span: list[dict] = []
        used = 0
        for item in history:
            span.append(item)
            used += len(str(item.get("content") or ""))
            if used >= target:
                break
        if not span:
            return False

        self._summarize(span, cards=self.store.list_cards(status="active", limit=60))
        # 压缩失败也要前移游标：原文仍在库里，只是不再进上下文
        self.store.set_cursor(window_key, str(span[-1]["id"]))
        log.info(
            "上下文压缩：折叠 %d 条消息（约 %d 字），游标 -> %s",
            len(span),
            used,
            span[-1]["id"],
        )
        return True

    def _summarize(self, span: list[dict], cards: list[dict]) -> int:
        lines = ["【已有记忆】"]
        lines.extend(f"{c['id']} [{c['kind']}] {c['summary']}" for c in cards)
        lines.append("")
        lines.append("【要压缩的对话】")
        for item in span:
            who = "用户" if item["role"] == "user" else "大肥鱼"
            lines.append(f"{who}：{str(item['content'])[:300]}")

        try:
            result = self.llm.chat(
                [
                    {"role": "system", "content": self.compact_prompt},
                    {"role": "user", "content": "\n".join(lines)},
                ],
                temperature=0.0,
                max_tokens=600,
                purpose="compact",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("压缩调用失败，仅前移游标：%s", exc)
            return 0

        ops = parse_ops(result.text, [])
        created = 0
        for op in ops:
            kind = op["kind"] if op["kind"] in VALID_KINDS else "chat"
            self.store.add_card(
                kind=kind,
                summary=op["summary"],
                detail=op["detail"],
                entities=op["entities"],
                occurred_at=op["occurred_at"],
                importance=op["importance"],
                source_from=span[0]["id"],
                source_to=span[-1]["id"],
                confirmed=False,  # 从对话里读出来的，也是"听来的"
            )
            created += 1
        return created


def _format_extract_input(cards: list[dict], recent: list[dict], now: int) -> str:
    stamp = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M")
    lines = ["【当前时间】", stamp, ""]
    lines.append("【已有记忆】")
    if cards:
        lines.extend(f"{c['id']} [{c['kind']}] {c['summary']}" for c in cards)
    else:
        lines.append("（空）")
    lines.append("")
    lines.append("【最近对话】")
    for item in recent:
        who = "用户" if item["role"] == "user" else "大肥鱼"
        lines.append(f"{who}：{str(item['content'])[:300]}")
    return "\n".join(lines)
