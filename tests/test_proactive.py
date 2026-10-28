"""主动消息：闸门、生成、发送、失败不重试。"""

import types
from datetime import datetime

import pytest

from qqbot.config import Settings
from qqbot.engine import Engine
from qqbot.llm import ChatResult, LLMError
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
    env = build(tmp_path)
    seed_silence(env, hours_ago=10)  # 才 10 小时
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
