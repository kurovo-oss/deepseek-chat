"""语义记忆（facts）：只追加、带证据、能更正、能定点遗忘。"""

from __future__ import annotations

import time

from qqbot import facts
from qqbot.config import Settings
from qqbot.llm import ChatResult
from qqbot.store import Store


class FakeLLM:
    def __init__(self, text=""):
        self.text = text
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return ChatResult(self.text, 10, 10, "fake")


def settings(tmp_path, **overrides):
    base = dict(
        allow_user_id=1,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "f.db",
        system_prompt_file=tmp_path / "s.md",
        memory_facts_enabled=True,
        reflect_enabled=False,
    )
    base.update(overrides)
    return Settings(**base)


def make_store(tmp_path):
    return Store(tmp_path / "f.db")


def test_record_and_duplicate(tmp_path):
    store = make_store(tmp_path)
    first = facts.record(store, "他", "讨厌", "香菜", quote="别放香菜")
    again = facts.record(store, "他", "讨厌", "香菜", quote="别放香菜")

    assert first == again
    row = facts.get(store, first)
    assert row["confidence"] > 0.6
    assert row["status"] == "active"


def test_new_object_supersedes_for_exclusive_predicates(tmp_path):
    store = make_store(tmp_path)
    old = facts.record(store, "他", "喜欢", "篮球")
    new = facts.record(store, "他", "喜欢", "足球")

    assert facts.get(store, old)["status"] == "superseded"
    assert facts.get(store, old)["superseded_by"] == new
    assert facts.get(store, new)["status"] == "active"


def test_non_exclusive_predicates_keep_both(tmp_path):
    store = make_store(tmp_path)
    facts.record(store, "他", "养的", "一只猫")
    facts.record(store, "他", "养的", "两只龟")

    assert facts.stats(store)["by_status"]["active"] == 2


def test_block_carries_quote_and_id(tmp_path):
    store = make_store(tmp_path)
    fact_id = facts.record(store, "他", "讨厌", "香菜", quote="别放香菜", importance=0.9)

    text = facts.block(store, "香菜", now=int(time.time()))

    assert f"#f{fact_id}" in text
    assert "别放香菜" in text
    assert "讨厌香菜" in text


def test_correct_supersedes_and_keeps_history(tmp_path):
    store = make_store(tmp_path)
    old = facts.record(store, "他", "讨厌", "香菜", quote="别放香菜")
    new = facts.correct(store, old, "其实不讨厌香菜了")

    assert new != old
    assert facts.get(store, old)["status"] == "superseded"
    actions = [event["action"] for event in facts.events(store, old)]
    assert "supersede" in actions


def test_forget_hides_from_recall_but_keeps_row(tmp_path):
    store = make_store(tmp_path)
    fact_id = facts.record(store, "他", "讨厌", "香菜", importance=0.9)

    assert facts.forget(store, fact_id) is True
    assert facts.get(store, fact_id)["status"] == "forgotten"
    assert facts.recall(store, "香菜") == []


def test_extract_keeps_only_evidenced_facts(tmp_path):
    store = make_store(tmp_path)
    store.save_message("100", "user", "我不吃香菜，别放", origin="chat")
    store.save_message("100", "assistant", "好，我记住了", origin="chat")
    llm = FakeLLM(
        '{"facts": ['
        '{"subject": "他", "predicate": "讨厌", "object": "香菜", "quote": "我不吃香菜"},'
        '{"subject": "他", "predicate": "喜欢", "object": "跑步", "quote": "我每天跑五公里"}'
        "]}"
    )

    written = facts.extract(store, llm, settings(tmp_path))

    assert len(written) == 1
    assert facts.get(store, written[0])["object"] == "香菜"


def test_extract_marks_cursor_so_it_does_not_run_every_minute(tmp_path):
    store = make_store(tmp_path)
    store.save_message("100", "user", "我不吃香菜", origin="chat")
    cfg = settings(tmp_path)
    facts.extract(store, FakeLLM('{"facts": []}'), cfg)

    assert facts.due(store, cfg) is False


def test_extract_skips_when_disabled(tmp_path):
    store = make_store(tmp_path)
    store.save_message("100", "user", "我不吃香菜", origin="chat")

    assert facts.extract(store, FakeLLM('{"facts": []}'), settings(tmp_path, memory_facts_enabled=False)) == []


def test_maintain_forgets_stale_low_confidence_and_writes_profile(tmp_path):
    store = make_store(tmp_path)
    old = int(time.time()) - 200 * 86400
    stale = facts.record(store, "他", "有", "一张健身卡", confidence=0.2, source_at=old)
    facts.record(store, "他", "讨厌", "香菜", importance=0.9)

    facts.maintain(store, now=int(time.time()))

    assert facts.get(store, stale)["status"] == "forgotten"
    assert "香菜" in facts.profile(store, "他")


def test_recall_is_relevance_first(tmp_path):
    store = make_store(tmp_path)
    facts.record(store, "他", "喜欢", "篮球", importance=0.5)
    facts.record(store, "他", "讨厌", "香菜", importance=0.5)

    rows = facts.recall(store, "香菜")

    assert rows[0]["object"] == "香菜"
