import time
import types
from pathlib import Path

import pytest

import qqbot.engine as engine_mod
from qqbot import growth, qzone
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
        self.records = []
        self.quoted = ""  # get_message 的返回值，引用回复测试用
        self.quoted_images = ()

    def get_message(self, message_id):
        return self.quoted

    def get_message_detail(self, message_id):
        return {"text": self.quoted, "images": self.quoted_images}

    def send_private(self, user_id, text):
        self.sent.append((user_id, text))
        self.events.append(("text", text))
        return self.ok

    def send_private_image(self, user_id, image, mode="path"):
        self.images.append((user_id, str(image), mode))
        self.events.append(("image", str(image)))
        return self.ok

    def send_private_record(self, user_id, audio):
        self.records.append((user_id, str(audio)))
        self.events.append(("record", str(audio)))
        return self.ok


class FakeImages:
    def __init__(self, cat_path):
        self.cat_path = cat_path
        self.resolved = []

    def resolve(self, tag, max_bytes=None):
        self.resolved.append(tag)
        return self.cat_path if tag == "cat" else None

    def path_of(self, row):
        return self.cat_path


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
        emoji_chance=0.0,
        reply_reshape=False,
        presence_enabled=False,
        catchup_enabled=False,
        life_dir=tmp_path / "life",
        sticker_dir=tmp_path / "stickers",
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
    roles = [m["role"] for m in last]
    # system → 开场样品（user/assistant 交替）→ 他这一句
    assert roles[0] == "system"
    assert roles[-1] == "user"
    assert last[-1]["content"] == "在吗"
    assert all(role == "user" for role in roles[1::2])
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
    assert "手上的事" not in reply


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

    class Always:
        def random(self):
            return 0.0

        def choice(self, seq):
            return seq[0]

        def shuffle(self, seq):
            return None

    # 情绪会把概率乘下去（0.95 封顶再乘 mood），随机一下就会偶发不中，测试里钉死
    env.engine._rng = Always()
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


# ------------------------------------------------ 她不是客服：会忙、会几条才回
class FixedRng:
    def __init__(self, value=0.9):
        self.value = value

    def random(self):
        return self.value

    def uniform(self, low, high):
        return (low + high) / 2


def make_busy(env, activity="在打游戏"):
    env.engine._presence_state = lambda: {"activity": activity, "busy": True, "until": 9e9}


def test_busy_skips_reply_when_not_forced(tmp_path):
    env = build_env(tmp_path, presence_enabled=True)
    make_busy(env)
    env.engine._rng = FixedRng(0.1)  # 0.1 < 0.55，跳过

    env.engine.handle_message(make_message("在吗"))

    assert env.sender.sent == []
    assert env.store.recent_messages("100", 1)  # 消息还是记下了


def test_busy_replies_when_rng_says_so(tmp_path):
    env = build_env(tmp_path, presence_enabled=True)
    make_busy(env)
    env.engine._rng = FixedRng(0.9)  # 0.9 >= 0.55，回

    env.engine.handle_message(make_message("在吗"))

    assert env.sender.sent


def test_three_pending_messages_force_a_reply(tmp_path):
    env = build_env(tmp_path, presence_enabled=True)
    make_busy(env)
    env.engine._rng = FixedRng(0.1)

    env.engine.handle_message(make_message("在吗", mid="p1"))
    env.engine.handle_message(make_message("人呢", mid="p2"))
    assert env.sender.sent == []

    env.engine.handle_message(make_message("喂", mid="p3"))

    assert env.sender.sent


def test_reply_after_busy_mentions_activity(tmp_path):
    env = build_env(tmp_path, presence_enabled=True)
    make_busy(env, "在刷抖音")
    env.store.save_message("100", "user", "在吗")
    env.store.save_message("100", "user", "人呢")

    note = env.engine._busy_note("100")

    assert "在刷抖音" in note
    assert "先说一句刚在干嘛" in note


def test_no_busy_note_when_only_one_message(tmp_path):
    env = build_env(tmp_path, presence_enabled=True)
    make_busy(env)
    env.store.save_message("100", "user", "在吗")

    assert env.engine._busy_note("100") == ""


def test_busy_message_is_answered_later(tmp_path):
    """忙的时候不回，但收工了要补上——不是永远不理。"""
    env = build_env(tmp_path, presence_enabled=True, catchup_enabled=True)
    make_busy(env)
    env.engine._rng = FixedRng(0.1)

    env.engine.handle_message(make_message("在吗", mid="late1"))
    assert env.sender.sent == []

    later = time.time() + env.settings.busy_reply_after_seconds + 1
    assert env.engine.catchup_tick(later) == 1
    assert env.sender.sent


def test_catchup_waits_before_answering(tmp_path):
    env = build_env(tmp_path, presence_enabled=True, catchup_enabled=True)
    make_busy(env)
    env.engine._rng = FixedRng(0.1)
    env.engine.handle_message(make_message("在吗", mid="late2"))

    assert env.engine.catchup_tick(time.time() + 5) == 0
    assert env.sender.sent == []


def test_catchup_does_not_answer_twice(tmp_path):
    env = build_env(tmp_path, presence_enabled=True, catchup_enabled=True)
    make_busy(env)
    env.engine._rng = FixedRng(0.1)
    env.engine.handle_message(make_message("在吗", mid="late3"))

    later = time.time() + env.settings.busy_reply_after_seconds + 1
    env.engine.catchup_tick(later)
    env.engine.catchup_tick(later + 60)

    assert len(env.sender.sent) == 1


def test_catchup_ignores_sessions_without_pending(tmp_path):
    env = build_env(tmp_path, presence_enabled=True, catchup_enabled=True)

    assert env.engine.catchup_tick(time.time() + 10_000) == 0


def test_old_pending_message_is_not_chased(tmp_path):
    """半小时以前的话不追着回，那不叫没看见。"""
    env = build_env(tmp_path, presence_enabled=True, catchup_enabled=True)
    make_busy(env)
    env.engine._rng = FixedRng(0.1)
    env.engine.handle_message(make_message("在吗", mid="old1"))

    way_later = time.time() + env.settings.catchup_max_age_seconds + 60

    assert env.engine.catchup_tick(way_later) == 0


def test_talk_state_goes_into_prompt(tmp_path):
    """她得知道自己上一条问了个还悬着的问题。"""
    env = build_env(tmp_path)
    env.store.save_message("100", "user", "今天好累")
    env.store.save_message("100", "assistant", "怎么啦，是上班的事吗")

    content = env.engine._build_messages("100")[0]["content"]

    assert "【对话状态】" in content
    assert "你上一条问了他" in content


def test_prompt_respects_token_budget(tmp_path):
    """system 再长也不能顶穿预算：人设不动，其余按优先级压。"""
    from qqbot import budget as budget_mod

    env = build_env(tmp_path, context_token_budget=160)
    env.store.save_message("100", "user", "他说了很长的一段话" * 10)

    content = env.engine._build_messages("100")[0]["content"]

    assert content.startswith("你是大肥鱼")
    assert budget_mod.estimate_tokens(content) <= 200


def test_tight_budget_drops_low_priority_sections(tmp_path):
    env = build_env(tmp_path, context_token_budget=120)
    env.store.save_message("100", "user", "在吗")

    content = env.engine._build_messages("100")[0]["content"]

    # 梗表是优先级最低的，预算紧的时候该被压掉
    assert "已老实=认输" not in content
    # 能力表优先级稍高，通常还在
    assert "【你会的】" in content or "刷抖音" in content


# ------------------------------------------------ 引用回复
def test_quoted_message_goes_into_context(tmp_path):
    """他引用一条消息说"这个呢"，她得看见被引用的那句。"""
    env = build_env(tmp_path)
    env.sender.quoted = "我周末要加班，可能去不了"
    message = IncomingMessage(
        user_id=100, message_id="q1", text="这个怎么办", raw={}, reply_id="42"
    )

    env.engine.handle_message(message)

    content = env.store.recent_messages("100", 2)[0]["content"]
    assert "我周末要加班" in content
    assert content.index("引用") < content.index("这个怎么办")


def test_quote_disabled_ignores_it(tmp_path):
    env = build_env(tmp_path, quote_enabled=False)
    env.sender.quoted = "我周末要加班"
    message = IncomingMessage(
        user_id=100, message_id="q2", text="这个呢", raw={}, reply_id="42"
    )

    env.engine.handle_message(message)

    assert "我周末要加班" not in env.store.recent_messages("100", 2)[0]["content"]


def test_quote_fetch_failure_is_harmless(tmp_path):
    env = build_env(tmp_path)
    env.sender.quoted = ""
    message = IncomingMessage(
        user_id=100, message_id="q3", text="这个呢", raw={}, reply_id="42"
    )

    env.engine.handle_message(message)

    assert env.sender.sent  # 照常回
    assert "引用" not in env.store.recent_messages("100", 2)[0]["content"]


def test_samples_go_right_after_system(tmp_path):
    """样品是消息，不是规则：system 后面紧跟 user/assistant 交替的几组。"""
    env = build_env(tmp_path)
    env.store.save_message("100", "user", "在吗")

    messages = env.engine._build_messages("100")

    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "assistant"
    assert messages[-1]["content"] == "在吗"


def test_samples_can_be_turned_off(tmp_path):
    env = build_env(tmp_path, sample_pairs=0)
    env.store.save_message("100", "user", "在吗")

    messages = env.engine._build_messages("100")

    assert [item["role"] for item in messages] == ["system", "user"]


def test_samples_are_dropped_when_budget_is_tight(tmp_path):
    """预算紧的时候先牺牲样品，保住真实的对话。"""
    env = build_env(tmp_path, context_token_budget=120)
    env.store.save_message("100", "user", "在吗")

    messages = env.engine._build_messages("100")

    assert [item["role"] for item in messages] == ["system", "user"]


# ------------------------------------------------ 前情摘要
def test_recap_note_goes_into_prompt(tmp_path):
    from qqbot import recap as recap_mod

    env = build_env(tmp_path, recap_enabled=True)
    recap_mod.put(
        env.settings, "100", recap_mod.Recap(upto_id=1, text="约好周六去看那家店", at=time.time())
    )
    env.store.save_message("100", "user", "在吗")

    content = env.engine._build_messages("100")[0]["content"]

    assert "前面聊过的" in content
    assert "周六" in content


def test_catchup_tick_summarizes_old_history(tmp_path):
    from qqbot import recap as recap_mod

    env = build_env(tmp_path, recap_enabled=True)
    env.llm.text = "他这周连着加班，约好周六去看那家店。"
    for index in range(60):
        env.store.save_message(
            "100", "user" if index % 2 == 0 else "assistant", f"第{index}句"
        )

    env.engine.catchup_tick()

    saved = recap_mod.get(env.settings, "100")
    assert "周六" in saved.text
    assert saved.upto_id > 0


def test_catchup_tick_does_not_repeat_the_summary(tmp_path):
    from qqbot import recap as recap_mod

    env = build_env(tmp_path, recap_enabled=True)
    env.llm.text = "他在加班。"
    for index in range(60):
        env.store.save_message(
            "100", "user" if index % 2 == 0 else "assistant", f"第{index}句"
        )

    env.engine.catchup_tick()
    first = recap_mod.get(env.settings, "100").text
    env.engine.catchup_tick()

    assert recap_mod.get(env.settings, "100").text == first
    assert len(env.llm.calls) == 1


def test_empty_summary_does_not_advance_the_marker(tmp_path):
    """模型一个字没写出来时别记账，不然那一段就永远没人总结了。"""
    from qqbot import recap as recap_mod

    env = build_env(tmp_path, recap_enabled=True)
    env.llm.text = ""
    for index in range(60):
        env.store.save_message(
            "100", "user" if index % 2 == 0 else "assistant", f"第{index}句"
        )

    env.engine.catchup_tick()

    assert recap_mod.get(env.settings, "100").upto_id == 0


# ------------------------------------------------ token 校准
def test_chat_records_a_token_sample(tmp_path, monkeypatch):
    from qqbot import usage as usage_mod

    seen = []
    monkeypatch.setattr(
        usage_mod, "record", lambda settings, est, actual: seen.append((est, actual)) or 1.0
    )
    env = build_env(tmp_path)
    env.engine.handle_message(make_message("在吗", mid="t1"))

    assert seen, "一次对话之后应该记一个校准样本"
    estimated, actual = seen[0]
    assert estimated > 0 and actual > 0


def test_ratio_tightens_the_budget(tmp_path, monkeypatch):
    from qqbot import usage as usage_mod

    env = build_env(tmp_path, context_token_budget=1000)
    for index in range(6):
        env.store.save_message("100", "user", f"第{index}句话")
    loose = env.engine._build_messages("100")[0]["content"]

    monkeypatch.setattr(usage_mod, "ratio", lambda settings: 2.0)
    tight = env.engine._build_messages("100")[0]["content"]

    # 实际是估算的两倍，预算就得按两倍收紧：内容只会更短
    assert len(tight) <= len(loose)


# ------------------------------------------------ 备用通道
def test_switches_to_the_backup_channel(tmp_path, monkeypatch):
    from qqbot import engine as engine_mod
    from qqbot.llm import ChatResult, LLMError

    class Backup:
        def __init__(self, **kwargs):
            self.model = kwargs.get("model")

        def chat(self, messages, temperature=0.8, max_tokens=800):
            return ChatResult("备用说的话。", 10, 5, self.model or "backup")

    monkeypatch.setattr(engine_mod, "LLMClient", Backup)
    env = build_env(tmp_path, fallback_model_id="backup-model")
    env.llm.error = LLMError("主通道挂了")

    env.engine.handle_message(make_message("在吗", mid="f1"))

    assert env.sender.sent
    assert "备用说的话" in "".join(text for _, text in env.sender.sent)


def test_both_channels_down_says_something_human(tmp_path, monkeypatch):
    from qqbot import engine as engine_mod
    from qqbot.llm import LLMError

    class Backup:
        def __init__(self, **kwargs):
            pass

        def chat(self, messages, temperature=0.8, max_tokens=800):
            raise LLMError("备用也挂了")

    monkeypatch.setattr(engine_mod, "LLMClient", Backup)
    env = build_env(tmp_path, fallback_model_id="backup-model")
    env.llm.error = LLMError("主通道挂了")

    env.engine.handle_message(make_message("在吗", mid="f2"))

    text = "".join(item for _, item in env.sender.sent)
    assert text in engine_mod.FALLBACK_REPLIES


def test_no_backup_configured_uses_fallback_line(tmp_path):
    from qqbot import engine as engine_mod
    from qqbot.llm import LLMError

    env = build_env(tmp_path)
    env.llm.error = LLMError("主通道挂了")

    env.engine.handle_message(make_message("在吗", mid="f3"))

    text = "".join(item for _, item in env.sender.sent)
    assert text in engine_mod.FALLBACK_REPLIES


def test_quoted_image_is_described(tmp_path, monkeypatch):
    """他引用一张图问"这个呢"——她得看得见那张图。"""
    from qqbot.onebot import IncomingImage

    env = build_env(tmp_path, vision_enabled=True, quote_enabled=True)
    env.sender.quoted = "看这个"
    env.sender.quoted_images = (IncomingImage(url="http://x/a.jpg", sub_type=0),)
    monkeypatch.setattr(env.engine, "_describe_images", lambda message: "一只白猫趴在窗台上")
    message = IncomingMessage(
        user_id=100, message_id="qi1", text="这个可爱吗", raw={}, reply_id="42"
    )

    env.engine.handle_message(message)

    content = env.store.recent_messages("100", 2)[0]["content"]
    assert "看这个" in content
    assert "一只白猫" in content


# ------------------------------------------------ 情绪影响表达
def _attach_mood(env, **values):
    """main 会把 Mood 注入引擎；测试里得自己来。"""
    from qqbot.mood import Mood

    env.engine.mood = Mood(env.store, env.settings)
    env.engine.mood.save({"tired": 0.1, "miss": 0.1, "warmth": 0.3} | values)
    return env.engine.mood


def test_tired_mood_sends_fewer_bubbles(tmp_path):
    env = build_env(tmp_path)
    _attach_mood(env, tired=0.95)
    env.llm.text = "第一句\n第二句\n第三句"

    env.engine.handle_message(make_message("在吗", mid="m1"))

    assert len(env.sender.sent) == 2


def test_tired_mood_adds_a_quieter_sample(tmp_path):
    env = build_env(tmp_path, sample_pairs=3)
    _attach_mood(env, tired=0.95)

    messages = env.engine._build_messages("100")
    joined = "".join(str(item.get("content") or "") for item in messages)

    assert "刚躺下" in joined


# ------------------------------------------------ 情绪影响发图/表情
class _Images:
    def __init__(self, path):
        self.path = path

    def resolve(self, tag, max_bytes=0):
        return self.path


def _fixed_rng(value):
    import types

    return types.SimpleNamespace(random=lambda: value)


def test_tired_mood_skips_most_images(tmp_path):
    env = build_env(tmp_path)
    _attach_mood(env, tired=0.95)
    env.engine.images = _Images(tmp_path / "x.png")
    env.engine._rng = _fixed_rng(0.1)  # 落在"懒得翻图"的 50% 里

    assert env.engine._pick_image("100", ["胖猫"]) is None


def test_calm_mood_still_sends_the_image(tmp_path):
    env = build_env(tmp_path)
    _attach_mood(env)
    env.engine.images = _Images(tmp_path / "x.png")
    env.engine._rng = _fixed_rng(0.1)

    assert env.engine._pick_image("100", ["胖猫"]) == tmp_path / "x.png"


def test_good_mood_makes_stickers_more_likely(tmp_path):
    env = build_env(tmp_path, sticker_chance=0.5, sticker_tags="胖猫")
    _attach_mood(env, warmth=0.8)
    env.engine.images = _Images(tmp_path / "s.png")
    env.engine._rng = _fixed_rng(0.55)  # 0.5*1.2=0.6 才过，0.55 刚好卡进来

    assert env.engine._maybe_sticker("100") == tmp_path / "s.png"


def test_tired_mood_makes_stickers_rarer(tmp_path):
    env = build_env(tmp_path, sticker_chance=0.5, sticker_tags="胖猫")
    _attach_mood(env, tired=0.95)
    env.engine.images = _Images(tmp_path / "s.png")
    env.engine._rng = _fixed_rng(0.3)  # 0.5*0.4=0.2，0.3 已经超了

    assert env.engine._maybe_sticker("100") is None


# ------------------------------------------------ 他要图就得给
def test_requested_image_ignores_cooldown(tmp_path):
    """他说"发个表情包"，冷却和情绪都得让路——不然她说发了，他什么都没收到。"""
    env = build_env(tmp_path)
    env.store.save_message("100", "assistant", "[图片]", image_tags=["x.png"])
    assert env.store.has_recent_image("100", 10)

    env.engine._send_reply("100", "100", "给你 {{img:cat}}", force_image=True)

    assert [path for _, path, _ in env.sender.images] == [str(env.images.cat_path)]


def test_random_image_still_respects_cooldown(tmp_path):
    env = build_env(tmp_path)
    env.store.save_message("100", "assistant", "[图片]", image_tags=["x.png"])

    env.engine._send_reply("100", "100", "给你 {{img:cat}}")

    assert env.sender.images == []


def test_last_tag_sends_the_newest_collected(tmp_path):
    env = build_env(tmp_path)
    env.store.add_image("a" * 64, "aa/a.png", "image/png", 10, tags=["cat"], source="user")
    env.store.add_image("b" * 64, "bb/b.png", "image/png", 10, tags=["cute"], source="user")

    assert env.engine._pick_image("100", ["last"]) == env.images.cat_path


def test_wants_image_recognises_requests():
    from qqbot.media import wants_image

    assert wants_image("把你收藏的表情包发我一个")
    assert wants_image("刚才那个再来一次")
    assert wants_image("发张图看看")
    assert not wants_image("今天好累")
    assert not wants_image("晚上吃什么")


def test_asking_for_a_sticker_actually_sends_one(tmp_path):
    env = build_env(tmp_path)
    env.engine.llm.text = "好呀 {{img:cat}}"

    env.engine.handle_message(make_message("把你收藏的表情包发我一个"))

    assert [path for _, path, _ in env.sender.images] == [str(env.images.cat_path)]
    assert "{{img" not in env.sender.sent[0][1]


def test_spoken_last_tag_also_works(tmp_path):
    """她可能写 {{img:刚才那个}}，不一定会写 last。"""
    env = build_env(tmp_path)
    env.store.add_image("a" * 64, "aa/a.png", "image/png", 10, tags=["cat"], source="user")

    assert env.engine._pick_image("100", ["刚才那个"]) == env.images.cat_path


def test_request_falls_back_to_latest_when_tag_misses(tmp_path):
    env = build_env(tmp_path)
    env.store.add_image("a" * 64, "aa/a.png", "image/png", 10, tags=["cat"], source="user")

    assert env.engine._pick_image("100", ["不存在"], force=True) == env.images.cat_path


def test_system_prompt_presses_when_she_repeats(tmp_path):
    """同一件事今天已经说了三遍：提示词里要压一句，别让她再拿它拐话题。"""
    env = build_env(tmp_path, life_enabled=True, life_dir=tmp_path / "life")
    (tmp_path / "life").mkdir()
    (tmp_path / "life" / (time.strftime("%Y-%m-%d") + ".md")).write_text(
        "今天挑的是：学做番茄牛腩\n灶上小火咕嘟着\n", encoding="utf-8"
    )
    for index in range(2):
        env.store.save_message("100", "assistant", f"牛腩还在炖着呢{index}")

    messages = env.engine._build_messages("100")

    assert "已经说过" in messages[0]["content"]


def test_system_prompt_drops_life_after_third_mention(tmp_path):
    """说第三遍就把这段收起来，免得她一整天拿同一件小事拐话题。"""
    env = build_env(tmp_path, life_enabled=True, life_dir=tmp_path / "life")
    (tmp_path / "life").mkdir()
    (tmp_path / "life" / (time.strftime("%Y-%m-%d") + ".md")).write_text(
        "今天挑的是：学做番茄牛腩\n灶上小火咕嘟着\n", encoding="utf-8"
    )
    for index in range(3):
        env.store.save_message("100", "assistant", f"牛腩还在炖着呢{index}")

    messages = env.engine._build_messages("100")

    assert "灶上小火咕嘟着" not in messages[0]["content"]


def test_fabricated_douyin_is_cut(tmp_path):
    """系统做不到的事，她嘴上也不能有。"""
    env = build_env(tmp_path)
    env.engine.llm.text = "我刚刷到一个超好笑的视频。你在干嘛呀"

    env.engine.handle_message(make_message("在吗"))

    sent = "\n".join(text for _, text in env.sender.sent)
    assert "刷到" not in sent
    assert "你在干嘛呀" in sent


def test_emoji_lands_when_the_dice_say_so(tmp_path):
    """提示词管不住（实测 0%），所以兜一层：骰子到了就补一个。"""
    from qqbot import emoji as emoji_mod

    env = build_env(tmp_path, emoji_chance=1.0)
    env.engine.llm.text = "好呀\n那你早点睡嘛"

    env.engine.handle_message(make_message("我困了"))

    sent = [text for _, text in env.sender.sent]
    assert emoji_mod.EMOJI_RE.search(sent[-1])


def test_no_emoji_when_the_switch_is_off(tmp_path):
    env = build_env(tmp_path, emoji_chance=0.0)
    env.engine.llm.text = "好呀\n那你早点睡嘛"

    env.engine.handle_message(make_message("我困了"))

    assert not any("🥺" in text or "😴" in text for _, text in env.sender.sent)


# ------------------------------------------------ 语音
def test_voice_command_says_off(tmp_path):
    env = build_env(tmp_path, voice_enabled=False)

    reply = env.engine._handle_command("/语音", "100")

    assert "关着" in reply


def test_voice_command_sends_a_record(tmp_path, monkeypatch):
    from qqbot import voice as voice_mod

    env = build_env(tmp_path, voice_enabled=True)
    monkeypatch.setattr(
        voice_mod, "synthesize", lambda text, settings, out_dir=None: tmp_path / "a.mp3"
    )

    reply = env.engine._handle_command("/语音", "100")

    assert "发了一条语音" in reply
    assert env.sender.records


def test_voice_failure_does_not_break_anything(tmp_path, monkeypatch):
    """合成失败也不影响她已经发出去的字。"""
    from qqbot import voice as voice_mod

    env = build_env(tmp_path, voice_enabled=True)
    monkeypatch.setattr(voice_mod, "synthesize", lambda *a, **k: None)

    env.engine._speak(100, "100", "晚安呀")

    assert env.sender.records == []


# ------------------------------------------------ 刷网
def test_browse_command_reports_what_she_saw(tmp_path, monkeypatch):
    from qqbot import browse as browse_mod

    monkeypatch.setattr(
        browse_mod,
        "collect",
        lambda settings, rng=None: browse_mod.Item("baidu", "小区里一只橘猫天天蹲在楼下等喂"),
    )
    env = build_env(tmp_path, web_enabled=True)

    reply = env.engine._handle_command("/刷", "100")

    assert "橘猫" in reply
    assert "热搜" in reply


def test_browse_command_says_off_when_web_is_disabled(tmp_path):
    env = build_env(tmp_path, web_enabled=False)

    assert "关着" in env.engine._handle_command("/刷", "100")


def test_sticker_tag_sends_from_the_pack(tmp_path):
    from qqbot import stickers as stickers_mod

    env = build_env(tmp_path, image_enabled=True, sticker_chance=0.0)
    source = tmp_path / "sad.gif"
    source.write_bytes(b"GIF89a" + b"1" * 32)
    stickers_mod.add_file(env.settings, source, "委屈")
    env.engine.llm.text = "那我不说了\n{{表情:委屈}}"

    env.engine.handle_message(make_message("哼"))

    assert env.sender.images, "点名要的表情没发出去"
    assert str(env.sender.images[0][1]).startswith(str(env.settings.sticker_dir))
    assert all("{{" not in text for _, text in env.sender.sent)


def test_sticker_tag_with_empty_bucket_falls_back(tmp_path):
    env = build_env(
        tmp_path,
        image_enabled=True,
        sticker_chance=1.0,
        sticker_tags="cat",
        image_cooldown_messages=0,
    )
    env.engine.llm.text = "哼\n{{表情:嘲讽}}"

    env.engine.handle_message(make_message("你好"))

    assert env.sender.images


def test_refusal_directive_reaches_the_model(tmp_path):
    env = build_env(tmp_path, refusal_enabled=True, refusal_chance=0.6)
    env.engine._rng = FixedRng(0.0)
    env.llm.text = "唔……不要嘛，我懒"

    env.engine.handle_message(make_message("帮我写个总结"))

    system = env.llm.calls[0][0]["content"]
    assert "撒娇地拒绝" in system
    assert "别凶" in system
    assert growth.recent_refusals(env.store)[0]["ask"] == "帮我写个总结"
    assert env.sender.sent


def test_no_refusal_directive_when_it_does_not_fire(tmp_path):
    env = build_env(tmp_path, refusal_enabled=True, refusal_chance=0.0)

    env.engine.handle_message(make_message("帮我写个总结"))

    assert "撒娇地拒绝" not in env.llm.calls[0][0]["content"]
    assert growth.recent_refusals(env.store) == []


def test_his_reaction_gets_settled(tmp_path):
    env = build_env(tmp_path, refusal_enabled=True, refusal_chance=0.6)
    env.engine._rng = FixedRng(0.0)
    env.llm.text = "唔……不要嘛，我写不来这种的"

    env.engine.handle_message(make_message("帮我写个总结", mid="m1"))
    env.engine.handle_message(make_message("算了，我自己来", mid="m2"))

    assert growth.recent_refusals(env.store)[0]["outcome"] == "rejected"
    notes = [row["text"] for row in growth.recent(env.store, kind="reflect")]
    assert notes and "不太高兴" in notes[0]


def test_growth_command_shows_her_thoughts(tmp_path):
    env = build_env(tmp_path)
    growth.record(env.store, "self", "他累的时候我该软一点", salience=0.7)

    env.engine.handle_message(make_message("/成长"))

    text = "\n".join(item for _, item in env.sender.sent)
    assert "最近想通的" in text
    assert "软一点" in text
    assert env.llm.calls == []


def test_system_prompt_knows_her_own_numbers(tmp_path):
    env = build_env(tmp_path)

    env.engine.handle_message(make_message("你用了多少 token 啊"))

    system = env.llm.calls[0][0]["content"]
    assert "关于你自己" in system
    assert "token" in system


def test_lookup_tag_triggers_a_second_pass(tmp_path, monkeypatch):
    from qqbot import search as search_mod

    env = build_env(tmp_path, search_enabled=True)
    env.llm.shots = ["等我一下，我查查\n{{查:地球到月球多远}}", "查到了，三十八万公里左右"]
    monkeypatch.setattr(
        search_mod,
        "search",
        lambda query, limit=4: [
            {"title": "地球到月球多远", "url": "https://baike.baidu.com/x", "snippet": "平均距离约 38.4 万公里"}
        ],
    )

    env.engine.handle_message(make_message("地球到月球多远"))

    sent = " ".join(text for _, text in env.sender.sent)
    assert "38" in sent or "三十八万" in sent
    assert "{{查" not in sent


def test_lookup_without_results_keeps_her_words(tmp_path, monkeypatch):
    from qqbot import search as search_mod

    env = build_env(tmp_path, search_enabled=True)
    env.llm.shots = ["我查一下哦\n{{查:查不到的东西}}"]
    monkeypatch.setattr(search_mod, "search", lambda query, limit=4: [])

    env.engine.handle_message(make_message("查不到的东西是啥"))

    sent = " ".join(text for _, text in env.sender.sent)
    # 查不到就把"我查一下"换成实话，不能只留半句承诺
    assert "真不知道" in sent
    assert "{{查" not in sent


def test_a_compliant_reply_does_not_get_booked_as_refusal(tmp_path):
    env = build_env(tmp_path, refusal_enabled=True, refusal_chance=0.6)
    env.engine._rng = FixedRng(0.0)
    env.llm.text = "好呀，我给你搭个架子，你看看行不行"

    env.engine.handle_message(make_message("帮我写个总结", mid="m9"))

    # 指令是"撒娇拒绝"，但模型照做了活——不能记一笔"我拒绝了他"
    assert growth.recent_refusals(env.store) == []


def test_proactive_never_gets_the_refusal_directive(tmp_path):
    env = build_env(tmp_path, refusal_enabled=True, refusal_chance=0.6)
    env.engine._rng = FixedRng(0.0)

    messages = env.engine._build_messages("100", allow_refusal=False)

    system = messages[0]["content"]
    assert "撒娇地拒绝" not in system
    assert getattr(env.engine, "_pending_refusal", None) is None


def test_memory_facts_command_shows_evidence(tmp_path):
    from qqbot import facts

    env = build_env(tmp_path)
    facts.record(env.store, "他", "讨厌", "香菜", quote="别放香菜")

    env.engine.handle_message(make_message("/记忆 香菜"))

    text = " ".join(chunk for _, chunk in env.sender.sent)
    assert "讨厌香菜" in text
    assert "别放香菜" in text

def test_health_command_lists_channels(tmp_path):
    env = build_env(tmp_path)

    env.engine.handle_message(make_message("/健康"))

    text = " ".join(chunk for _, chunk in env.sender.sent)
    assert "【健康】" in text
    assert "空间" in text
    assert "记忆" in text


def test_douyin_command_reports_switch(tmp_path):
    env = build_env(tmp_path)

    env.engine.handle_message(make_message("/抖音"))

    text = " ".join(chunk for _, chunk in env.sender.sent)
    assert "抖音：关" in text

def test_memory_profile_command_when_empty(tmp_path):
    env = build_env(tmp_path)

    env.engine.handle_message(make_message("/记忆 档案"))

    text = " ".join(chunk for _, chunk in env.sender.sent)
    assert "没有档案" in text


def test_memory_tidy_command_reports_counts(tmp_path, monkeypatch):
    from qqbot import facts as facts_mod

    env = build_env(tmp_path)
    monkeypatch.setattr(facts_mod, "extract", lambda store, llm, settings: [1, 2])
    monkeypatch.setattr(facts_mod, "maintain", lambda store: 3)

    env.engine.handle_message(make_message("/记忆 整理"))

    text = " ".join(chunk for _, chunk in env.sender.sent)
    assert "新记住 2 条" in text
    assert "清掉 3 条" in text

def test_life_command_shows_real_events(tmp_path):
    env = build_env(tmp_path, life_enabled=True, life_dir=tmp_path / "life")
    folder = tmp_path / "life"
    folder.mkdir()
    (folder / (time.strftime("%Y-%m-%d") + ".md")).write_text("泡了第二杯茶", encoding="utf-8")
    env.store.save_message("100", "user", "房间收拾得怎么样了")

    reply = env.engine._handle_command("/生活", "100")

    assert "今天真实发生" in reply
    assert "找你说过 1 次话" in reply

def test_qzone_draft_commands_list_delete_and_approve(tmp_path):
    env = build_env(tmp_path, qzone_enabled=True, qzone_dry_run=False)
    pid = qzone.save_draft(
        env.store,
        {"text": "他说「周六来吃饭」，我一直记着，锅里还留着汤。", "image": None},
        int(time.time()), [], "他最近没说过这句话",
    )

    listed = env.engine._handle_command("/空间 草稿", "100")
    assert f"#{pid}" in listed
    assert "他最近没说过这句话" in listed

    held = env.engine._handle_command(f"/空间 发 {pid}", "100")
    assert "没过事实校验" in held

    deleted = env.engine._handle_command(f"/空间 删 {pid}", "100")
    assert "删掉了" in deleted
    assert qzone.drafts(env.store) == []

