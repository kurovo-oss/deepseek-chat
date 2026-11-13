# -*- coding: utf-8 -*-
from pathlib import Path
from types import SimpleNamespace

from qqbot import autonomy, goals
from qqbot.config import Settings
from qqbot.store import Store
from qqbot.webui import WebUI

ROOT = Path(__file__).resolve().parents[1]


class FakeLLM:
    def __init__(self, text="我在想，你现在方便说句话吗？"):
        self.text = text
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return SimpleNamespace(text=self.text)


class FakeScheduler:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.sent = []

    def can_send(self, now=None):
        return (True, "ok") if self.allowed else (False, "免打扰时段")

    def send_approved(self, text, now=None):
        self.sent.append(text)
        return True, "ok"


def _env(tmp_path: Path, mode="shadow", shadow_days=0, allowed=True):
    settings = Settings(
        allow_user_id=123,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "data" / "chat.db",
        system_prompt_file=tmp_path / "prompts" / "system.md",
        autonomy_enabled=True,
        autonomy_mode=mode,
        autonomy_shadow_days=shadow_days,
        autonomy_shadow_max_per_day=1,
        autonomy_policy_min_score=0.5,
        autonomy_max_actions_per_day=1,
        autonomy_ignore_hours=24,
    )
    store = Store(settings.db_path)
    engine = SimpleNamespace(
        settings=settings,
        store=store,
        llm=FakeLLM(),
        proactive_scheduler=FakeScheduler(allowed),
    )
    engine._build_messages = lambda *args, **kwargs: [{"role": "system", "content": "背景"}]
    return settings, store, engine


def _goal(store, stamp, autonomy_value="auto"):
    return store.add_goal(
        "shared",
        "问问他今天的面试",
        why="他昨天说今天面试",
        status="active",
        autonomy=autonomy_value,
        evidence=[1],
        confidence=0.7,
        due_at=stamp - 10,
    )


def test_shadow_records_decision_without_sending(tmp_path):
    settings, store, engine = _env(tmp_path, "shadow")
    stamp = 1_700_000_000
    goal_id = _goal(store, stamp)

    created = autonomy.tick(engine, store, settings, now=stamp)

    assert len(created) == 1
    action = created[0]
    assert action["status"] == "shadow"
    assert action["approval"] == "shadow"
    assert action["goal_id"] == goal_id
    assert action["payload"]["score"] >= 0.5
    assert engine.proactive_scheduler.sent == []
    assert store.list_proactive() == []
    assert store.get_goal(goal_id)["progress"] == 0


def test_shadow_does_not_send_when_proactive_gate_blocks(tmp_path):
    settings, store, engine = _env(tmp_path, "shadow", allowed=False)
    stamp = 1_700_000_000
    _goal(store, stamp)

    created = autonomy.tick(engine, store, settings, now=stamp)

    assert created[0]["status"] == "shadow"
    assert "proactive_gate" in created[0]["payload"]["hard_rules"]
    assert engine.proactive_scheduler.sent == []


def test_bounded_auto_sends_once_per_day(tmp_path):
    settings, store, engine = _env(tmp_path, "bounded_auto")
    stamp = 1_700_000_000
    store.set_cursor("autonomy:shadow_started", str(stamp - 8 * 86400))
    goal_id = _goal(store, stamp)

    first = autonomy.tick(engine, store, settings, now=stamp)
    second = autonomy.tick(engine, store, settings, now=stamp + 60)

    assert len(first) == 1
    assert first[0]["status"] == "done"
    assert first[0]["result"] == "sent"
    assert engine.proactive_scheduler.sent == [first[0]["payload"]["text"]]
    assert second == []
    assert store.get_goal(goal_id)["review_at"] == stamp
    assert store.count_auto_actions_since(stamp - 86400) == 1


def test_bounded_auto_blocks_without_shadow_period(tmp_path):
    settings, store, engine = _env(tmp_path, "bounded_auto", shadow_days=7)
    stamp = 1_700_000_000
    store.set_cursor("autonomy:shadow_started", str(stamp - 86400))
    _goal(store, stamp)

    assert autonomy.tick(engine, store, settings, now=stamp) == []
    assert engine.proactive_scheduler.sent == []


def test_bounded_auto_requires_goal_marked_auto(tmp_path):
    settings, store, engine = _env(tmp_path, "bounded_auto")
    stamp = 1_700_000_000
    store.set_cursor("autonomy:shadow_started", str(stamp - 8 * 86400))
    _goal(store, stamp, autonomy_value="confirm")

    assert autonomy.tick(engine, store, settings, now=stamp) == []
    assert engine.proactive_scheduler.sent == []


def test_breaker_blocks_sends_until_manual_reset(tmp_path):
    settings, store, engine = _env(tmp_path, "bounded_auto")
    stamp = 1_700_000_000
    store.set_cursor("autonomy:shadow_started", str(stamp - 8 * 86400))
    _goal(store, stamp)
    autonomy.trip(store, "feedback_bad", now=stamp)

    assert autonomy.tick(engine, store, settings, now=stamp) == []
    assert autonomy.breaker(store)["reason"] == "feedback_bad"

    autonomy.clear_breaker(store)
    store.update_goal(1, review_at=0)
    assert autonomy.tick(engine, store, settings, now=stamp + 1)


def test_unsafe_text_trips_breaker_and_never_sends(tmp_path):
    settings, store, engine = _env(tmp_path, "bounded_auto")
    stamp = 1_700_000_000
    store.set_cursor("autonomy:shadow_started", str(stamp - 8 * 86400))
    engine.llm.text = "你不理我，我就难过，我一直在你门口"
    _goal(store, stamp)

    created = autonomy.tick(engine, store, settings, now=stamp)

    assert created == []
    assert engine.proactive_scheduler.sent == []
    assert autonomy.breaker(store)["tripped_at"] > 0


def test_two_ignored_auto_actions_trip_breaker(tmp_path):
    settings, store, engine = _env(tmp_path, "bounded_auto")
    stamp = 1_700_000_000
    store.set_cursor("autonomy:shadow_started", str(stamp - 8 * 86400))
    goal_id = _goal(store, stamp)
    for index in range(2):
        action_id = store.add_goal_action(
            goal_id,
            "send_message",
            payload={"text": "在吗"},
            created_at=stamp - 3 * 86400 + index,
            status="done",
            approval="auto",
        )
        store.update_goal_action(action_id, result="sent")
    assert autonomy.tick(engine, store, settings, now=stamp) == []
    assert autonomy.breaker(store)["reason"] == "same_goal_ignored_twice"
    assert store.get_goal(goal_id)["autonomy"] == "confirm"


def test_user_reply_advances_progress_and_bad_feedback_trips(tmp_path):
    settings, store, engine = _env(tmp_path, "bounded_auto")
    stamp = 1_700_000_000
    store.set_cursor("autonomy:shadow_started", str(stamp - 8 * 86400))
    goal_id = _goal(store, stamp)
    action = autonomy.tick(engine, store, settings, now=stamp)[0]

    goals.record_user_reply(store, now=stamp + 10)
    assert store.get_goal(goal_id)["progress"] == 0.1

    goals.record_feedback(store, "bad", now=stamp + 20)
    assert autonomy.breaker(store)["reason"] == "feedback_bad"


def test_pause_stops_auto_and_ui_reset_is_manual(tmp_path):
    settings, store, engine = _env(tmp_path, "bounded_auto")
    stamp = 1_700_000_000
    store.set_cursor("autonomy:shadow_started", str(stamp - 8 * 86400))
    _goal(store, stamp)
    autonomy.set_paused(store, True)

    assert autonomy.tick(engine, store, settings, now=stamp) == []
    autonomy.set_paused(store, False)
    store.update_goal(1, review_at=0)
    assert autonomy.tick(engine, store, settings, now=stamp + 1)


def test_goal_api_reports_autonomy_and_allows_marking_auto(tmp_path):
    settings, store, engine = _env(tmp_path, "shadow")
    goal_id = _goal(store, 1_700_000_000)
    ui = WebUI(engine, settings)

    payload = ui.api_goals()
    assert payload["autonomy"]["mode"] == "shadow"
    assert payload["autonomy"]["enabled"] is True
    assert payload["autonomy"]["breaker"] == {}

    updated = ui.api_goal_update({"id": goal_id, "autonomy": "auto"})
    assert updated["ok"] is True
    assert updated["item"]["autonomy"] == "auto"


def test_acceptance_shadow_seven_days_then_bounded_auto(tmp_path):
    base = 1_700_000_000
    settings, store, engine = _env(tmp_path, "shadow", shadow_days=7)
    goal_id = _goal(store, base)

    for day in range(7):
        now = base + day * 86400
        store.update_goal(goal_id, due_at=now - 1, review_at=0)
        created = autonomy.tick(engine, store, settings, now=now)
        assert len(created) == 1
        assert created[0]["status"] == "shadow"
        assert engine.proactive_scheduler.sent == []

    # 影子满 7 天后，切到 bounded_auto，可以真正发一次
    settings2 = Settings(
        **{
            **settings.__dict__,
            "autonomy_mode": "bounded_auto",
        }
    )
    engine.settings = settings2
    now = base + 7 * 86400
    store.update_goal(goal_id, due_at=now - 1, review_at=0)
    created = autonomy.tick(engine, store, settings2, now=now)

    assert len(created) == 1
    assert created[0]["status"] == "done"
    assert len(engine.proactive_scheduler.sent) == 1


def test_acceptance_fourteen_days_one_send_per_day_no_violation(tmp_path):
    base = 1_700_000_000
    settings, store, engine = _env(tmp_path, "bounded_auto")
    store.set_cursor("autonomy:shadow_started", str(base - 8 * 86400))
    goal_id = _goal(store, base)

    for day in range(14):
        now = base + day * 86400
        store.update_goal(goal_id, due_at=now - 1, review_at=0, progress=0, status="active")
        actions = autonomy.tick(engine, store, settings, now=now)
        assert len(actions) == 1, f"day {day}"
        assert actions[0]["status"] == "done"
        goals.record_user_reply(store, now=now + 10)

    assert len(engine.proactive_scheduler.sent) == 14
    assert autonomy.breaker(store) == {}
    assert all(
        action["action"] == "send_message"
        for action in store.list_goal_actions(limit=200)
        if action.get("approval") == "auto"
    )


def test_api_pause_and_reset_are_visible_and_take_effect(tmp_path):
    settings, store, engine = _env(tmp_path, "bounded_auto")
    stamp = 1_700_000_000
    store.set_cursor("autonomy:shadow_started", str(stamp - 8 * 86400))
    _goal(store, stamp)
    ui = WebUI(engine, settings)

    ui.api_autonomy_pause({"paused": True})
    assert autonomy.paused(store) is True
    assert autonomy.tick(engine, store, settings, now=stamp) == []
    ui.api_autonomy_pause({"paused": False})
    store.update_goal(1, review_at=0)
    assert autonomy.tick(engine, store, settings, now=stamp + 1)

    autonomy.trip(store, "feedback_bad", now=stamp + 2)
    ui.api_autonomy_reset({})
    assert autonomy.breaker(store) == {}


def test_shadow_ready_counts_active_days_not_wall_clock(tmp_path):
    settings, store, _engine = _env(tmp_path, "shadow", shadow_days=7)
    base = 1_700_000_000
    store.set_cursor("autonomy:shadow_started", str(base))
    store.add_goal_action(
        1,
        "send_message",
        payload={"text": "晚安"},
        run_at=base,
        status="shadow",
        approval="shadow",
        created_at=base,
    )

    # 关机 8 天：墙上过了 8 天，但只有 1 个影子日，不算满
    assert autonomy.shadow_ready(store, settings, base + 8 * 86400) is False

    for day in range(1, 7):
        store.add_goal_action(
            1,
            "send_message",
            payload={"text": "晚安"},
            run_at=base + day * 86400,
            status="shadow",
            approval="shadow",
            created_at=base + day * 86400,
        )
    assert autonomy.shadow_ready(store, settings, base + 7 * 86400) is True


def test_stage6_ui_surface_exists():
    html = (ROOT / "qqbot" / "ui" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "qqbot" / "ui" / "app.js").read_text(encoding="utf-8")

    assert "autonomy-reset" in html
    assert "autonomy-pause" in html
    assert "/api/goals/autonomy/reset" in js
    assert "/api/goals/autonomy/pause" in js
    assert "影子运行" in js
    assert "自动执行中" in js
