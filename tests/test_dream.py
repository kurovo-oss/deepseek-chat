import time
"""做梦（M2 P3）：夜里整理记忆，产出记忆本 + 卡片操作。"""

import json
from datetime import datetime

import pytest

from qqbot.config import Settings
from qqbot.dream import Dreamer, parse_dream
from qqbot.llm import ChatResult
from qqbot.store import Store

PROMPT = "上次记忆本\n{{JOURNAL}}\n卡片\n{{CARDS}}\n对话\n{{MESSAGES}}\n"


class FakeLLM:
    def __init__(self, payload=None, error=None):
        self.payload = payload if payload is not None else {"journal": "", "ops": []}
        self.error = error
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return ChatResult(json.dumps(self.payload, ensure_ascii=False), 100, 50, "fake")


def at(hour, minute=0, day=29):
    return int(datetime(2026, 10, day, hour, minute).timestamp())


@pytest.fixture
def env(tmp_path):
    prompt_file = tmp_path / "dream.md"
    prompt_file.write_text(PROMPT, encoding="utf-8")
    settings = Settings(
        allow_user_id=100,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "d.db",
        system_prompt_file=tmp_path / "s.md",
        dream_prompt_file=prompt_file,
        journal_file=tmp_path / "journal.md",
        dream_at="23:30",
    )
    store = Store(settings.db_path)
    store.save_message("100", "user", "今天加班到十点", created_at=at(20))
    llm = FakeLLM()
    dreamer = Dreamer(store, llm, settings, clock=lambda: at(23, 30))
    yield type(
        "Env", (), {"store": store, "llm": llm, "settings": settings, "dreamer": dreamer}
    )
    store.close()


# ---------------------------------------------------------------- 解析
def test_parse_dream_plain_json():
    data = parse_dream('{"journal": "x", "ops": []}')
    assert data == {"journal": "x", "ops": []}


def test_parse_dream_strips_code_fence():
    raw = '好的：\n```json\n{"journal": "x", "ops": []}\n```'
    assert parse_dream(raw) == {"journal": "x", "ops": []}


@pytest.mark.parametrize("raw", ["", "不是 JSON", "[1,2]", "null"])
def test_parse_dream_rejects_garbage(raw):
    assert parse_dream(raw) is None


# ---------------------------------------------------------------- 触发时机
def test_due_when_never_dreamt(env):
    assert env.dreamer.due(at(9)) is True


def test_not_due_right_after_dreaming(env):
    env.dreamer.dream(at(23, 30))
    assert env.dreamer.due(at(23, 40)) is False
    assert env.dreamer.due(at(9, day=30) + 3600 * 20) is True  # 20 小时后该做了


def test_due_when_target_time_passed(env):
    env.dreamer.dream(at(23, 30, day=28))  # 前天做过
    assert env.dreamer.due(at(9)) is False  # 今天 23:30 还没到，且间隔不够
    assert env.dreamer.due(at(23, 31)) is True  # 今天到点了


def test_catch_up_after_long_downtime(env):
    """昨晚关机，早上开机要补一次。"""
    env.dreamer.dream(at(23, 30, day=27))
    assert env.dreamer.due(at(9)) is True


def test_disabled_dreamer_never_runs(tmp_path):
    settings = Settings(
        allow_user_id=100,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "d.db",
        system_prompt_file=tmp_path / "s.md",
        dream_enabled=False,
    )
    store = Store(settings.db_path)
    dreamer = Dreamer(store, FakeLLM(), settings)
    assert dreamer.due(at(23, 59)) is False
    store.close()


# ---------------------------------------------------------------- 做梦本身
def test_dream_without_messages_skips_llm(env):
    env.dreamer.dream(at(23, 30))  # 第一次把现有消息处理掉
    calls = len(env.llm.calls)

    result = env.dreamer.dream(at(23, 40))  # 这之间没有新消息

    assert result["ok"] is True
    assert result["messages"] == 0
    assert len(env.llm.calls) == calls  # 没有新消息就不调模型
    assert env.store.last_dream_at() is not None


def test_dream_writes_journal_and_log(env):
    env.llm.payload = {
        "journal": "他在赶项目，常常十点才下班。",
        "ops": [{"op": "add", "kind": "fact", "summary": "常加班到十点"}],
    }

    result = env.dreamer.dream(at(23, 30))

    assert result["ok"] is True
    assert result["added"] == 1
    assert env.settings.journal_file.read_text(encoding="utf-8").strip() == (
        "他在赶项目，常常十点才下班。"
    )
    dreams = env.store.list_dreams()
    assert dreams[0]["ok"] == 1
    assert dreams[0]["cards_added"] == 1
    assert dreams[0]["journal_chars"] == len("他在赶项目，常常十点才下班。")


def test_dream_prompt_contains_journal_cards_messages(env):
    env.llm.payload = {"journal": "新的", "ops": []}
    env.dreamer.dream(at(23, 30))
    prompt = env.llm.calls[-1][0]["content"]

    assert "今天加班到十点" in prompt  # 这一天的对话
    assert "（还没有卡片）" in prompt


def test_dream_prompt_skips_commands(env):
    """/status 这种指令不是聊天，写进记忆本会很怪。"""
    env.store.save_message("100", "user", "/status", created_at=at(21))
    env.llm.payload = {"journal": "新的", "ops": []}

    env.dreamer.dream(at(23, 30))

    prompt = env.llm.calls[-1][0]["content"]
    assert "/status" not in prompt
    assert "今天加班到十点" in prompt


def test_dream_cards_start_unconfirmed(env):
    """做梦推出来的结论，不能和"他说过的事实"一个可信度。"""
    env.llm.payload = {
        "journal": "x",
        "ops": [{"op": "add", "kind": "preference", "summary": "好像喜欢爬山"}],
    }

    env.dreamer.dream(at(23, 30))

    assert env.store.list_cards()[0]["confirmed"] is False


def test_dream_merge_archives_source(env):
    keep = env.store.add_card("fact", "养了一只叫团子的猫", detail="橘猫")
    drop = env.store.add_card("fact", "养了一只猫", detail="掉毛")
    env.store.touch_cards([drop], at(20))
    env.llm.payload = {
        "journal": "养了只橘猫。",
        "ops": [{"op": "merge", "from": drop, "into": keep}],
    }

    result = env.dreamer.dream(at(23, 30))

    assert result["merged"] == 1
    merged = env.store.get_card(keep)
    assert "掉毛" in merged["detail"]
    assert merged["hit_count"] == 1  # 源卡的命中次数累加过来
    assert env.store.get_card(drop)["status"] == "archived"  # 归档不是删除
    assert [card["id"] for card in env.store.list_cards(status="active")] == [keep]


def test_dream_reweight_clamps(env):
    card_id = env.store.add_card("fact", "很在意隐私")
    env.llm.payload = {
        "journal": "x",
        "ops": [{"op": "reweight", "id": card_id, "importance": 9}],
    }

    env.dreamer.dream(at(23, 30))

    assert env.store.get_card(card_id)["importance"] == 1.0


def test_dream_ignores_delete_and_unknown_ids(env):
    card_id = env.store.add_card("fact", "很在意隐私")
    env.llm.payload = {
        "journal": "x",
        "ops": [
            {"op": "delete", "id": card_id},
            {"op": "merge", "from": 999, "into": card_id},
            {"op": "reweight", "id": 999, "importance": 0.9},
            {"op": "add", "summary": ""},
        ],
    }

    result = env.dreamer.dream(at(23, 30))

    assert result["added"] == result["merged"] == result["reweight"] == 0
    assert env.store.count_cards() == 1  # 什么都没删


def test_dream_promotes_fact_from_another_day(env):
    """同一条事实隔天又被总结出来：转正。"""
    card_id = env.store.add_card(
        "fact", "常加班到十点", importance=0.5, confirmed=False, created_at=at(20, day=28)
    )
    env.llm.payload = {"journal": "x", "ops": [{"op": "add", "summary": "常加班到十点"}]}

    env.dreamer.dream(at(23, 30, day=29))

    assert env.store.get_card(card_id)["confirmed"] is True


def test_dream_keeps_same_day_fact_pending(env):
    card_id = env.store.add_card(
        "fact", "常加班到十点", importance=0.5, confirmed=False, created_at=at(1, day=29)
    )
    env.llm.payload = {"journal": "x", "ops": [{"op": "add", "summary": "常加班到十点"}]}

    env.dreamer.dream(at(23, 30, day=29))

    assert env.store.get_card(card_id)["confirmed"] is False


def test_dream_skips_duplicate_summary(env):
    env.store.add_card("fact", "常加班到十点")
    env.llm.payload = {
        "journal": "x",
        "ops": [{"op": "add", "kind": "fact", "summary": "常加班到十点"}],
    }

    result = env.dreamer.dream(at(23, 30))

    assert result["added"] == 0
    assert env.store.count_cards() == 1


def test_dream_failure_keeps_old_journal(env):
    env.settings.journal_file.write_text("旧记忆本\n", encoding="utf-8")
    env.llm.error = RuntimeError("boom")

    result = env.dreamer.dream(at(23, 30))

    assert result["ok"] is False
    assert env.settings.journal_file.read_text(encoding="utf-8").strip() == "旧记忆本"
    assert env.store.list_dreams()[0]["ok"] == 0


def test_dream_non_json_output_fails_cleanly(env):
    class BadLLM:
        def chat(self, messages, **kwargs):
            return ChatResult("我不是 JSON", 10, 5, "fake")

    env.dreamer.llm = BadLLM()

    result = env.dreamer.dream(at(23, 30))

    assert result["ok"] is False
    assert result["note"] == "输出不是 JSON"
    assert env.store.list_dreams()[0]["ok"] == 0


def test_dream_empty_output_is_named_for_what_it_is(env):
    """真实踩过：推理模型把额度烧在 reasoning 上，正文是空的。"""

    class ReasoningHungryLLM:
        def chat(self, messages, **kwargs):
            return ChatResult("", 1000, 6000, "fake")

    env.dreamer.llm = ReasoningHungryLLM()

    result = env.dreamer.dream(at(23, 30))

    assert result["ok"] is False
    assert "推理占满额度" in result["note"]
    assert env.store.list_dreams()[0]["note"] == "模型没返回正文（推理占满额度）"


def test_dream_asks_for_enough_tokens(env):
    """推理 token 也占额度，给 800 必然被截断。"""
    seen = {}

    class RecordingLLM:
        def chat(self, messages, **kwargs):
            seen.update(kwargs)
            return ChatResult('{"journal": "x", "ops": []}', 10, 5, "fake")

    env.dreamer.llm = RecordingLLM()
    env.dreamer.dream(at(23, 30))

    assert seen["max_tokens"] >= 4000


def test_bedtime_reflects_even_with_dream_disabled(tmp_path, monkeypatch):
    from qqbot import dream, reflect
    from qqbot.config import Settings
    from qqbot.store import Store

    store = Store(tmp_path / "bed.db")
    settings = Settings(
        allow_user_id=1,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "bed.db",
        system_prompt_file=tmp_path / "s.md",
        dream_enabled=False,
        reflect_enabled=True,
    )
    store.set_cursor(reflect.CURSOR_KEY, "0")
    called = []
    monkeypatch.setattr(dream.reflect_mod, "reflect", lambda *a, **k: called.append(1) or [])

    assert dream.bedtime(store, None, settings) is True
    assert called == [1]


def test_bedtime_skips_until_due(tmp_path, monkeypatch):
    from qqbot import dream
    from qqbot.config import Settings
    from qqbot.store import Store

    store = Store(tmp_path / "bed2.db")
    settings = Settings(
        allow_user_id=1,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "bed2.db",
        system_prompt_file=tmp_path / "s.md",
        reflect_enabled=True,
    )
    called = []
    monkeypatch.setattr(dream.reflect_mod, "reflect", lambda *a, **k: called.append(1) or [])
    store.set_cursor(dream.reflect_mod.CURSOR_KEY, str(int(time.time())))

    assert dream.bedtime(store, None, settings) is False
    assert called == []

def test_dream_processes_more_than_one_batch(env):
    """超过 400 条要分批吃完，不能把第 401 条之后永久漏掉。"""
    for index in range(450):
        env.store.save_message("100", "user", f"第 {index} 条", created_at=at(21) + index)
    env.llm.payload = {"journal": "x", "ops": []}

    result = env.dreamer.dream(at(23, 30))

    assert result["ok"] is True
    assert result["messages"] == 451
    assert len(env.llm.calls) == 2


def test_dream_cursor_continues_after_a_full_batch(env):
    """分批之后游标要停在第 400 条，下次从第 401 条继续。"""
    for index in range(420):
        env.store.save_message("100", "user", f"第 {index} 条", created_at=at(21) + index)
    env.llm.payload = {"journal": "x", "ops": []}

    env.dreamer.dream(at(23, 30))
    calls = len(env.llm.calls)
    env.store.save_message("100", "user", "新的一条", created_at=at(22))

    result = env.dreamer.dream(at(23, 40))

    assert result["messages"] == 1
    assert len(env.llm.calls) == calls + 1
