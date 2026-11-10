# -*- coding: utf-8 -*-
from pathlib import Path
from types import SimpleNamespace

import pytest

from qqbot.config import Settings
from qqbot.goal_actions import GoalActionService
from qqbot.store import Store


class FakeLLM:
    def chat(self, messages, **kwargs):
        return SimpleNamespace(text="今天面试还顺利吗？")


class FakeScheduler:
    def __init__(self, ok=True, reason="想他了"):
        self.ok = ok
        self.reason = reason
        self.sent = []

    def send_approved(self, text, now=None):
        if self.ok:
            self.sent.append(text)
        return self.ok, self.reason


def _env(tmp_path: Path, status="active"):
    settings = Settings(
        allow_user_id=123,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "data" / "chat.db",
        system_prompt_file=tmp_path / "prompts" / "system.md",
    )
    store = Store(settings.db_path)
    goal_id = store.add_goal(
        "shared", "问问他面试结果", why="他提过这周有面试", status=status, evidence=[1]
    )
    engine = SimpleNamespace(
        settings=settings,
        store=store,
        llm=FakeLLM(),
        proactive_scheduler=FakeScheduler(),
    )
    engine._build_messages = lambda *args, **kwargs: [{"role": "system", "content": "背景"}]
    return settings, store, engine, goal_id


def test_prepare_creates_waiting_action_without_sending(tmp_path):
    _settings, store, engine, goal_id = _env(tmp_path)

    action = GoalActionService(engine).prepare(goal_id)

    assert action["status"] == "waiting_approval"
    assert action["approval"] == "confirm"
    assert action["payload"]["text"] == "今天面试还顺利吗？"
    assert engine.proactive_scheduler.sent == []


def test_approve_sends_once_and_records_outcome(tmp_path):
    _settings, store, engine, goal_id = _env(tmp_path)
    service = GoalActionService(engine)
    action = service.prepare(goal_id)

    done = service.approve(action["id"])

    assert done["status"] == "done"
    assert engine.proactive_scheduler.sent == ["今天面试还顺利吗？"]
    assert store.list_goal_actions(status="done")[0]["id"] == action["id"]
    assert store.list_goal_actions(status="waiting_approval") == []


def test_approve_respects_gate_and_keeps_action_pending(tmp_path):
    _settings, store, engine, goal_id = _env(tmp_path)
    engine.proactive_scheduler = FakeScheduler(ok=False, reason="不在时段")
    service = GoalActionService(engine)
    action = service.prepare(goal_id)

    with pytest.raises(ValueError, match="现在不能发"):
        service.approve(action["id"])

    assert store.get_goal_action(action["id"])["status"] == "waiting_approval"
    assert engine.proactive_scheduler.sent == []


def test_candidate_goal_cannot_prepare_action(tmp_path):
    _settings, _store, engine, goal_id = _env(tmp_path, status="candidate")

    with pytest.raises(ValueError, match="确认成"):
        GoalActionService(engine).prepare(goal_id)


def test_cancel_removes_pending_action(tmp_path):
    _settings, store, engine, goal_id = _env(tmp_path)
    service = GoalActionService(engine)
    action = service.prepare(goal_id)

    cancelled = service.cancel(action["id"])

    assert cancelled["status"] == "cancelled"
    assert store.list_goal_actions(status="waiting_approval") == []
