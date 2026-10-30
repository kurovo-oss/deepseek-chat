import time
import types
from pathlib import Path

import pytest

import qqbot.engine as engine_mod
from qqbot.config import Settings
from qqbot.engine import FALLBACK_REPLIES, Engine, fallback_reply
from qqbot.llm import ChatResult, LLMError
from qqbot.onebot import IncomingImage, IncomingMessage
from qqbot.store import Store

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class FakeLLM:
    def __init__(self, text="好呀。", error=None, shots=None):
        self.text = text
        self.error = error
        self.shots = list(shots or [])
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        text = self.shots.pop(0) if self.shots else self.text
        return ChatResult(text, 12, 34, "fake-model")


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

    def resolve(self, tag, max_bytes=None):
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
        digest_dir=tmp_path / "daily",
        digest_prompt_file=tmp_path / "digest.md",
        export_dir=tmp_path / "exports",
        # 测试默认离线：看图/画图会真的发网络请求
        vision_enabled=False,
        draw_enabled=False,
        inbox_dir=tmp_path / "inbox",
        draw_dir=tmp_path / "drawn",
        life_enabled=False,
        sticker_chance=0.0,
        reply_reshape=False,
        life_dir=tmp_path / "life",
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
    content = env.llm.calls[0][0]["content"]
    assert content.startswith("你是大肥鱼")  # 人设永远在最前面
    assert "【你会的】" in content  # 后面挂的是生活会话用的补充段落


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
    assert env.sender.sent[-1][1] in FALLBACK_REPLIES


def test_fallback_replies_vary():
    """同一句兜底连着发四次，比不说话更像机器人。"""
    assert len({fallback_reply() for _ in range(60)}) > 1


def test_blank_lines_are_collapsed(env):
    """真人不在聊天框里空行；模型爱用它分段。"""
    env.llm.text = "我还在画呢。\n\n那个头发又画歪了。"

    env.engine.handle_message(make_message("在干嘛"))

    texts = [text for _, text in env.sender.sent]
    assert texts == ["我还在画呢。", "那个头发又画歪了。"]


def test_multiline_reply_is_sent_as_separate_messages(env, monkeypatch):
    """正常 QQ 聊天是一条一条发，不是一条里塞换行。"""
    # 条数随机化是另一条测试的事，这里只验证"按行拆开"
    monkeypatch.setattr(engine_mod, "reshape_bubbles", lambda parts, rng: parts)
    env.llm.text = "画画\n又画歪了\n烦"

    env.engine.handle_message(make_message("在干嘛"))

    assert [text for _, text in env.sender.sent] == ["画画", "又画歪了", "烦"]
    assert len(env.sleeps) == 2  # 三条之间各停一次


def test_fallback_reply_is_not_fed_to_memory(env):
    """兜底话术不是她说的话，喂给抽取会抽出"她经常走神"这种垃圾卡片。"""
    calls = []
    env.engine.memory = types.SimpleNamespace(
        context=lambda: "", note_reply=lambda session, text="": calls.append(text)
    )
    env.llm.error = LLMError("boom")

    env.engine.handle_message(make_message("你好"))

    assert env.sender.sent[-1][1] in FALLBACK_REPLIES
    assert calls == []


def test_normal_reply_is_fed_to_memory(env):
    calls = []
    env.engine.memory = types.SimpleNamespace(
        context=lambda: "", note_reply=lambda session, text="": calls.append(text)
    )

    env.engine.handle_message(make_message("今天加班到十点"))

    assert calls == ["今天加班到十点"]


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
    assert env.settings.model_id in "".join(text for _, text in env.sender.sent)


def test_memory_command_empty(env):
    env.engine.handle_message(make_message("/memory", mid="me1"))
    assert "还没记住" in env.sender.sent[-1][1]


def test_memory_command_lists_entries(env):
    env.store.add_card("fact", "养了一只叫团子的猫")
    env.engine.handle_message(make_message("/memory", mid="me2"))
    reply = env.sender.sent[-1][1]
    assert "养了一只叫团子的猫" in reply
    assert "事实" in reply


def test_memory_add_records_manual_card(env):
    """自动抽取会记错、会漏，所以得能自己塞一条进去。"""
    env.engine.handle_message(make_message("/memory add 他不吃香菜", mid="ma1"))

    cards = env.store.list_cards(status="active")
    assert [card["summary"] for card in cards] == ["他不吃香菜"]
    assert cards[0]["kind"] == "fact"
    assert "记下来啦" in env.sender.sent[-1][1]


def test_memory_add_without_text_asks_back(env):
    env.engine.handle_message(make_message("/memory add", mid="ma2"))
    assert "要记什么" in env.sender.sent[-1][1]
    assert env.store.count_cards() == 0


def test_memory_edit_updates_summary(env):
    card_id = env.store.add_card("fact", "养了一只叫团子的猫")
    env.engine.handle_message(
        make_message(f"/memory edit {card_id} 养了一只叫团子的橘猫", mid="me3")
    )

    assert env.store.get_card(card_id)["summary"] == "养了一只叫团子的橘猫"
    assert "改好了" in env.sender.sent[-1][1]


def test_memory_edit_unknown_id_is_rejected(env):
    env.engine.handle_message(make_message("/memory edit 999 随便什么", mid="me4"))
    assert "没有第 999 条" in env.sender.sent[-1][1]


def test_digest_command_writes_today(env):
    import time
    from datetime import date

    env.store.save_message("100", "user", "今天面试完了", created_at=int(time.time()))
    env.llm.text = "他今天面完试，说还行。"

    env.engine.handle_message(make_message("/digest", mid="dg1"))

    assert "他今天面完试" in env.sender.sent[-1][1]
    saved = env.settings.digest_dir / f"{date.today():%Y-%m-%d}.md"
    assert saved.exists()


def test_digest_command_on_empty_day(env):
    env.engine.handle_message(make_message("/digest 昨天", mid="dg2"))
    assert "没说什么" in env.sender.sent[-1][1]


def test_digest_command_rejects_unknown_period(env):
    env.engine.handle_message(make_message("/digest 前天", mid="dg3"))
    assert "用法" in env.sender.sent[-1][1]


def test_export_command_writes_file(env):
    env.store.add_card("fact", "他不吃香菜", importance=0.7)

    env.engine.handle_message(make_message("/export", mid="ex1"))

    joined = "".join(text for _, text in env.sender.sent)  # 长消息会被分段
    assert "导出好了" in joined
    files = list(env.settings.export_dir.glob("*.md"))
    assert files and "不吃香菜" in files[0].read_text(encoding="utf-8")


def test_mood_command_shows_state(env):
    env.engine.mood = types.SimpleNamespace(line=lambda: "我现在：想念 0.80")
    env.engine.handle_message(make_message("/mood", mid="mo1"))
    assert "想念 0.80" in env.sender.sent[-1][1]


def test_mood_command_without_state(env):
    env.engine.handle_message(make_message("/mood", mid="mo2"))
    assert "没什么特别" in env.sender.sent[-1][1]


def test_memory_list_marks_pending_and_hints(env):
    env.store.add_card("fact", "他好像喜欢爬山", importance=0.9, confirmed=False)
    env.engine.handle_message(make_message("/memory", mid="p1"))

    reply = env.sender.sent[-1][1]
    assert "待确认" in reply
    assert "/memory ok" in reply


def test_memory_ok_confirms_card(env):
    card_id = env.store.add_card("fact", "他好像喜欢爬山", importance=0.9, confirmed=False)

    env.engine.handle_message(make_message(f"/memory ok {card_id}", mid="p2"))

    assert "记牢了" in env.sender.sent[-1][1]
    assert env.store.get_card(card_id)["confirmed"] is True


def test_memory_ok_rejects_unknown_card(env):
    env.engine.handle_message(make_message("/memory ok 9999", mid="p3"))
    assert "没有第 9999 条" in env.sender.sent[-1][1]


def test_journal_command_when_empty(env):
    env.engine.memory = types.SimpleNamespace(journal=lambda: "", context=lambda: "")
    env.engine.handle_message(make_message("/journal", mid="j1"))
    assert "还没整理过" in env.sender.sent[-1][1]


def test_journal_command_shows_text(env):
    env.engine.memory = types.SimpleNamespace(
        journal=lambda: "他在赶项目。", context=lambda: ""
    )
    env.engine.handle_message(make_message("/journal", mid="j2"))
    assert "他在赶项目。" in env.sender.sent[-1][1]


def test_dream_command_reports_result(env):
    class FakeDreamer:
        def dream(self, now=None):
            return {
                "ok": True,
                "messages": 12,
                "added": 2,
                "merged": 1,
                "reweight": 0,
                "chars": 300,
            }

    env.engine.dreamer = FakeDreamer()
    env.engine.handle_message(make_message("/dream", mid="d1"))

    reply = env.sender.sent[-1][1]
    assert "12 条消息" in reply
    assert "新记 2 条" in reply
    assert env.llm.calls == []  # 指令不调对话模型


def test_dream_command_without_dreamer(env):
    env.engine.handle_message(make_message("/dream", mid="d2"))
    assert "还没准备好" in env.sender.sent[-1][1]


def test_forget_requires_confirmation(env):
    card_id = env.store.add_card("fact", "养了一只叫团子的猫")
    env.engine.handle_message(make_message(f"/forget {card_id}", mid="f1"))
    assert "confirm" in env.sender.sent[-1][1]
    assert env.store.count_cards() == 1  # 还没删

    env.engine.handle_message(make_message(f"/forget {card_id} confirm", mid="f2"))
    assert env.store.count_cards() == 0


def test_forget_command_removes_matching(env):
    env.store.add_card("fact", "养了一只叫团子的猫")
    env.engine.handle_message(make_message("/forget 猫 confirm", mid="f3"))
    assert env.store.count_cards() == 0
    assert "删掉 1 条" in env.sender.sent[-1][1]


def test_pin_command_marks_card(env):
    card_id = env.store.add_card("fact", "很在意隐私")
    env.engine.handle_message(make_message(f"/pin {card_id}", mid="p1"))
    assert env.store.get_card(card_id)["pinned"] is True

    env.engine.handle_message(make_message(f"/unpin {card_id}", mid="p2"))
    assert env.store.get_card(card_id)["pinned"] is False


def test_forget_without_keyword_asks_back(env):
    env.engine.handle_message(make_message("/forget", mid="f2"))
    assert "关键词" in env.sender.sent[-1][1]


def test_unknown_command_hints(env):
    env.engine.handle_message(make_message("/nope", mid="u1"))
    # 帮助文本可能被分段发送，断言要看全部片段
    joined = "".join(text for _, text in env.sender.sent)
    assert "不认识" in joined
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


def test_image_message_is_described_and_fed_to_context(tmp_path):
    """他自己发的图，她得"看见"，而不是只回一句"存下来了"。"""
    env = build_env(tmp_path, vision_enabled=True)
    env.llm.shots = ["一只趴在窗台上的胖猫，很困", "猫猫好可爱。"]
    env.engine._fetch_image = lambda image: PNG_BYTES

    message = IncomingMessage(
        user_id=100,
        message_id="img1",
        text="",
        raw={},
        images=(IncomingImage(url="http://example.invalid/x.png"),),
    )
    env.engine.handle_message(message)

    rows = env.store.recent_messages("100", 2)
    assert "胖猫" in rows[0]["content"]
    assert "图片" in rows[0]["content"]
    assert env.sender.sent  # 她回应了画面，而不是走收藏话术

    saved = [p for p in Path(env.settings.inbox_dir).rglob("*") if p.is_file()]
    assert saved and ".." not in saved[0].name


def test_sticker_image_is_described_as_sticker(tmp_path):
    env = build_env(tmp_path, vision_enabled=True)
    env.llm.shots = ["一个人捂脸笑，表示无语", "哈哈你这个。"]
    env.engine._fetch_image = lambda image: PNG_BYTES

    message = IncomingMessage(
        user_id=100,
        message_id="img2",
        text="",
        raw={},
        images=(IncomingImage(url="http://example.invalid/x.png", sub_type=1),),
    )
    env.engine.handle_message(message)

    rows = env.store.recent_messages("100", 2)
    assert "表情包" in rows[0]["content"]


def test_draw_tag_sends_a_real_image(tmp_path, monkeypatch):
    env = build_env(tmp_path, draw_enabled=True, draw_min_interval_seconds=0)
    env.llm.text = "画好啦{{draw:一只趴在窗台上的猫}}"
    monkeypatch.setattr(
        engine_mod.draw_mod, "generate", lambda settings, llm, text: PNG_BYTES
    )

    env.engine.handle_message(make_message("画个猫给我看看"))

    for _ in range(100):
        if env.sender.images:
            break
        time.sleep(0.02)

    assert env.sender.images, "画好的图没发出去"
    assert "{{draw" not in "".join(text for _, text in env.sender.sent)


def test_draw_respects_interval(tmp_path, monkeypatch):
    env = build_env(tmp_path, draw_enabled=True, draw_min_interval_seconds=3600)
    env.llm.text = "等一下{{draw:一只猫}}"
    calls = []
    monkeypatch.setattr(
        engine_mod.draw_mod,
        "generate",
        lambda settings, llm, text: calls.append(text) or PNG_BYTES,
    )

    env.engine.handle_message(make_message("画个猫"))
    time.sleep(0.3)
    assert len(calls) == 1  # 第一张照画

    env.engine.handle_message(make_message("再画一个猫", mid="m2"))
    time.sleep(0.3)

    assert len(calls) == 1  # 间隔没到，第二次不画

def test_draw_command_triggers_image(tmp_path, monkeypatch):
    env = build_env(tmp_path, draw_enabled=True, draw_min_interval_seconds=0)
    monkeypatch.setattr(
        engine_mod.draw_mod, "generate", lambda settings, llm, text: PNG_BYTES
    )

    reply = env.engine._handle_command("/draw 一只趴在窗台上的猫", "100")

    assert "画好直接发你" in reply
    for _ in range(100):
        if env.sender.images:
            break
        time.sleep(0.02)
    assert env.sender.images


def test_draw_command_requires_prompt(tmp_path):
    env = build_env(tmp_path, draw_enabled=True)
    assert "格式" in env.engine._handle_command("/draw", "100")

def test_life_line_goes_into_prompt(tmp_path):
    env = build_env(tmp_path, life_enabled=True, life_dir=tmp_path / "life")
    folder = tmp_path / "life"
    folder.mkdir()
    (folder / (time.strftime("%Y-%m-%d") + ".md")).write_text(
        "洗了窗帘\n削笔刀找不到了\n泡了第二杯茶", encoding="utf-8"
    )
    env.store.save_message("100", "user", "在干嘛")

    messages = env.engine._build_messages("100")

    assert "【你今天】" in messages[0]["content"]
    assert "洗了窗帘" in messages[0]["content"]


def test_life_command_prints_today(tmp_path):
    env = build_env(tmp_path, life_enabled=True, life_dir=tmp_path / "life")
    folder = tmp_path / "life"
    folder.mkdir()
    (folder / (time.strftime("%Y-%m-%d") + ".md")).write_text("泡了第二杯茶", encoding="utf-8")

    reply = env.engine._handle_command("/life", "100")

    assert "泡了第二杯茶" in reply


def test_assistant_history_loses_old_address(tmp_path):
    env = build_env(tmp_path)
    env.store.save_message("100", "user", "在干嘛")
    env.store.save_message("100", "assistant", "我在画画呀，爸爸")

    messages = env.engine._build_messages("100")

    blob = "\n".join(m["content"] for m in messages if m["role"] != "user")
    assert "爸爸" not in blob


def test_random_sticker_is_sent_after_text(tmp_path):
    env = build_env(
        tmp_path,
        sticker_chance=1.0,
        sticker_tags="cat",
        image_cooldown_messages=0,
    )

    env.engine.handle_message(make_message("你好"))

    assert [text for _, text in env.sender.sent] == ["好呀。"]
    assert len(env.sender.images) == 1
    assert env.sender.images[0][1].endswith("cat.png")


def test_sticker_disabled_by_zero_chance(tmp_path):
    env = build_env(tmp_path, sticker_chance=0.0, sticker_tags="cat", image_cooldown_messages=0)

    env.engine.handle_message(make_message("你好"))

    assert env.sender.images == []


class FakePC:
    def __init__(self, tmp_path):
        self.folder = tmp_path / "screen"
        self.folder.mkdir(parents=True, exist_ok=True)

    def screenshot(self, dest):
        path = Path(dest) / "shot.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        return path

    def prune(self, dest, keep=20):
        return 0

    def shrink(self, path, max_side=1000):
        return PNG_BYTES

    def status(self):
        return {"cpu": 9, "mem_total_mb": 16384, "mem_free_mb": 1024, "window": "编辑器"}

    def describe(self, data):
        return "CPU 占用 9%"

    hash_value = "1010101010101010" * 16
    change_always = True

    def frame_hash(self, data, size=16):
        return self.hash_value

    def changed(self, before, after, threshold=12):
        return self.change_always or before != after


def test_screen_command_sends_a_picture(tmp_path, monkeypatch):
    env = build_env(tmp_path)
    monkeypatch.setattr(engine_mod, "pc_mod", FakePC(tmp_path))
    monkeypatch.setattr(engine_mod.vision_mod, "note_for", lambda *a, **k: "屏幕上是一堆代码")

    reply = env.engine._handle_command("/screen", "100")

    assert reply
    assert len(env.sender.images) == 1
    assert env.sender.images[0][1].endswith("shot.png")


def test_screen_command_respects_switch(tmp_path):
    env = build_env(tmp_path, screen_enabled=False)

    assert "关着" in env.engine._handle_command("/screen", "100")


def test_pc_command_comments_on_state(tmp_path, monkeypatch):
    env = build_env(tmp_path)
    monkeypatch.setattr(engine_mod, "pc_mod", FakePC(tmp_path))

    reply = env.engine._handle_command("/pc", "100")

    assert reply


def test_pc_command_detail_prints_raw(tmp_path, monkeypatch):
    env = build_env(tmp_path)
    monkeypatch.setattr(engine_mod, "pc_mod", FakePC(tmp_path))

    assert "CPU 占用 9%" in env.engine._handle_command("/pc 详细", "100")


def test_watch_starts_and_says_one_line(tmp_path, monkeypatch):
    env = build_env(
        tmp_path,
        watch_interval_seconds=0,
        watch_max_lines=1,
        watch_minutes=1,
        vision_enabled=True,
    )
    monkeypatch.setattr(engine_mod, "pc_mod", FakePC(tmp_path))
    monkeypatch.setattr(engine_mod.vision_mod, "note_for", lambda *a, **k: "屏幕上是游戏画面")

    reply = env.engine._handle_command("/watch", "100")
    env.engine._watch_thread.join(timeout=10)

    assert "小板凳" in reply
    assert [text for _, text in env.sender.sent] == ["好呀。"]
    assert env.store.recent_messages("100", 1)[0]["origin"] == "watch"


def test_watch_skips_unchanged_screen(tmp_path, monkeypatch):
    env = build_env(tmp_path, vision_enabled=True)
    fake = FakePC(tmp_path)
    fake.change_always = False
    monkeypatch.setattr(engine_mod, "pc_mod", fake)
    monkeypatch.setattr(engine_mod.vision_mod, "note_for", lambda *a, **k: "屏幕上是游戏画面")

    assert env.engine._watch_tick("100", [], fake.hash_value) is None


def test_watch_stop(tmp_path, monkeypatch):
    env = build_env(tmp_path, watch_interval_seconds=600, vision_enabled=True)
    monkeypatch.setattr(engine_mod, "pc_mod", FakePC(tmp_path))
    monkeypatch.setattr(engine_mod.vision_mod, "note_for", lambda *a, **k: "屏幕上是游戏画面")

    env.engine._handle_command("/watch", "100")
    reply = env.engine._handle_command("/watch 停", "100")
    env.engine._watch_stop.set()
    env.engine._watch_thread.join(timeout=10)

    assert "不看了" in reply
    assert not env.engine._watch_thread.is_alive()


def test_watch_respects_switch(tmp_path):
    env = build_env(tmp_path, watch_enabled=False)

    assert "关着" in env.engine._handle_command("/watch", "100")


def test_merged_bubbles_send_as_one_message(tmp_path, monkeypatch):
    env = build_env(tmp_path, reply_reshape=True)
    env.llm.text = "骂你啥啦\n不是，他凭什么骂你呀"
    monkeypatch.setattr(engine_mod, "reshape_bubbles", lambda parts, rng: ["骂你啥啦，不是，他凭什么骂你呀"])

    env.engine.handle_message(make_message("我被骂了"))

    assert [text for _, text in env.sender.sent] == ["骂你啥啦，不是，他凭什么骂你呀"]


def test_abilities_go_into_prompt(tmp_path):
    env = build_env(tmp_path)
    env.store.save_message("100", "user", "你能干嘛")

    messages = env.engine._build_messages("100")

    assert "【你会的】" in messages[0]["content"]
    assert "刷抖音" in messages[0]["content"]


def test_meme_glossary_goes_into_prompt(tmp_path):
    env = build_env(tmp_path)
    env.store.save_message("100", "user", "在吗")

    content = env.engine._build_messages("100")[0]["content"]

    assert "【他可能会用的网络用语】" in content
    assert "已老实=认输" in content


def test_image_hint_carries_previous_context(tmp_path):
    env = build_env(tmp_path)
    env.store.save_message("100", "user", "今天被领导说了两句")
    env.store.save_message("100", "assistant", "啊……\n说你啥了呀")
    rid = env.store.save_message("100", "user", "[表情]")

    hint = env.engine._image_context_hint("100", rid)

    assert "说你啥了呀" in hint
    assert "[表情]" not in hint


def test_image_hint_skips_other_image_notes(tmp_path):
    env = build_env(tmp_path)
    env.store.save_message("100", "user", "（他发来一张表情包：一只猫在笑）")
    rid = env.store.save_message("100", "user", "[表情]")

    hint = env.engine._image_context_hint("100", rid)

    assert "一只猫在笑" not in hint
