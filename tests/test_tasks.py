# -*- coding: utf-8 -*-
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from qqbot.config import Settings
from qqbot.store import Store
from qqbot.tasks import TaskService


class FakeLLM:
    def chat(self, messages, **kwargs):
        return SimpleNamespace(text="我来啦，这件事已经处理好了。")


def _env(tmp_path: Path):
    settings = Settings(
        allow_user_id=123,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "data" / "chat.db",
        system_prompt_file=tmp_path / "prompts" / "system.md",
        proactive_quiet_start_hour=23,
        proactive_quiet_end_hour=8,
    )
    store = Store(settings.db_path)
    sent = []

    engine = SimpleNamespace(
        settings=settings,
        store=store,
        llm=FakeLLM(),
        _build_messages=lambda *args, **kwargs: [{"role": "system", "content": "背景"}],
        send_proactive=lambda session, text: sent.append((session, text)) or True,
    )
    return settings, store, engine, sent


def test_promise_creates_task_and_deduplicates(tmp_path):
    settings, store, engine, _sent = _env(tmp_path)
    service = TaskService(engine, settings)

    first = service.note_promise("帮我整理一下", "好的，我完成了会跟你说", source_message_id=7)
    second = service.note_promise("帮我整理一下", "好的，我完成了会跟你说", source_message_id=7)

    assert first is not None
    assert first["kind"] == "promise"
    assert first["status"] == "pending"
    assert second is None
    assert len(store.list_tasks()) == 1


def test_due_task_runs_and_notifies(tmp_path):
    settings, store, engine, sent = _env(tmp_path)
    service = TaskService(engine, settings)
    now = int(datetime(2026, 11, 11, 12, 0).timestamp())
    task = service.create("写一句总结", "测试任务", due_at=now - 1)

    service.run_due(now)
    done = store.get_task(task["id"])
    assert done["status"] == "done"
    assert "处理好了" in done["result"]

    service.notify_due(now)
    assert sent == [("123", "我来啦，这件事已经处理好了。")]
    assert store.get_task(task["id"])["notify_status"] == "sent"


def test_quiet_hours_defers_notification(tmp_path):
    settings, store, engine, sent = _env(tmp_path)
    service = TaskService(engine, settings)
    night = int(datetime(2026, 11, 11, 23, 30).timestamp())
    task = service.create("写一句总结", "测试任务", due_at=night - 1)

    service.run_due(night)
    service.notify_due(night)

    assert sent == []
    assert store.get_task(task["id"])["notify_status"] == "pending"
    assert store.get_task(task["id"])["notify_at"] > night


def test_reminder_task_does_not_call_model(tmp_path):
    settings, store, engine, _sent = _env(tmp_path)
    service = TaskService(engine, settings)
    now = int(datetime(2026, 11, 11, 12, 0).timestamp())
    task = service.create("提醒他喝水", kind="reminder", due_at=now - 1)

    service.run_due(now)

    result = store.get_task(task["id"])["result"]
    assert "提醒" in result
