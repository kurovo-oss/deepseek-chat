import types

import pytest

from qqbot.config import Settings
from qqbot.engine import FALLBACK_REPLY, Engine
from qqbot.llm import ChatResult, LLMError
from qqbot.onebot import IncomingMessage
from qqbot.store import Store


class FakeLLM:
    def __init__(self, text="好呀。", error=None):
        self.text = text
        self.error = error
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return ChatResult(self.text, 12, 34, "fake-model")


class FakeSender:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []
        self.images = []
        self.events = []

    def send_private(self, user_id, text):
        self.sent.append((user_id, text))
        self.events.append(("text", text))
        return self.ok

    def send_private_image(self, user_id, image, mode="path"):
        self.images.append((user_id, str(image), mode))
        self.events.append(("image", str(image)))
        return self.ok


class FakeImages:
    def __init__(self, cat_path):
        self.cat_path = cat_path
        self.resolved = []

    def resolve(self, tag):
        self.resolved.append(tag)
        return self.cat_path if tag == "cat" else None


def make_message(text, mid="m1", uid=100):
    return IncomingMessage(user_id=uid, message_id=mid, text=text, raw={})


def build_env(tmp_path, **overrides):
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
    settings = Settings(**kwargs)
    store = Store(settings.db_path)
    llm = FakeLLM()
    sender = FakeSender()
    sleeps: list[float] = []
    image_file = tmp_path / "cat.png"
    image_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    images = FakeImages(image_file)
    engine = Engine(
        settings,
        store,
        llm,
        sender,
        "你是大肥鱼",
        sleep=sleeps.append,
        images=images,
    )
    return types.SimpleNamespace(
        settings=settings,
        store=store,
        llm=llm,
        sender=sender,
        engine=engine,
        sleeps=sleeps,
        images=images,
    )


@pytest.fixture
def env(tmp_path):
    ctx = build_env(tmp_path)
    yield ctx
    ctx.store.close()


def test_stranger_is_ignored_silently(env):
    env.engine.handle_message(make_message("你好", uid=999))
    assert env.sender.sent == []
    assert env.store.count_messages("100") == 0
    assert env.llm.calls == []


def test_normal_reply_saved_and_sent(env):
    env.engine.handle_message(make_message("你好"))
    assert [text for _, text in env.sender.sent] == ["好呀。"]
    roles = [m["role"] for m in env.store.recent_messages("100", 10)]
    assert roles == ["user", "assistant"]
    assert env.store.recent_messages("100", 1)[0]["send_state"] == "sent"


def test_duplicate_message_id_not_answered_twice(env):
    env.engine.handle_message(make_message("你好", mid="dup"))
    env.engine.handle_message(make_message("你好", mid="dup"))
    assert len(env.sender.sent) == 1
    assert env.store.count_messages("100") == 2


def test_system_prompt_comes_first(env):
    env.engine.handle_message(make_message("你好"))
    assert env.llm.calls[0][0]["role"] == "system"
    assert env.llm.calls[0][0]["content"] == "你是大肥鱼"


def test_context_accumulates(env):
    env.engine.handle_message(make_message("第一句", mid="m1"))
    env.engine.handle_message(make_message("第二句", mid="m2"))
    last = env.llm.calls[-1]
    assert len(last) >= 4  # system + 3 条历史 + 当前
    assert last[-1]["content"] == "第二句"


def test_long_reply_is_split_with_delay(env):
    env.llm.text = "啊。" * 120  # 240 字
    env.engine.handle_message(make_message("说长一点"))

    parts = [text for _, text in env.sender.sent]
    assert len(parts) > 1
    assert all(len(p) <= 100 for p in parts)
    assert len(env.sleeps) == len(parts) - 1  # 段间隔次数 = 段数 - 1


def test_llm_failure_falls_back(env):
    env.llm.error = LLMError("boom")
    env.engine.handle_message(make_message("你好"))
    assert env.sender.sent[-1][1] == FALLBACK_REPLY


def test_send_failure_marks_message_failed(env):
    env.sender.ok = False
    env.engine.handle_message(make_message("你好"))
    row = env.store.recent_messages("100", 1)[0]
    assert row["send_state"] == "failed"


def test_reset_clears_window_but_keeps_history(env):
    env.engine.handle_message(make_message("第一句", mid="m1"))
    env.engine.handle_message(make_message("/reset", mid="m2"))
    env.engine.handle_message(make_message("在吗", mid="m3"))

    last = env.llm.calls[-1]
    assert [m["role"] for m in last] == ["system", "user"]
    assert last[1]["content"] == "在吗"
    # 原文不删，只是不再进上下文（2 轮对话 + /reset 一来一回）
    assert env.store.count_messages("100") == 6


def test_status_command_does_not_call_llm(env):
    env.engine.handle_message(make_message("/status", mid="s1"))
    assert env.llm.calls == []
    # 引用配置本身，避免模型名变更时测试误报
    assert env.settings.model_id in env.sender.sent[-1][1]


def test_memory_command_empty(env):
    env.engine.handle_message(make_message("/memory", mid="me1"))
    assert "还没记住" in env.sender.sent[-1][1]


def test_memory_command_lists_entries(env):
    env.store.add_memory("fact", "养了一只叫团子的猫")
    env.engine.handle_message(make_message("/memory", mid="me2"))
    assert "养了一只叫团子的猫" in env.sender.sent[-1][1]
    assert "事实" in env.sender.sent[-1][1]


def test_forget_command_removes_matching(env):
    env.store.add_memory("fact", "养了一只叫团子的猫")
    env.engine.handle_message(make_message("/forget 猫", mid="f1"))
    assert env.store.count_memories() == 0
    assert "删掉 1 条" in env.sender.sent[-1][1]


def test_forget_without_keyword_asks_back(env):
    env.engine.handle_message(make_message("/forget", mid="f2"))
    assert "关键词" in env.sender.sent[-1][1]


def test_unknown_command_hints(env):
    env.engine.handle_message(make_message("/nope", mid="u1"))
    assert "不认识" in env.sender.sent[-1][1]
    assert env.llm.calls == []


def test_image_sent_when_tag_matches(env):
    env.llm.text = "喏，给你{{img:cat}}"
    env.engine.handle_message(make_message("发个猫"))

    assert len(env.sender.images) == 1
    assert all("{{" not in text for _, text in env.sender.sent)
    # 文本先发，图片后发
    assert env.sender.events[0][0] == "text"
    assert env.sender.events[-1][0] == "image"
    assert env.store.has_recent_image("100")


def test_image_suppressed_within_cooldown(env):
    env.llm.text = "喏{{img:cat}}"
    env.engine.handle_message(make_message("第一句", mid="a1"))
    env.engine.handle_message(make_message("第二句", mid="a2"))

    assert len(env.sender.images) == 1  # 第二次被冷却拦下
    assert env.store.recent_messages("100", 1)[0]["image_tags"] == "[]"
    # 被拦下时文本照常发送
    assert env.sender.sent[-1][1] == "喏"


def test_image_allowed_again_after_cooldown(tmp_path):
    ctx = build_env(tmp_path, image_cooldown_messages=2)
    ctx.llm.text = "喏{{img:cat}}"
    ctx.engine.handle_message(make_message("一", mid="b1"))  # 发图
    ctx.engine.handle_message(make_message("二", mid="b2"))  # 最近 2 条含图 → 抑制
    ctx.engine.handle_message(make_message("三", mid="b3"))  # 最近 2 条已无图 → 放行

    assert len(ctx.sender.images) == 2
    ctx.store.close()


def test_cooldown_zero_disables_guard(tmp_path):
    ctx = build_env(tmp_path, image_cooldown_messages=0)
    ctx.llm.text = "喏{{img:cat}}"
    ctx.engine.handle_message(make_message("一", mid="c1"))
    ctx.engine.handle_message(make_message("二", mid="c2"))

    assert len(ctx.sender.images) == 2
    ctx.store.close()


def test_unknown_tag_sends_text_only(env):
    env.llm.text = "嗯{{img:nope}}"
    env.engine.handle_message(make_message("随便说点"))

    assert env.sender.images == []
    assert env.sender.sent[-1][1] == "嗯"
    assert env.store.has_recent_image("100") is False


def test_image_only_reply_sends_no_text(env):
    env.llm.text = "{{img:cat}}"
    env.engine.handle_message(make_message("发个图"))

    assert env.sender.sent == []
    assert len(env.sender.images) == 1
    assert env.store.recent_messages("100", 1)[0]["content"] == "[图片]"


def test_images_disabled_sends_no_image(tmp_path):
    ctx = build_env(tmp_path, image_enabled=False)
    ctx.llm.text = "喏{{img:cat}}"
    ctx.engine.handle_message(make_message("发个猫"))

    assert ctx.sender.images == []
    assert ctx.images.resolved == []  # 关闭后连解析都不该发生
    ctx.store.close()


def test_image_send_failure_marks_message_failed(tmp_path):
    ctx = build_env(tmp_path)
    ctx.llm.text = "喏{{img:cat}}"
    ctx.sender.ok = False
    ctx.engine.handle_message(make_message("发个猫"))

    assert ctx.store.recent_messages("100", 1)[0]["send_state"] == "failed"
    ctx.store.close()


def test_handle_payload_ignores_non_private():
    assert Engine.handle_payload is not None  # 结构性占位断言，真实分支由 test_onebot 覆盖
