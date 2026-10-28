import pytest

from qqbot.store import Store, sanitize_fts_query


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "test.db")
    yield db
    db.close()


def test_message_id_dedupe(store):
    first = store.save_message("1", "user", "你好", qq_msg_id="m1")
    second = store.save_message("1", "user", "你好", qq_msg_id="m1")
    assert first is not None
    assert second is None
    assert store.has_qq_msg_id("m1")
    assert store.count_messages("1") == 1


def test_recent_messages_ordered_oldest_first(store):
    for i in range(3):
        store.save_message("1", "user", f"第{i}条")
    recent = store.recent_messages("1", 10)
    assert [m["content"] for m in recent] == ["第0条", "第1条", "第2条"]


def test_window_after_cursor(store):
    first = store.save_message("1", "user", "旧的")
    store.save_message("1", "user", "新的")
    after = store.recent_messages_after("1", first, 10)
    assert [m["content"] for m in after] == ["新的"]


def test_max_message_id_empty_session(store):
    assert store.max_message_id("nobody") == 0


def test_send_state_update(store):
    msg_id = store.save_message("1", "assistant", "hi", send_state="pending")
    store.update_send_state(msg_id, "failed")
    assert store.recent_messages("1", 1)[0]["send_state"] == "failed"


def test_memories_crud(store):
    mid = store.add_memory("fact", "养了一只叫团子的猫")
    store.add_memory("preference", "不喜欢长篇大论")
    assert store.count_memories() == 2
    assert store.list_memories()[0]["id"] == store.list_memories()[0]["id"]
    assert store.delete_memory(mid) is True
    assert store.delete_memory(mid) is False
    assert store.count_memories() == 1


def test_delete_memories_matching(store):
    store.add_memory("fact", "养了一只叫团子的猫")
    store.add_memory("fact", "在做 QQ 机器人")
    assert store.delete_memories_matching("猫") == 1
    assert store.count_memories() == 1


def test_usage_totals(store):
    store.log_usage("m", 10, 20, "chat")
    store.log_usage("m", 5, 5, "chat", ok=False)
    assert store.usage_total() == (15, 25)


def test_cursor_roundtrip(store):
    assert store.get_cursor("k", "0") == "0"
    store.set_cursor("k", "42")
    assert store.get_cursor("k") == "42"


def test_search_finds_chinese_substring(store):
    store.save_message("1", "user", "今天楼下有只小橘猫", qq_msg_id="a")
    store.save_message("1", "user", "晚饭吃了面条", qq_msg_id="b")
    hits = store.search_messages("小橘猫")
    assert any("小橘猫" in hit["content"] for hit in hits)


def test_search_survives_fts_special_chars(store):
    store.save_message("1", "user", "测试内容", qq_msg_id="a")
    # 含 FTS5 特殊字符的查询不得抛异常
    assert store.search_messages('a - b * c " ( ) : ^') == []
    assert store.search_messages("") == []


def test_sanitize_fts_query():
    assert sanitize_fts_query("测试 - 内容") == '"测试" OR "内容"'
    assert sanitize_fts_query("a-") == ""
    assert sanitize_fts_query("") == ""


def test_image_tags_persisted(store):
    import json

    store.save_message("1", "assistant", "喏", image_tags=["cat.png"])
    assert store.has_recent_image("1") is True
    assert json.loads(store.recent_messages("1", 1)[0]["image_tags"]) == ["cat.png"]


def test_has_recent_image_respects_window(store):
    for i in range(3):
        store.save_message("1", "user", f"m{i}")
    store.save_message("1", "assistant", "图", image_tags=["cat.png"])
    for i in range(2):
        store.save_message("1", "user", f"x{i}")

    assert store.has_recent_image("1", last_n=5) is True
    assert store.has_recent_image("1", last_n=2) is False


def test_has_recent_image_zero_window(store):
    store.save_message("1", "assistant", "图", image_tags=["cat.png"])
    assert store.has_recent_image("1", last_n=0) is False


def test_empty_image_tags_is_false(store):
    store.save_message("1", "assistant", "纯文本")
    assert store.has_recent_image("1") is False


# ---------------------------------------------------------------- 卡片
def test_add_and_get_card(store):
    card_id = store.add_card(
        "preference",
        "不喜欢被叫起床",
        detail="周末想睡到自然醒",
        entities=["作息"],
        importance=0.7,
    )
    card = store.get_card(card_id)
    assert card["kind"] == "preference"
    assert card["summary"] == "不喜欢被叫起床"
    assert card["detail"] == "周末想睡到自然醒"
    assert card["entities"] == ["作息"]
    assert card["importance"] == pytest.approx(0.7)
    assert card["pinned"] is False
    assert card["hit_count"] == 0
    assert card["status"] == "active"


def test_list_cards_filters_status(store):
    first = store.add_card("fact", "养了一只猫")
    second = store.add_card("fact", "在互联网公司上班")
    store.update_card(second, status="archived")

    assert [c["id"] for c in store.list_cards()] == [first]
    assert store.count_cards() == 1
    assert store.count_cards(status="archived") == 1


def test_update_card_ignores_unknown_fields(store):
    card_id = store.add_card("fact", "原来的")
    assert store.update_card(card_id, summary="改后的", id=999, nope="x") is True
    card = store.get_card(card_id)
    assert card["summary"] == "改后的"
    assert card["id"] == card_id


def test_update_card_serializes_entities(store):
    card_id = store.add_card("fact", "x")
    store.update_card(card_id, entities=["工作", "猫"])
    assert store.get_card(card_id)["entities"] == ["工作", "猫"]


def test_set_pinned(store):
    card_id = store.add_card("fact", "很在意隐私")
    assert store.set_pinned(card_id, True) is True
    assert store.get_card(card_id)["pinned"] is True
    store.set_pinned(card_id, False)
    assert store.get_card(card_id)["pinned"] is False


def test_delete_card(store):
    card_id = store.add_card("fact", "临时的事")
    assert store.delete_card(card_id) is True
    assert store.get_card(card_id) is None
    assert store.delete_card(card_id) is False


def test_delete_cards_matching(store):
    store.add_card("fact", "养了一只叫团子的猫")
    store.add_card("fact", "在互联网公司上班")
    assert store.delete_cards_matching("猫") == 1
    assert store.count_cards() == 1


def test_touch_cards_counts_once_per_day(store):
    card_id = store.add_card("fact", "x")
    day = 1_700_000_000
    store.touch_cards([card_id], now=day)
    store.touch_cards([card_id], now=day + 60)  # 同一天，不再计数
    assert store.get_card(card_id)["hit_count"] == 1

    store.touch_cards([card_id], now=day + 86400)  # 第二天
    assert store.get_card(card_id)["hit_count"] == 2


def test_migrates_legacy_memories(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.db"
    first = Store(path)
    first.add_memory("preference", "不喜欢被叫起床")
    first.add_memory("fact", "养了一只猫")
    first.close()

    # 模拟旧库：只有 memories，没有 cards
    con = sqlite3.connect(path)
    con.execute("DELETE FROM cards")
    con.commit()
    con.close()

    migrated = Store(path)
    cards = migrated.list_cards()
    assert len(cards) == 2
    assert {c["kind"] for c in cards} == {"preference", "fact"}
    assert all(c["importance"] == pytest.approx(0.5) for c in cards)
    migrated.close()


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "legacy2.db"
    first = Store(path)
    first.add_memory("fact", "养了一只猫")
    first.close()

    migrated = Store(path)
    assert migrated.count_cards() == 1
    migrated.close()

    again = Store(path)
    assert again.count_cards() == 1
    again.close()
