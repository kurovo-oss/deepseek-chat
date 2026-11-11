# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from qqbot import autonomy, goals
from qqbot.config import Settings
from qqbot.store import Store


class FakeLLM:
    def chat(self, messages, **kwargs):
        return SimpleNamespace(text="今天面试还顺利吗？")


def _env(tmp_path: Path, enabled=True):
    settings = Settings(
        allow_user_id=123,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "data" / "chat.db",
        system_prompt_file=tmp_path / "prompts" / "system.md",
        goals_enabled=True,
        autonomy_enabled=enabled,
        autonomy_mode="confirm",
    )
    store = Store(settings.db_path)
    engine = SimpleNamespace(
        settings=settings,
        store=store,
        llm=FakeLLM(),
        proactive_scheduler=None,
    )
    engine._build_messages = lambda *args, **kwargs: [{"role": "system", "content": "背景"}]
    return settings, store, engine


def test_due_active_goal_becomes_waiting_approval(tmp_path):
    settings, store, engine = _env(tmp_path)
    stamp = 1_700_000_000
    goal_id = store.add_goal(
        "shared", "问问他面试结果", status="active", due_at=stamp - 100, evidence=[1]
    )

    created = autonomy.tick(engine, store, settings, now=stamp)

    assert len(created) == 1
    assert created[0]["status"] == "waiting_approval"
    assert created[0]["goal_id"] == goal_id
    assert engine.proactive_scheduler is None  # 没有发送器也不影响“准备”
    assert store.get_goal(goal_id)["progress"] == 0


def test_disabled_autonomy_does_nothing(tmp_path):
    settings, store, engine = _env(tmp_path, enabled=False)
    stamp = 1_700_000_000
    store.add_goal("shared", "问问他面试结果", status="active", due_at=stamp - 100, evidence=[1])

    assert autonomy.tick(engine, store, settings, now=stamp) == []


def test_reply_updates_goal_progress_once(tmp_path):
    _settings, store, _engine = _env(tmp_path)
    goal_id = store.add_goal("shared", "问问他面试结果", status="active", evidence=[1])
    action_id = store.add_goal_action(
        goal_id, "send_message", payload={"text": "在吗"}, status="done"
    )
    store.update_goal_action(action_id, result="sent")

    first = goals.record_user_reply(store, now=1_000)
    second = goals.record_user_reply(store, now=1_100)

    assert first is not None
    assert second is None
    assert store.get_goal(goal_id)["progress"] == 0.1
    assert store.list_outcomes(action_id)[0]["signal"] == "replied"


def test_old_goal_table_gets_new_columns(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE goals (id INTEGER PRIMARY KEY, kind TEXT, title TEXT, why TEXT,"
        " horizon TEXT, priority REAL, status TEXT, source TEXT, evidence TEXT,"
        " confidence REAL, due_at INTEGER, review_at INTEGER, created_at INTEGER, updated_at INTEGER)"
    )
    conn.execute(
        "INSERT INTO goals(kind,title,why,horizon,priority,status,source,evidence,"
        "confidence,created_at,updated_at) VALUES ('shared','旧目标','','week',0.5,"
        "'active','reflection','[]',0.5,1,1)"
    )
    conn.commit()
    conn.close()

    store = Store(path)
    goal = store.get_goal(1)
    assert goal["progress"] == 0
    assert goal["autonomy"] == "confirm"
    assert goal["parent_id"] is None
    store.close()


def test_goal_review_parses_due_hours(tmp_path):
    settings, store, engine = _env(tmp_path)
    card_id = store.add_card("preference", "他最近在准备面试")
    engine.llm = SimpleNamespace(
        chat=lambda *args, **kwargs: SimpleNamespace(
            text='{"goals":[{"kind":"shared","title":"问面试","why":"他提过","horizon":"week","priority":0.6,"due_hours":24,"evidence":[%d]}]}'
            % card_id
        )
    )

    created = goals.review(store, engine.llm, settings, now=1_000)

    assert created[0]["due_at"] == 1_000 + 24 * 3600
