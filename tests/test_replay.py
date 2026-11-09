"""发送失败重放（M5）：断网 2 分钟恢复后，回复要补上，且不能补两遍。"""

import json
import threading
import time
import types

from qqbot.config import Settings
from qqbot.engine import Engine
from qqbot.replay import ReplyReplayer
from qqbot.store import Store

NOW = 1_800_000_000


class FakeSender:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    def send_private(self, user_id, text):
        self.sent.append((user_id, text))
        return self.ok

    def send_private_image(self, user_id, image, mode="path"):
        return self.ok


def build(tmp_path, sender_ok=True, max_chars=100, **overrides):
    settings = Settings(
        allow_user_id=100,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "d.db",
        system_prompt_file=tmp_path / "s.md",
        segment_delay_ms=0,
        reply_max_chars=max_chars,
        sticker_chance=0.0,
        emoji_chance=0.0,
        reply_reshape=False,
        **overrides,
    )
    store = Store(settings.db_path)
    sender = FakeSender(sender_ok)
    engine = Engine(settings, store, object(), sender, "你是大肥鱼", sleep=lambda _: None)
    replayer = ReplyReplayer(engine, store, settings, clock=lambda: NOW)
    return types.SimpleNamespace(
        settings=settings,
        store=store,
        sender=sender,
        engine=engine,
        replayer=replayer,
    )


def add_failed(env, created_at=None, origin="reply", text="我回来了", image_tags=None, parts=None):
    return env.store.save_message(
        "100",
        "assistant",
        text,
        send_state="failed",
        created_at=NOW - 60 if created_at is None else created_at,
        origin=origin,
        image_tags=image_tags,
        parts=parts,
    )


# ---------------------------------------------------------------- 基本重放
def test_replays_failed_reply(tmp_path):
    env = build(tmp_path)
    add_failed(env)

    assert env.replayer.tick(NOW) == 1
    assert env.sender.sent == [(100, "我回来了")]
    env.store.close()


def test_replay_does_not_create_a_second_message(tmp_path):
    """重放只改状态：原文早就在库里，补发不该在历史里多出一条。"""
    env = build(tmp_path)
    add_failed(env)

    env.replayer.tick(NOW)

    assert len(env.store.recent_messages("100", 10)) == 1
    assert env.store.recent_messages("100", 10)[0]["send_state"] == "sent"
    env.store.close()


def test_successful_replay_is_not_repeated(tmp_path):
    env = build(tmp_path)
    add_failed(env)

    assert env.replayer.tick(NOW) == 1
    assert env.replayer.tick(NOW) == 0
    assert len(env.sender.sent) == 1
    env.store.close()


def test_replay_splits_long_text(tmp_path):
    env = build(tmp_path, max_chars=10)
    add_failed(env, text="啊" * 25)

    env.replayer.tick(NOW)

    assert len(env.sender.sent) == 3
    assert "".join(text for _, text in env.sender.sent) == "啊" * 25
    env.store.close()


# ---------------------------------------------------------------- 不该重放的
def test_old_failure_is_left_alone(tmp_path):
    """半小时后再补一句"嗯嗯"不叫补发，叫莫名其妙。"""
    env = build(tmp_path)
    add_failed(env, created_at=NOW - 3600)

    assert env.replayer.tick(NOW) == 0
    assert env.sender.sent == []
    assert env.store.recent_messages("100", 5)[0]["send_state"] == "failed"
    env.store.close()


def test_proactive_failure_is_not_replayed(tmp_path):
    """主动消息失败不重试——隔几小时冒出来是骚扰。"""
    env = build(tmp_path, sender_ok=True)
    add_failed(env, origin="proactive")

    assert env.replayer.tick(NOW) == 0
    assert env.sender.sent == []
    env.store.close()


# ---------------------------------------------------------------- 放弃条件
def test_gives_up_after_max_attempts(tmp_path):
    env = build(tmp_path, sender_ok=False)
    add_failed(env)

    for _ in range(env.settings.replay_max_attempts):
        assert env.replayer.tick(NOW) == 0
    assert env.store.recent_messages("100", 5)[0]["send_state"] == "failed"

    # 再一轮：不再尝试发送，直接判死
    assert env.replayer.tick(NOW) == 0
    assert len(env.sender.sent) == env.settings.replay_max_attempts
    assert env.store.recent_messages("100", 5)[0]["send_state"] == "dead"
    env.store.close()


def test_replayer_ticks_immediately_on_start(tmp_path):
    env = build(tmp_path)
    calls = []
    env.replayer.tick = lambda now=None: calls.append(now) or 0

    stop = threading.Event()
    thread = threading.Thread(target=env.replayer.run, args=(stop,), daemon=True)
    thread.start()
    deadline = time.monotonic() + 2
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    thread.join(timeout=2)

    assert calls, "启动后没有立刻检查一次"
    env.store.close()

class FlakySender(FakeSender):
    def __init__(self, fail_on=0):
        super().__init__(ok=True)
        self.fail_on = fail_on
        self.calls = []

    def send_private(self, user_id, text):
        self.calls.append(text)
        if self.fail_on and len(self.calls) == self.fail_on:
            return False
        return True


def test_reply_records_how_many_bubbles_were_sent(tmp_path):
    env = build(tmp_path)
    flaky = FlakySender(fail_on=2)
    env.engine.sender = flaky

    ok = env.engine._send_reply(100, "100", "一\n二\n三")

    assert ok is False
    row = env.store.recent_messages("100", 1)[0]
    assert row["send_state"] == "failed"
    assert row["sent_parts"] == 1
    assert json.loads(row["parts_json"]) == ["一", "二", "三"]
    env.store.close()


def test_partial_replay_only_sends_the_rest(tmp_path):
    env = build(tmp_path)
    flaky = FlakySender(fail_on=2)
    env.engine.sender = flaky
    env.engine._send_reply(100, "100", "一\n二\n三")
    env.store._conn.execute("UPDATE messages SET created_at = ?", (NOW - 60,))
    env.store._conn.commit()

    env.engine.sender = FlakySender(fail_on=0)
    assert env.replayer.tick(NOW) == 1

    assert env.engine.sender.calls == ["二", "三"]
    assert env.store.recent_messages("100", 1)[0]["send_state"] == "sent"
    env.store.close()


def test_image_only_failure_is_not_replayed_as_text(tmp_path):
    env = build(tmp_path)
    add_failed(env, text="[图片]", image_tags=["cat.png"], parts=[])

    assert env.replayer.tick(NOW) == 0
    assert env.sender.sent == []
    assert env.store.recent_messages("100", 1)[0]["send_state"] == "dead"
    env.store.close()
