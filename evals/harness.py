"""跑评测：每个场景一个临时库，真模型，检查点见 cases.py。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from qqbot import talkstate
from qqbot.config import Settings
from qqbot.engine import Engine
from qqbot.llm import LLMClient
from qqbot.onebot import IncomingMessage
from qqbot.store import Store

from .cases import Case


@dataclass
class TurnResult:
    user: str
    replies: list[str]


@dataclass
class CaseResult:
    case: Case
    turns: list[TurnResult]
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def text(self) -> str:
        return "\n".join(reply for turn in self.turns for reply in turn.replies)

    def as_dict(self) -> dict:
        return {
            "name": self.case.name,
            "ok": self.ok,
            "failures": self.failures,
            "turns": [
                {"user": turn.user, "replies": turn.replies} for turn in self.turns
            ],
        }


class _Sender:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_private(self, user_id: int, text: str) -> bool:
        self.sent.append(text)
        return True

    def send_private_image(self, user_id: int, image, mode: str = "path") -> bool:
        return True


def evaluate(case: Case, turns: list[TurnResult]) -> list[str]:
    """按检查点判分。只看最后一条回复的条数/字数，看全部回复说的话。"""
    failures: list[str] = []
    everything = "\n".join(reply for turn in turns for reply in turn.replies)
    last = turns[-1].replies if turns else []
    last_text = "\n".join(last)

    if not last_text.strip():
        failures.append("最后一条没回")

    if case.include and not any(re.search(p, everything) for p in case.include):
        failures.append("没接住：" + "/".join(case.include))
    for pattern in case.exclude:
        found = re.search(pattern, everything)
        if found:
            failures.append(f"不该说：{found.group(0)!r}")
    if len(last) > case.max_bubbles:
        failures.append(f"气泡 {len(last)} 条 > {case.max_bubbles}")
    if len(last) < case.min_bubbles:
        failures.append(f"气泡 {len(last)} 条 < {case.min_bubbles}")
    if len(last_text) > case.max_chars:
        failures.append(f"{len(last_text)} 字 > {case.max_chars}")

    for pattern, allowed in case.max_hits:
        hits = sum(len(re.findall(pattern, " ".join(turn.replies))) for turn in turns)
        if hits > allowed:
            failures.append(f"「{pattern}」出现 {hits} 次 > {allowed}")

    questions = sum(1 for bubble in last if talkstate.asks_question(bubble))
    if questions > case.max_questions:
        failures.append(f"问了 {questions} 句 > {case.max_questions}（像查户口）")
    return failures


def run_case(case: Case, base: Settings, llm: LLMClient, workdir: Path) -> CaseResult:
    """跑一个场景。每个场景独立建库，互不影响。"""
    workdir.mkdir(parents=True, exist_ok=True)
    db = workdir / f"case-{abs(hash(case.name)) % 10**8}.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)

    settings = replace(
        base,
        db_path=db,
        sticker_chance=0.0,  # 评测只看文字，别让随机表情包干扰
        presence_enabled=False,  # 别让"她在忙"吃掉回复
        catchup_enabled=False,
        life_enabled=False,  # 生活线跟着日期变，评测要可复现
        digest_dir=workdir / "daily",
    )
    store = Store(settings.db_path)
    sender = _Sender()
    engine = Engine(
        settings,
        store,
        llm,
        sender,
        Path(base.system_prompt_file).read_text(encoding="utf-8"),
        sleep=lambda _seconds: None,
    )

    turns: list[TurnResult] = []
    try:
        for index, text in enumerate(case.turns, 1):
            sender.sent.clear()
            engine.handle_message(
                IncomingMessage(
                    user_id=base.allow_user_id,
                    message_id=f"{case.name}-{index}",
                    text=text,
                    raw={},
                )
            )
            turns.append(TurnResult(user=text, replies=list(sender.sent)))
    finally:
        store.close()
        for suffix in ("", "-wal", "-shm"):
            Path(str(db) + suffix).unlink(missing_ok=True)

    return CaseResult(case=case, turns=turns, failures=evaluate(case, turns))
