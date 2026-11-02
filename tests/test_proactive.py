"""主动消息：闸门、生成、发送、失败不重试。"""

import types
from datetime import datetime
import threading
import time

import pytest

from qqbot.config import Settings
from qqbot.engine import Engine
from qqbot.llm import ChatResult, LLMError
from qqbot.memory import parse_events
from qqbot.proactive import ProactiveScheduler
from qqbot.store import Store


def ts(month, day, hour, minute=0):
    """本地时间 → 时间戳（调度器用的是本地时区）。"""
    return int(datetime(2026, month, day, hour, minute).timestamp())


class FakeLLM:
    def __init__(self, text="今天还加班吗"):
        self.text = text
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if isinstance(self.text, Exception):
            raise self.text
        return ChatResult(self.text, 100, 10, "fake-model")


class FakeSender:
    def __init__(self, ok=True):
        self.ok = ok
        self.texts = []

    def send_private(self, user_id, text):
        self.texts.append(text)
        return self.ok

    def send_private_image(self, user_id, image, mode="path"):
        return True


def make_settings(tmp_path, **overrides):
    kwargs = dict(
        allow_user_id=100,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "d.db",
        system_prompt_file=tmp_path / "s.md",
        segment_delay_ms=0,
        reply_max_chars=100,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


def build(tmp_path, reply="今天还加班吗", sender_ok=True, **overrides):
    settings = make_settings(tmp_path, **overrides)
    store = Store(settings.db_path)
    llm = FakeLLM(reply)
    sender = FakeSender(sender_ok)
    engine = Engine(
        settings, store, llm, sender, "你是大肥鱼", sleep=lambda _: None
    )
    scheduler = ProactiveScheduler(engine, store, settings, prompt="")
    return types.SimpleNamespace(
        settings=settings,
        store=store,
        llm=llm,
        sender=sender,
        engine=engine,
        scheduler=scheduler,
    )


def seed_silence(env, hours_ago=76, base=ts(10, 28, 14)):
    env.store.save_message("100", "user", "晚安", created_at=base - hours_ago * 3600)
    env.store.save_message("100", "assistant", "晚安呀", created_at=base - hours_ago * 3600 + 60)


# ---------------------------------------------------------------- 闸门
def test_allows_followup_after_long_silence(tmp_path):
    env = build(tmp_path)
    seed_silence(env)
    assert env.scheduler.gate(ts(10, 28, 14)) == "followup"
    env.store.close()


def test_blocks_outside_hours(tmp_path):
    env = build(tmp_path)
    seed_silence(env)
    assert env.scheduler.gate(ts(10, 28, 7)) is None  # 早上 7 点
    assert env.scheduler.gate(ts(10, 28, 23, 30)) is None  # 深夜
    env.store.close()


def test_blocks_when_user_talked_recently(tmp_path):
    env = build(tmp_path)
    seed_silence(env, hours_ago=0.2)  # 12 分钟前刚说过话
    assert env.scheduler.gate(ts(10, 28, 14)) is None
    env.store.close()


def test_blocks_when_silence_not_long_enough(tmp_path):
    env = build(tmp_path, proactive_idle_hours=48)
    seed_silence(env, hours_ago=10)  # 才 10 小时，离 48 小时远着
    assert env.scheduler.gate(ts(10, 28, 14)) is None
    env.store.close()


def test_blocks_after_daily_limit(tmp_path):
    env = build(tmp_path)
    seed_silence(env)
    now = ts(10, 28, 14)
    for offset in range(3):
        env.store.add_proactive_event(
            "followup", "第%d条" % offset, created_at=env.scheduler.midnight(now) + offset
        )
    assert env.scheduler.gate(now) is None
    env.store.close()


def test_blocks_when_gap_too_small(tmp_path):
    env = build(tmp_path)
    seed_silence(env)
    now = ts(10, 28, 14)
    env.store.add_proactive_event("followup", "刚才说过", created_at=now - 3600)
    assert env.scheduler.gate(now) is None
    env.store.close()


def test_blocks_when_she_spoke_last(tmp_path):
    """她上次主动说完用户没回，不能连着追。"""
    env = build(tmp_path)
    now = ts(10, 28, 14)
    env.store.save_message("100", "user", "晚安", created_at=now - 76 * 3600)
    env.store.add_proactive_event("followup", "在忙吗", created_at=now - 10 * 3600)
    assert env.scheduler.gate(now) is None
    env.store.close()


def test_blocks_once_per_week(tmp_path):
    env = build(tmp_path)
    seed_silence(env)
    now = ts(10, 28, 14)
    env.store.add_proactive_event("followup", "上周说过", created_at=now - 3 * 86400)
    assert env.scheduler.gate(now) is None
    env.store.close()


def test_blocks_without_any_history(tmp_path):
    env = build(tmp_path)
    assert env.scheduler.gate(ts(10, 28, 14)) is None
    env.store.close()


def test_disabled_blocks_everything(tmp_path):
    env = build(tmp_path, proactive_enabled=False)
    seed_silence(env)
    assert env.scheduler.gate(ts(10, 28, 14)) is None
    env.store.close()


# ---------------------------------------------------------------- 发送
def test_tick_sends_and_records(tmp_path):
    env = build(tmp_path, reply="今天还加班吗")
    seed_silence(env)
    assert env.scheduler.tick(now=ts(10, 28, 14)) == "sent"

    assert env.sender.texts == ["今天还加班吗"]
    event = env.store.latest_proactive()
    assert event["kind"] == "followup"
    assert event["status"] == "sent"
    assert env.store.recent_messages("100", 1)[0]["content"] == "今天还加班吗"
    env.store.close()


def test_tick_respects_skip(tmp_path):
    env = build(tmp_path, reply="SKIP")
    seed_silence(env)
    assert env.scheduler.tick(now=ts(10, 28, 14)) == "skipped"
    assert env.sender.texts == []
    assert env.store.latest_proactive() is None
    env.store.close()


def test_tick_reports_send_failure(tmp_path):
    env = build(tmp_path, sender_ok=False)
    seed_silence(env)
    assert env.scheduler.tick(now=ts(10, 28, 14)) == "failed"
    event = env.store.latest_proactive()
    assert event["status"] == "failed"
    env.store.close()


def test_tick_does_not_retry_after_failure(tmp_path):
    """失败不重试：重试等于骚扰。"""
    env = build(tmp_path, sender_ok=False)
    seed_silence(env)
    now = ts(10, 28, 14)
    assert env.scheduler.tick(now=now) == "failed"
    assert env.scheduler.tick(now=now + 600) == "blocked"
    env.store.close()


def test_tick_survives_llm_error(tmp_path):
    env = build(tmp_path, reply=LLMError("模型挂了"))
    seed_silence(env)
    assert env.scheduler.tick(now=ts(10, 28, 14)) == "skipped"
    assert env.sender.texts == []
    env.store.close()


def test_second_tick_same_day_is_blocked_by_gap(tmp_path):
    env = build(tmp_path)
    seed_silence(env)
    now = ts(10, 28, 14)
    assert env.scheduler.tick(now=now) == "sent"
    assert env.scheduler.tick(now=now + 3600) == "blocked"
    env.store.close()


# ---------------------------------------------------------------- 手动触发
def test_force_bypasses_gate(tmp_path):
    """刚聊完也能手动触发一次，用来验证效果。"""
    env = build(tmp_path, reply="在忙吗")
    env.store.save_message("100", "user", "刚说完", created_at=ts(10, 28, 14) - 60)
    assert env.scheduler.gate(ts(10, 28, 14)) is None

    assert env.scheduler.force(ts(10, 28, 14)) == "sent"
    assert env.sender.texts == ["在忙吗"]
    assert env.store.latest_proactive()["kind"] == "manual"
    env.store.close()


def test_force_respects_skip(tmp_path):
    env = build(tmp_path, reply="SKIP")
    assert env.scheduler.force(ts(10, 28, 14)) == "skipped"
    assert env.sender.texts == []
    env.store.close()


def test_proactive_command_triggers(tmp_path):
    env = build(tmp_path, reply="在忙吗")
    env.engine.proactive_trigger = env.scheduler.force
    reply = env.engine._handle_command("/proactive", "100")
    assert "开口" in reply
    assert env.sender.texts == ["在忙吗"]
    env.store.close()


def test_proactive_command_without_scheduler(tmp_path):
    env = build(tmp_path)
    assert "没开" in env.engine._handle_command("/proactive", "100")
    env.store.close()


# ---------------------------------------------------------------- 提示词
def test_instruction_mentions_silence_hours(tmp_path):
    env = build(tmp_path)
    seed_silence(env, hours_ago=76)
    messages = env.scheduler.build_messages("followup", ts(10, 28, 14))
    assert messages[-1]["role"] == "system"
    assert "76" in messages[-1]["content"]
    env.store.close()


def test_prompt_placeholders_are_substituted(tmp_path):
    env = build(tmp_path)
    env.scheduler.prompt = "现在是 {time}，{hours} 小时没说话了"
    seed_silence(env, hours_ago=76)
    content = env.scheduler.build_messages("followup", ts(10, 28, 14))[-1]["content"]
    assert content == "现在是 14:00，76 小时没说话了"
    env.store.close()


def test_prompt_file_is_loaded():
    from pathlib import Path

    text = (
        Path(__file__).resolve().parent.parent / "prompts" / "proactive.md"
    ).read_text(encoding="utf-8")
    assert "SKIP" in text
    assert "{hours}" in text
    assert "{time}" in text
    assert "编" in text  # 明确禁止编造话题


# ---------------------------------------------------------------- 未来事件
def test_parse_events_keeps_valid_due_time():
    raw = '{"cards":[],"events":[{"summary":"面试","due_at":"2026-11-04 09:00"}]}'
    assert parse_events(raw, ts(10, 28, 22)) == [
        {"summary": "面试", "due_at": ts(11, 4, 9)}
    ]


def test_parse_events_drops_unparseable_time():
    """解析不出具体时间就丢掉——猜错了会在错误的日子开口。"""
    raw = '{"events":[{"summary":"面试","due_at":"下周三"}]}'
    assert parse_events(raw, ts(10, 28, 22)) == []


def test_parse_events_drops_past():
    raw = '{"events":[{"summary":"面试","due_at":"2026-10-01 09:00"}]}'
    assert parse_events(raw, ts(10, 28, 22)) == []


def test_parse_events_caps_count():
    raw = (
        '{"events":[{"summary":"a","due_at":"2026-11-04"},'
        '{"summary":"b","due_at":"2026-11-05"},'
        '{"summary":"c","due_at":"2026-11-06"}]}'
    )
    assert len(parse_events(raw, ts(10, 28, 22))) == 2


def test_date_only_defaults_to_evening():
    raw = '{"events":[{"summary":"体检","due_at":"2026-11-04"}]}'
    assert parse_events(raw, ts(10, 28, 22))[0]["due_at"] == ts(11, 4, 19)


def test_event_prompt_documents_events():
    from pathlib import Path

    text = (
        Path(__file__).resolve().parent.parent / "prompts" / "extract.md"
    ).read_text(encoding="utf-8")
    assert "events" in text
    assert "due_at" in text


def test_scheduled_prompt_has_placeholders():
    from pathlib import Path

    text = (
        Path(__file__).resolve().parent.parent / "prompts" / "scheduled.md"
    ).read_text(encoding="utf-8")
    assert "{event}" in text
    assert "{time}" in text


def test_same_event_updates_instead_of_duplicating(tmp_path):
    env = build(tmp_path)
    env.store.add_scheduled_event("面试", ts(11, 4, 9))
    env.store.add_scheduled_event("面试", ts(11, 5, 9))
    events = env.store.list_events(status="pending")
    assert len(events) == 1
    assert events[0]["trigger_at"] == ts(11, 5, 9)
    env.store.close()


def test_due_event_beats_followup_wait(tmp_path):
    """才聊完 3 小时，但事件到点了 → 发 scheduled，不等 72 小时。"""
    env = build(tmp_path)
    env.store.save_message("100", "user", "刚聊完", created_at=ts(10, 28, 14) - 3 * 3600)
    env.store.add_scheduled_event("面试", ts(10, 28, 14))
    assert env.scheduler.gate(ts(10, 28, 14)) == "scheduled"
    env.store.close()


def test_scheduled_respects_quiet_minutes(tmp_path):
    env = build(tmp_path)
    env.store.save_message("100", "user", "在", created_at=ts(10, 28, 14) - 60)
    env.store.add_scheduled_event("面试", ts(10, 28, 14))
    assert env.scheduler.gate(ts(10, 28, 14)) is None
    env.store.close()


def test_scheduled_fires_and_marks_event_sent(tmp_path):
    env = build(tmp_path, reply="面试还顺利吗")
    env.store.add_scheduled_event("面试", ts(10, 28, 14))
    assert env.scheduler.tick(now=ts(10, 28, 14)) == "sent"

    assert env.sender.texts == ["面试还顺利吗"]
    events = env.store.list_events(status="sent")
    assert events[0]["content"] == "面试还顺利吗"
    assert env.store.list_events(status="pending") == []
    env.store.close()


def test_scheduled_skip_postpones_instead_of_dropping(tmp_path):
    """模型一时不想说话，不该把"面试"这种事弄丢——推迟几小时再来一次。"""
    env = build(tmp_path, reply="SKIP")
    env.store.add_scheduled_event("面试", ts(10, 28, 14))
    assert env.scheduler.tick(now=ts(10, 28, 14)) == "skipped"

    pending = env.store.list_events(status="pending")
    assert len(pending) == 1
    assert pending[0]["attempts"] == 1
    assert pending[0]["trigger_at"] == ts(10, 28, 17)  # +3 小时
    env.store.close()


def test_scheduled_gives_up_after_two_attempts(tmp_path):
    env = build(tmp_path, reply="SKIP")
    env.store.add_scheduled_event("面试", ts(10, 28, 14))
    env.scheduler.tick(now=ts(10, 28, 14))
    assert env.scheduler.tick(now=ts(10, 28, 17)) == "skipped"

    assert env.store.list_events(status="pending") == []
    assert env.store.list_events(status="skipped")
    env.store.close()


def test_scheduled_send_failure_is_retried(tmp_path):
    env = build(tmp_path, sender_ok=False)
    env.store.add_scheduled_event("面试", ts(10, 28, 14))
    assert env.scheduler.tick(now=ts(10, 28, 14)) == "failed"

    pending = env.store.list_events(status="pending")
    assert len(pending) == 1  # 还留着，晚点再试
    assert pending[0]["attempts"] == 1
    env.store.close()


def test_scheduler_ticks_immediately_on_start(tmp_path):
    """等满一个周期的话，开机后到点的事件要白等十分钟。"""
    env = build(tmp_path)
    calls = []
    env.scheduler.tick = lambda now=None: calls.append(now) or "blocked"

    stop = threading.Event()
    thread = threading.Thread(target=env.scheduler.run, args=(stop,), daemon=True)
    thread.start()
    deadline = time.monotonic() + 2
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    thread.join(timeout=2)

    assert calls, "启动后没有立刻检查一次"
    env.store.close()


def test_concurrent_sends_do_not_interleave(tmp_path):
    """主动消息线程和消息线程同时发时，一条回复不能被劈成两半插进另一条。"""
    env = build(tmp_path)
    events = []
    inside = threading.Event()
    release = threading.Event()

    class BlockingSender:
        def send_private(self, user_id, text):
            events.append(("start", text))
            if len(events) == 1:
                inside.set()
                release.wait(timeout=5)
            events.append(("end", text))
            return True

        def send_private_image(self, user_id, image, mode="path"):
            return True

    env.engine.sender = BlockingSender()

    first = threading.Thread(target=env.engine.send_proactive, args=("100", "第一句"))
    first.start()
    assert inside.wait(timeout=2)

    second = threading.Thread(target=env.engine.send_proactive, args=("100", "第二句"))
    second.start()
    time.sleep(0.05)  # 给第二个线程时间去抢锁
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert events == [
        ("start", "第一句"),
        ("end", "第一句"),
        ("start", "第二句"),
        ("end", "第二句"),
    ]
    env.store.close()


def test_expired_events_are_retired(tmp_path):
    env = build(tmp_path)
    env.store.add_scheduled_event("体检", ts(10, 20, 9))  # 8 天前
    assert env.scheduler.expire_stale(ts(10, 28, 14)) == 1
    assert env.store.list_events(status="expired")
    env.store.close()


def test_prompt_gets_event_name(tmp_path):
    env = build(tmp_path)
    env.scheduler.scheduled_prompt = "现在 {time}，他今天有 {event}"
    content = env.scheduler.build_messages(
        "scheduled", ts(10, 28, 14), event="面试"
    )[-1]["content"]
    assert content == "现在 14:00，他今天有 面试"
    env.store.close()


# ---------------------------------------------------------------- /event 指令
def test_event_command_adds_and_lists(tmp_path):
    env = build(tmp_path)
    reply = env.engine._handle_command("/event 2026-11-04 09:00 面试", "100")
    assert "记住了" in reply

    listed = env.engine._handle_command("/events", "100")
    assert "面试" in listed
    assert "11-04 09:00" in listed
    env.store.close()


def test_event_command_removes(tmp_path):
    env = build(tmp_path)
    env.engine._handle_command("/event 2026-11-04 面试", "100")
    reply = env.engine._handle_command("/event rm 1", "100")
    assert "删掉" in reply
    assert env.store.list_events(status="pending") == []
    env.store.close()


def test_event_command_rejects_bad_date(tmp_path):
    env = build(tmp_path)
    assert "日期" in env.engine._handle_command("/event 明天 面试", "100")
    env.store.close()


def test_event_now_makes_it_due_immediately(tmp_path):
    env = build(tmp_path)
    env.engine._handle_command("/event now 面试", "100")
    event = env.store.list_events()[0]
    assert event["trigger_at"] <= int(time.time()) + 1
    env.store.close()


def test_events_list_empty(tmp_path):
    env = build(tmp_path)
    assert "还没记下" in env.engine._handle_command("/events", "100")
    env.store.close()


# ---------------------------------------------------------------- 情绪决定动作
def attach_mood(env, **values):
    from qqbot.mood import Mood

    env.engine.mood = Mood(env.store, env.settings, clock=lambda: ts(10, 28, 14))
    state = {"miss": 0.15, "warmth": 0.35, "tired": 0.2}
    state.update(values)
    env.engine.mood.save(state, ts(10, 28, 14))
    return env.engine.mood


def test_tired_blocks_her_own_chatter(tmp_path):
    """累了她就不主动找人说话——这不是语气问题，是行为。"""
    env = build(tmp_path)
    attach_mood(env, tired=0.9)
    seed_silence(env)

    assert env.scheduler.gate(ts(10, 28, 14)) is None
    assert env.scheduler._decide(ts(10, 28, 14))[1] == "累了"


def test_tired_does_not_cancel_a_promise(tmp_path):
    """但答应过他的事照说："今天面试"不能因为她累了就没提。"""
    env = build(tmp_path)
    attach_mood(env, tired=0.9)
    seed_silence(env)
    env.store.add_scheduled_event("今天面试", ts(10, 28, 13))

    assert env.scheduler.gate(ts(10, 28, 14)) == "scheduled"


def test_missing_him_lets_her_speak_earlier(tmp_path):
    env = build(tmp_path)
    attach_mood(env, miss=0.9)
    seed_silence(env, hours_ago=3.5)  # 默认要 5 小时，很想他时 3 小时就够

    assert env.scheduler.gate(ts(10, 28, 14)) == "followup"


def test_good_mood_shortens_a_little(tmp_path):
    env = build(tmp_path)
    attach_mood(env, warmth=0.8)
    seed_silence(env, hours_ago=4.2)  # 心情好时 4 小时就够

    assert env.scheduler.gate(ts(10, 28, 14)) == "followup"


def test_short_silence_still_blocks_when_calm(tmp_path):
    env = build(tmp_path)
    attach_mood(env)
    seed_silence(env, hours_ago=3.5)

    kind, reason = env.scheduler._decide(ts(10, 28, 14))

    assert kind is None
    assert "静默" in reason
