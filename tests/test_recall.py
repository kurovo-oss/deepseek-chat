"""原话召回（FTS）+ 待确认事实（confirmed）。

两条思路来自同类实现：原文才是唯一真相（卡片只是索引）；
模型推出来的结论要先"待确认"，不能和事实混在一起。
"""

import pytest

from qqbot.config import Settings
from qqbot.memory import BLOCK_TITLE, PENDING_TITLE, Memory
from qqbot.store import Store, trigrams


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "d.db")
    yield store
    store.close()


def _memory(tmp_path, store):
    settings = Settings(
        allow_user_id=1,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "d.db",
        system_prompt_file=tmp_path / "s.md",
        journal_file=tmp_path / "journal.md",
    )
    return Memory(settings, store, None)


# ---------------------------------------------------------------- 切词
def test_trigrams_needs_three_runes():
    """trigram 分词器只认 3 个字以上，2 个字的词召不回来。"""
    assert trigrams("火锅") == []
    assert trigrams("") == []


def test_trigrams_keeps_short_run_whole():
    assert trigrams("吃火锅") == ["吃火锅"]


def test_trigrams_slides_over_long_runs():
    assert trigrams("我今天加班")[:3] == ["我今天", "今天加", "天加班"]


def test_trigrams_caps_term_count():
    text = "".join(chr(0x4E00 + i) for i in range(60))  # 60 个不同的汉字
    assert len(trigrams(text)) == 12


def test_trigrams_splits_on_punctuation():
    assert trigrams("今天加班，明天休息") == ["今天加", "天加班", "明天休", "天休息"]


# ---------------------------------------------------------------- 召回
def test_recall_finds_old_utterance(store):
    old = store.save_message("100", "user", "我对香菜过敏，别放香菜", created_at=1000)
    store.save_message("100", "assistant", "好，我记住啦", created_at=1001)

    rows = store.recall_messages("今天吃饭又被放了香菜", limit=3)

    assert [row["id"] for row in rows] == [old]


def test_recall_only_returns_user_messages(store):
    store.save_message("100", "assistant", "我对香菜过敏", created_at=1000)
    assert store.recall_messages("香菜过敏那件事") == []


def test_recall_skips_window_ids(store):
    mid = store.save_message("100", "user", "我对香菜过敏", created_at=1000)
    assert store.recall_messages("香菜过敏", exclude_ids={mid}) == []


def test_recall_handles_two_char_words(store):
    """「火锅」这种两个字的词，trigram 索引不到，靠 LIKE 兜底。"""
    mid = store.save_message("100", "user", "明天想吃火锅", created_at=1000)
    assert [row["id"] for row in store.recall_messages("火锅")] == [mid]


def test_recall_returns_nothing_for_unrelated_text(store):
    store.save_message("100", "user", "我对香菜过敏", created_at=1000)
    assert store.recall_messages("今天股票又跌了") == []


def test_recall_respects_limit(store):
    for i in range(5):
        store.save_message("100", "user", f"香菜过敏第{i}次", created_at=1000 + i)
    assert len(store.recall_messages("香菜过敏", limit=2)) == 2


# ---------------------------------------------------------------- 待确认
def test_cards_are_confirmed_by_default(store):
    card_id = store.add_card("fact", "喜欢猫")
    assert store.get_card(card_id)["confirmed"] is True


def test_set_confirmed_flips_and_reports_missing(store):
    card_id = store.add_card("fact", "好像喜欢爬山", confirmed=False)
    assert store.get_card(card_id)["confirmed"] is False

    assert store.set_confirmed(card_id) is True
    assert store.get_card(card_id)["confirmed"] is True
    assert store.set_confirmed(9999) is False


def test_block_separates_pending_from_confirmed(tmp_path, store):
    store.add_card("fact", "他养了一只橘猫", importance=0.9)
    store.add_card("fact", "他好像喜欢爬山", importance=0.9, confirmed=False)

    block = _memory(tmp_path, store).block()

    main = block.split(PENDING_TITLE)[0]
    assert BLOCK_TITLE in main
    assert "橘猫" in main
    assert "爬山" not in main  # 推测不能混进正文
    assert PENDING_TITLE in block
    assert "爬山" in block.split(PENDING_TITLE)[1]


def test_block_without_pending_has_no_pending_section(tmp_path, store):
    store.add_card("fact", "他养了一只橘猫")
    block = _memory(tmp_path, store).block()
    assert PENDING_TITLE not in block


def test_injecting_pending_does_not_raise_its_weight(tmp_path, store):
    """被注入 ≠ 变可信。"""
    card_id = store.add_card("fact", "他好像喜欢爬山", confirmed=False)
    memory = _memory(tmp_path, store)
    memory.block()
    memory.block()
    assert store.get_card(card_id)["hit_count"] == 0
