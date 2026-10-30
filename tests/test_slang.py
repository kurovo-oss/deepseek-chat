"""口头语：每天一小批，别堆、别用过时的。"""

from datetime import date, timedelta

from qqbot import slang


def test_pick_is_stable_within_a_day():
    day = date(2026, 10, 30)
    assert slang.pick(day) == slang.pick(day)


def test_pick_changes_between_days():
    base = {word for word, _, _ in slang.pick(date(2026, 10, 30))}
    later = {word for word, _, _ in slang.pick(date(2026, 11, 20))}
    assert base != later


def test_pick_respects_count_and_bounds():
    assert len(slang.pick(date(2026, 10, 30), count=3)) == 3
    assert len(slang.pick(date(2026, 10, 30), count=999)) == len(slang.ITEMS)


def test_block_has_usage_rules():
    text = slang.block(date(2026, 10, 30))

    assert "【今天的口头语】" in text
    assert "一天最多三四次" in text
    assert "插话用的词" in text
    assert "这些别说" in text


def test_block_marks_banned_words_as_banned():
    text = slang.block(date(2026, 10, 30))
    tail = text.split("这些别说", 1)[1]

    assert "绝绝子" in tail
    # 禁词只出现在"别说"那一行，不能被当成可用的梗推出去
    body = text.split("这些别说", 1)[0]
    for word in slang.BANNED:
        assert word not in body
