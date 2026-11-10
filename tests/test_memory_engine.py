"""记忆与引擎的接合处：注入、抽取时机、压缩、开关。"""

import json
import time
import types

import pytest

from qqbot.config import Settings
from qqbot.engine import Engine
from qqbot.llm import ChatResult, LLMError
from qqbot.memory import BLOCK_TITLE, Memory
from qqbot.onebot import IncomingMessage
from qqbot.store import Store


class ScriptedLLM:
    """按脚本依次返回；脚本用完后抛错，方便暴露多余的调用。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if not self.replies:
            raise LLMError("脚本用完了")
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return ChatResult(item, 10, 5, "fake-model")


class FakeSender:
    def __init__(self):
        self.sent = []

    def send_private(self, user_id, text):
        self.sent.append((user_id, text))
        return True

    def send_private_image(self, user_id, image, mode="path"):
        return True


def make_message(text, mid="m1"):
    return IncomingMessage(user_id=100, message_id=mid, text=text, raw={})


def build(tmp_path, replies=(), with_memory=True, **overrides):
    kwargs = dict(
        allow_user_id=100,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "d.db",
        system_prompt_file=tmp_path / "s.md",
        segment_delay_ms=0,
        reply_max_chars=100,
        journal_file=tmp_path / "journal.md",
        # 生活线会在后台偷偷调模型，把剧本抢走，测试里必须关
        life_enabled=False,
    )
    kwargs.update(overrides)
    settings = Settings(**kwargs)
    store = Store(settings.db_path)
    llm = ScriptedLLM(replies)
    memory = (
        Memory(settings, store, llm, extract_prompt="EXTRACT", compact_prompt="COMPACT")
        if with_memory
        else None
    )
    sender = FakeSender()
    engine = Engine(
        settings,
        store,
        llm,
        sender,
        "你是大肥鱼",
        sleep=lambda _: None,
        memory=memory,
    )
    return types.SimpleNamespace(
        settings=settings, store=store, llm=llm, sender=sender, engine=engine, memory=memory
    )


@pytest.fixture
def env(tmp_path):
    ctx = build(tmp_path, replies=["好呀。"])
    yield ctx
    ctx.store.close()


def _system_prompt(calls):
    return next(call for call in calls if call[0]["role"] == "system")[0]["content"]


def test_journal_is_injected_into_prompt(tmp_path):
    """记忆本（做梦写出来的叙事）要和卡片一起进 system prompt。"""
    env = build(tmp_path, replies=["好呀。"], journal_char_budget=1200)
    env.settings.journal_file.write_text("他在赶项目，常常十点才下班。\n", encoding="utf-8")

    env.engine.handle_message(make_message("在忙什么呀"))

    system = _system_prompt(env.llm.calls)
    assert "【记忆本】" in system
    assert "他在赶项目，常常十点才下班。" in system


def test_no_journal_means_no_section(env):
    env.engine.handle_message(make_message("在忙什么呀"))

    assert "【记忆本】" not in _system_prompt(env.llm.calls)
    assert env.memory.journal() == ""


def test_mood_is_injected_into_prompt(tmp_path):
    """她"此刻的状态"要进 system prompt，语气才会跟着变。"""
    from qqbot.mood import Mood

    ctx = build(tmp_path, replies=["嗯嗯。"])
    ctx.engine.mood = Mood(ctx.store, ctx.settings)

    ctx.engine.handle_message(make_message("在干嘛呀"))

    assert "# 此刻的你" in _system_prompt(ctx.llm.calls)
    ctx.store.close()


def test_his_tone_reaches_the_prompt(tmp_path):
    """他说"难过"，这条回复的语气就该先照顾他。"""
    from qqbot.mood import Mood

    ctx = build(tmp_path, replies=["嗯，我在。"])
    ctx.engine.mood = Mood(ctx.store, ctx.settings)

    ctx.engine.handle_message(make_message("今天真的好难过，什么事都不顺"))

    assert "先陪着" in _system_prompt(ctx.llm.calls)
    ctx.store.close()


def test_recall_injects_old_utterance(tmp_path):
    """窗口外的旧话要能被"想起来"——卡片记结论，原话记怎么说。"""
    ctx = build(tmp_path, replies=["好呀。"], history_max_turns=1)
    ctx.store.save_message("100", "user", "我对香菜过敏，别放香菜", created_at=1000)
    ctx.store.save_message("100", "assistant", "记住啦", created_at=1001)
    ctx.store.save_message("100", "user", "今天想吃什么", created_at=1002)
    ctx.store.save_message("100", "assistant", "都行呀", created_at=1003)

    ctx.engine.handle_message(make_message("晚上吃火锅可以放香菜吗"))

    system = _system_prompt(ctx.llm.calls)
    assert "他以前说过的原话" in system
    assert "别放香菜" in system
    ctx.store.close()


def test_recall_can_be_turned_off(tmp_path):
    ctx = build(tmp_path, replies=["好呀。"], history_max_turns=1, recall_enabled=False)
    ctx.store.save_message("100", "user", "我对香菜过敏，别放香菜", created_at=1000)
    ctx.store.save_message("100", "assistant", "记住啦", created_at=1001)

    ctx.engine.handle_message(make_message("晚上吃火锅可以放香菜吗"))

    assert "他以前说过的原话" not in _system_prompt(ctx.llm.calls)
    ctx.store.close()


def test_recall_absent_when_nothing_matches(tmp_path):
    ctx = build(tmp_path, replies=["好呀。"], history_max_turns=1)
    ctx.store.save_message("100", "user", "我对香菜过敏", created_at=1000)
    ctx.store.save_message("100", "assistant", "记住啦", created_at=1001)

    ctx.engine.handle_message(make_message("今天股票又跌了"))

    assert "他以前说过的原话" not in _system_prompt(ctx.llm.calls)
    ctx.store.close()


def card_reply(**kwargs):
    payload = {"cards": [{"op": "add", "kind": "preference", "summary": "不喜欢被叫起床"}]}
    payload["cards"][0].update(kwargs)
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------- 注入
def test_memory_block_injected_into_system(env):
    env.store.add_card("fact", "养了一只叫团子的猫", importance=0.9)
    env.engine.handle_message(make_message("今天好累"))

    system = env.llm.calls[0][0]["content"]
    assert BLOCK_TITLE in system
    assert "养了一只叫团子的猫" in system
    assert system.startswith("你是大肥鱼")


def test_identity_card_survives_decay(env):
    env.store.add_card("identity", "他是 agent 的创造者", importance=1.0)
    env.engine.handle_message(make_message("我是谁"))
    assert "他是 agent 的创造者" in env.llm.calls[0][0]["content"]


def test_low_weight_card_not_injected(env):
    env.store.add_card("event", "很久以前的闲聊", importance=0.05)
    env.engine.handle_message(make_message("今天好累"))
    assert "很久以前的闲聊" not in env.llm.calls[0][0]["content"]


def test_no_cards_no_block(env):
    env.engine.handle_message(make_message("今天好累"))
    system = env.llm.calls[0][0]["content"]
    assert BLOCK_TITLE not in system


def test_memory_disabled_skips_block(tmp_path):
    ctx = build(tmp_path, replies=["好呀。"], memory_enabled=False)
    ctx.store.add_card("fact", "养了一只猫", importance=0.9)
    ctx.engine.handle_message(make_message("今天好累"))
    assert BLOCK_TITLE not in ctx.llm.calls[0][0]["content"]
    ctx.store.close()


# ---------------------------------------------------------------- 抽取
def test_extraction_adds_card(tmp_path):
    ctx = build(tmp_path, replies=["好呀。", card_reply(importance=0.7)])
    ctx.engine.handle_message(make_message("我不喜欢周末被叫起来，让我睡到自然醒吧"))

    cards = ctx.store.list_cards()
    assert len(cards) == 1
    assert cards[0]["summary"] == "不喜欢被叫起床"
    assert cards[0]["importance"] == pytest.approx(0.7)
    assert cards[0]["source_from"] is not None
    assert cards[0]["source_to"] >= cards[0]["source_from"]
    ctx.store.close()


def test_extraction_runs_after_reply_sent(tmp_path):
    ctx = build(tmp_path, replies=["好呀。", card_reply()])
    ctx.engine.handle_message(make_message("我最近都在加班，忙到很晚才回家"))
    assert ctx.sender.sent, "回复必须先发出去"
    assert ctx.store.count_cards() == 1
    ctx.store.close()


def test_extraction_failure_does_not_break_reply(tmp_path):
    ctx = build(tmp_path, replies=["好呀。", LLMError("抽取挂了")])
    ctx.engine.handle_message(make_message("我最近都在加班，忙到很晚才回家"))
    assert ctx.sender.sent[-1][1] == "好呀。"
    assert ctx.store.count_cards() == 0
    ctx.store.close()


def test_short_message_skips_extraction(tmp_path):
    ctx = build(tmp_path, replies=["在呀。"])
    ctx.engine.handle_message(make_message("在吗"))
    assert len(ctx.llm.calls) == 1  # 只有对话那次
    ctx.store.close()


def test_command_skips_extraction(tmp_path):
    ctx = build(tmp_path, replies=[])
    ctx.engine.handle_message(make_message("/memory"))
    assert ctx.llm.calls == []
    ctx.store.close()


def test_same_fact_next_day_is_promoted(tmp_path):
    """隔天又说了一次同一件事：从"待确认"转正。"""
    ctx = build(tmp_path, replies=["好呀。", card_reply(), "好呀。", card_reply()])
    ctx.engine.handle_message(make_message("我不喜欢周末被叫起来", mid="m1"))
    card = ctx.store.list_cards()[0]
    assert card["confirmed"] is False

    ctx.memory._clock = lambda: time.time() + 86400  # 第二天又提到
    ctx.engine.handle_message(make_message("还是不喜欢被叫起来，别早上叫我", mid="m2"))

    assert ctx.store.get_card(card["id"])["confirmed"] is True
    ctx.store.close()


def test_same_fact_same_day_stays_pending(tmp_path):
    """同一天里说两遍不算证据，可能只是随口一提。"""
    ctx = build(tmp_path, replies=["好呀。", card_reply(), "好呀。", card_reply()])
    ctx.engine.handle_message(make_message("我不喜欢周末被叫起来", mid="m1"))
    ctx.engine.handle_message(make_message("还是不喜欢被叫起来，别早上叫我", mid="m2"))

    assert ctx.store.list_cards()[0]["confirmed"] is False
    ctx.store.close()


def test_extraction_dedupes_similar_card(tmp_path):
    reply = json.dumps(
        {"cards": [{"op": "add", "kind": "preference", "summary": "不喜欢被叫起床"}]},
        ensure_ascii=False,
    )
    ctx = build(tmp_path, replies=["好呀。", reply, "好呀。", reply])
    ctx.store.add_card("preference", "不喜欢被叫起床", importance=0.5)

    ctx.engine.handle_message(make_message("我不喜欢周末被叫起来，让我睡个够吧", mid="a"))
    assert ctx.store.count_cards() == 1  # 没有重复新增
    ctx.store.close()


def test_extraction_never_deletes(tmp_path):
    reply = json.dumps({"cards": [{"op": "delete", "id": 1, "summary": "x"}]})
    ctx = build(tmp_path, replies=["好呀。", reply])
    card_id = ctx.store.add_card("fact", "养了一只猫")
    ctx.engine.handle_message(make_message("我们聊聊别的事情吧，随便说点什么"))
    assert ctx.store.get_card(card_id) is not None
    ctx.store.close()


# ---------------------------------------------------------------- 压缩
def test_compact_advances_cursor(tmp_path):
    ctx = build(tmp_path, replies=["summary"], context_soft_token_budget=100)
    for i in range(12):
        ctx.store.save_message("100", "user" if i % 2 == 0 else "assistant", "很长的一段话" * 20)

    llm_reply = json.dumps({"cards": [{"kind": "chat", "summary": "聊了很多工作的事"}]}, ensure_ascii=False)
    ctx.llm.replies = [llm_reply]
    assert ctx.memory.compact("100") is True

    assert int(ctx.store.get_cursor("window_start:100", "0")) > 0
    cards = ctx.store.list_cards()
    assert len(cards) == 1
    assert cards[0]["kind"] == "chat"
    ctx.store.close()


def test_compact_failure_still_advances_cursor(tmp_path):
    ctx = build(tmp_path, replies=[], context_soft_token_budget=100)
    for i in range(12):
        ctx.store.save_message("100", "user", "很长的一段话" * 20)

    ctx.llm.replies = [LLMError("压缩挂了")]
    assert ctx.memory.compact("100") is True

    assert int(ctx.store.get_cursor("window_start:100", "0")) > 0
    assert ctx.store.count_cards() == 0  # 没生成卡片，但上下文已释放
    ctx.store.close()


def test_compact_skipped_when_under_budget(tmp_path):
    ctx = build(tmp_path, replies=[], context_soft_token_budget=8000)
    ctx.store.save_message("100", "user", "短消息")
    assert ctx.memory.compact("100") is False
    assert ctx.store.get_cursor("window_start:100", "0") == "0"
    ctx.store.close()
