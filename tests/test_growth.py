"""事件流：只追加、会淡忘、说两次才算认知。"""

from qqbot import growth
from qqbot.store import Store


def make_store(tmp_path):
    return Store(tmp_path / "g.db")


def test_record_appends(tmp_path):
    store = make_store(tmp_path)

    first = growth.record(store, "said", "他说想我")
    second = growth.record(store, "said", "他又说了一次")

    assert first and second and first != second
    assert [row["text"] for row in growth.recent(store, limit=5)] == [
        "他又说了一次",
        "他说想我",
    ]


def test_empty_text_is_ignored(tmp_path):
    store = make_store(tmp_path)
    assert growth.record(store, "said", "   ") == 0
    assert growth.recent(store) == []


def test_self_view_keeps_newest_and_dedupes(tmp_path):
    store = make_store(tmp_path)
    growth.record(store, "self", "原来他加班的时候不想被打扰", salience=0.7)
    growth.record(store, "self", "原来他加班时不想被打扰", salience=0.7)  # 同一句
    growth.record(store, "self", "我好像有点太粘人了", salience=0.7)

    view = growth.self_view(store)

    # 从旧到新排；重复的那句留下最新说法
    assert view == ["原来他加班时不想被打扰", "我好像有点太粘人了"]


def test_low_salience_thoughts_stay_out_of_view(tmp_path):
    store = make_store(tmp_path)
    growth.record(store, "self", "随口一想", salience=0.2)

    assert growth.self_view(store) == []


def test_beliefs_need_repetition(tmp_path):
    store = make_store(tmp_path)
    growth.record(store, "self", "他其实很在意我", salience=0.7)

    assert growth.beliefs(store) == []

    growth.record(store, "self", "他其实很在意我", salience=0.7)
    assert growth.beliefs(store) == ["他其实很在意我"]


def test_persona_block_is_bounded(tmp_path):
    store = make_store(tmp_path)
    for index in range(12):
        growth.record(store, "self", f"第{index}条想法" + "很" * 30, salience=0.8)

    block = growth.persona_block(store, budget=120)

    assert len(block) <= 120
    assert "【你最近想通的】" in block


def test_maintain_forgets_old_but_keeps_beliefs(tmp_path):
    store = make_store(tmp_path)
    old = 1_700_000_000
    growth.record(store, "said", "去年的闲聊", salience=0.3, now=old)
    growth.record(store, "self", "我想通的一件事", salience=0.9, now=old)

    removed = growth.maintain(store, now=old + 200 * 86400)

    assert removed == 1
    assert [row["text"] for row in growth.recent(store)] == ["我想通的一件事"]


def test_refusal_lifecycle(tmp_path):
    store = make_store(tmp_path)
    rid = growth.open_refusal(store, "帮我写个总结")

    assert growth.last_open_refusal(store)["ask"] == "帮我写个总结"

    growth.close_refusal(store, rid, "accepted", "他没生气")

    assert growth.last_open_refusal(store) is None
    assert growth.recent_refusal_outcomes(store) == ["accepted"]
    assert growth.recent_refusals(store)[0]["note"] == "他没生气"
