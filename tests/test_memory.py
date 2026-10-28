import json

import pytest

from qqbot.memory import (
    BLOCK_TITLE,
    MAX_OPS,
    build_block,
    card_weight,
    find_duplicate,
    parse_ops,
    parse_timestamp,
    select_cards,
    similarity,
)

NOW = 1_700_000_000
DAY = 86400


def card(**kwargs):
    base = {
        "id": 1,
        "kind": "fact",
        "summary": "常加班到十点",
        "importance": 0.5,
        "pinned": False,
        "hit_count": 0,
        "occurred_at": NOW,
        "created_at": NOW,
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------- 权重
@pytest.mark.parametrize(
    "kw, expected",
    [
        # 3 个月前的闲聊：沉底
        (dict(importance=0.5, hit_count=0, occurred_at=NOW - 90 * DAY), 0.09),
        # 3 个月前但重要、常被提起：还在
        (dict(importance=1.0, hit_count=3, occurred_at=NOW - 90 * DAY), 0.25),
        # 昨天刚聊过的偏好
        (dict(importance=0.7, hit_count=0, occurred_at=NOW - DAY), 0.41),
        # 一年前的 identity：永不衰减
        (
            dict(kind="identity", importance=1.0, occurred_at=NOW - 365 * DAY),
            1.0,
        ),
        # 钉住的卡：永不衰减
        (dict(pinned=True, importance=0.2, occurred_at=NOW - 365 * DAY), 1.0),
    ],
)
def test_weight_matches_design_table(kw, expected):
    assert card_weight(card(**kw), NOW, 30.0) == pytest.approx(expected, abs=0.01)


def test_weight_decays_monotonically():
    young = card_weight(card(occurred_at=NOW), NOW, 30.0)
    old = card_weight(card(occurred_at=NOW - 120 * DAY), NOW, 30.0)
    assert old < young


def test_hit_count_raises_weight():
    cold = card_weight(card(hit_count=0), NOW, 30.0)
    hot = card_weight(card(hit_count=5), NOW, 30.0)
    assert hot > cold


def test_weight_tolerates_missing_fields():
    assert card_weight({"summary": "x"}, NOW, 30.0) >= 0.0


# ---------------------------------------------------------------- 选卡
def test_select_filters_by_threshold():
    cards = [card(id=1, importance=1.0), card(id=2, importance=0.1)]
    selected = select_cards(cards, NOW, threshold=0.15, char_budget=10000, half_life_days=30)
    assert [c["id"] for c in selected] == [1]


def test_select_sorts_by_weight_desc():
    cards = [
        card(id=1, importance=0.5),
        card(id=2, importance=0.9),
        card(id=3, kind="identity", importance=0.3),
    ]
    selected = select_cards(cards, NOW, 0.15, 10000, 30)
    assert [c["id"] for c in selected] == [3, 2, 1]
    assert all("_weight" in c for c in selected)


def test_select_respects_char_budget():
    cards = [card(id=i, summary="x" * 40) for i in range(1, 20)]
    selected = select_cards(cards, NOW, 0.15, char_budget=200, half_life_days=30)
    assert 0 < len(selected) < 19


def test_select_empty_input():
    assert select_cards([], NOW, 0.15, 1000, 30) == []


# ---------------------------------------------------------------- 注入块
def test_build_block_groups_by_kind():
    block = build_block(
        [
            {"kind": "identity", "summary": "他是 agent 的创造者"},
            {"kind": "preference", "summary": "不喜欢被叫起床"},
        ]
    )
    assert block.startswith(BLOCK_TITLE)
    assert "[身份] 他是 agent 的创造者" in block
    assert "[偏好] 不喜欢被叫起床" in block


def test_build_block_empty():
    assert build_block([]) == ""
    assert build_block([{"kind": "fact", "summary": "   "}]) == ""


# ---------------------------------------------------------------- 解析
def test_parse_ops_add():
    raw = json.dumps(
        {
            "cards": [
                {
                    "op": "add",
                    "kind": "preference",
                    "summary": "不喜欢被叫起床",
                    "importance": 0.7,
                    "entities": ["作息"],
                    "occurred_at": "2026-10-27",
                }
            ]
        }
    )
    ops = parse_ops(raw, [])
    assert len(ops) == 1
    assert ops[0]["op"] == "add"
    assert ops[0]["kind"] == "preference"
    assert ops[0]["importance"] == pytest.approx(0.7)
    assert ops[0]["occurred_at"] is not None


def test_parse_ops_drops_delete():
    raw = json.dumps({"cards": [{"op": "delete", "id": 3, "summary": "删掉它"}]})
    assert parse_ops(raw, [3]) == []


def test_parse_ops_update_requires_known_id():
    raw = json.dumps({"cards": [{"op": "update", "id": 99, "summary": "x"}]})
    assert parse_ops(raw, [1, 2]) == []


def test_parse_ops_update_accepts_known_id():
    raw = json.dumps({"cards": [{"op": "update", "id": 2, "summary": "改成这样"}]})
    ops = parse_ops(raw, [1, 2])
    assert len(ops) == 1
    assert ops[0]["id"] == 2


def test_parse_ops_caps_count():
    items = [{"op": "add", "kind": "fact", "summary": f"第{i}件事"} for i in range(12)]
    ops = parse_ops(json.dumps({"cards": items}), [])
    assert len(ops) == MAX_OPS


def test_parse_ops_tolerates_code_fence():
    raw = '好的，结果如下：\n```json\n{"cards":[{"op":"add","kind":"fact","summary":"养了只猫"}]}\n```\n完毕'
    ops = parse_ops(raw, [])
    assert ops and ops[0]["summary"] == "养了只猫"


def test_parse_ops_handles_garbage():
    assert parse_ops("我记不住", []) == []
    assert parse_ops("", []) == []
    assert parse_ops("{不是 json", []) == []


def test_parse_ops_clamps_importance():
    raw = json.dumps({"cards": [{"op": "add", "kind": "fact", "summary": "x", "importance": 9}]})
    assert parse_ops(raw, [])[0]["importance"] == 1.0


def test_parse_ops_rejects_unknown_kind_on_add():
    raw = json.dumps({"cards": [{"op": "add", "kind": "nonsense", "summary": "x"}]})
    assert parse_ops(raw, []) == []


def test_parse_ops_truncates_summary():
    raw = json.dumps({"cards": [{"op": "add", "kind": "fact", "summary": "字" * 200}]})
    assert len(parse_ops(raw, [])[0]["summary"]) <= 80


# ---------------------------------------------------------------- 时间与相似度
@pytest.mark.parametrize(
    "value", ["2026-10-27", "2026/10/27", "2026.10.27", "2026-10-27 12:00:00"]
)
def test_parse_timestamp_formats(value):
    assert parse_timestamp(value) is not None


def test_parse_timestamp_invalid():
    assert parse_timestamp("昨天") is None
    assert parse_timestamp("") is None
    assert parse_timestamp(None) is None


def test_similarity_detects_near_duplicates():
    assert similarity("不喜欢被叫起床", "不喜欢被人叫起床") > 0.8
    assert similarity("养了只猫", "在互联网公司上班") < 0.5


def test_find_duplicate_same_kind_only():
    cards = [{"id": 1, "kind": "fact", "summary": "不喜欢被叫起床"}]
    assert find_duplicate("preference", "不喜欢被叫起床", cards) is None
    assert find_duplicate("fact", "不喜欢被叫起床", cards)["id"] == 1
