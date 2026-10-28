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
