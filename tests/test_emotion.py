"""从他这句话里读出情绪（词表法，赶在回复之前生效）。"""

import pytest

from qqbot import emotion


@pytest.mark.parametrize(
    "text,tone",
    [
        ("今天真的好难过，心里堵得慌", "sad"),
        ("明天要面试，好紧张", "anxious"),
        ("烦死了，气死我了", "angry"),
        ("一个人待着有点孤单", "lonely"),
        ("今天加班到十点，好累", "tired"),
        ("拿到 offer 了，太开心了", "happy"),
    ],
)
def test_detect_basic_tones(text, tone):
    assert emotion.detect(text) == tone


def test_level_words_are_ignored():
    """"心里有点堵"和"心里堵"是一个意思——中间的"有点"不能把匹配搅黄。

    这是真机验证踩到的：她对着"心里有点堵"完全没反应。
    """
    assert emotion.detect("这两天心里有点堵，什么都不太想干") == "sad"
    assert emotion.detect("今天真的很累") == "tired"
    assert emotion.detect("有点不开心") == "sad"


def test_negated_positive_is_sad():
    """"不开心"不能因为含"开心"就被当成高兴。"""
    assert emotion.detect("今天有点不开心") == "sad"
    assert emotion.detect("这游戏真没意思") == "sad"


def test_negation_of_negative_does_not_fire():
    assert emotion.detect("我不难过，别担心") != "sad"


def test_neutral_text_returns_none():
    assert emotion.detect("在干嘛") is None
    assert emotion.detect("") is None


def test_commands_are_ignored():
    assert emotion.detect("/status") is None


def test_negative_takes_priority_over_positive():
    """"虽然拿到了但太累了"——先按疲惫处理。"""
    assert emotion.detect("项目搞定了，但真的撑不住了") == "tired"


def test_hint_and_effect_lookup():
    assert "先陪着" in emotion.hint("sad")
    assert emotion.hint(None) == ""
    assert emotion.effect("happy")[0] > 0  # 高兴让她更亲近
    assert emotion.effect(None) == (0.0, 0.0, 0.0)
