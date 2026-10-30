"""她会什么、不会什么：他问"有没有这个功能"时要有事实可依。"""

from qqbot import abilities


def test_block_lists_what_she_can_do():
    text = abilities.block()

    assert "【你会的】" in text
    for item in abilities.CAN:
        assert item in text


def test_block_lists_limits_in_her_words():
    text = abilities.block()

    assert "做不到的" in text
    assert "刷抖音" in text
    assert "我没有功能" in text  # 明确说了别这么说


def test_block_stays_short():
    """这段每条消息都进 prompt，太长就是在烧钱。"""
    assert len(abilities.block()) < 400
