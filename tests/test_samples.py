"""开场样品：教手感，但不许夹带梗和长句。"""

from qqbot import memes, samples


def test_samples_are_short_pairs():
    assert len(samples.SAMPLES) >= 5
    for said, answered in samples.SAMPLES:
        assert said.strip()
        assert answered.strip()
        # 她说的话要短：样品要是写成小作文，学出来的就是小作文
        assert len(answered) <= 40, answered


def test_samples_do_not_teach_memes():
    joined = "".join(said + answered for said, answered in samples.SAMPLES)
    for word in memes.DATED:
        assert word not in joined
    for item in memes.GLOSSARY:
        assert item[0] not in joined


def test_messages_alternate_roles():
    pairs = samples.messages(3)

    assert [item["role"] for item in pairs] == ["user", "assistant"] * 3


def test_messages_respect_limit():
    assert samples.messages(0) == []
    assert len(samples.messages(2)) == 4
    assert len(samples.messages(999)) == len(samples.SAMPLES) * 2


def test_empty_limit_is_safe():
    assert samples.messages(-1) == []


def test_pairs_teach_that_silence_is_allowed():
    """样品里得有"说完就停"的样子，不然她会学成每句都反问。"""
    frontline = samples.SAMPLES[: samples.DEFAULT_PAIRS]
    quiet = [answered for _, answered in frontline if "？" not in answered and "?" not in answered]

    assert len(quiet) >= 3


def test_pairs_include_her_own_day():
    joined = "".join(answered for _, answered in samples.SAMPLES[: samples.DEFAULT_PAIRS])

    assert "画到一半" in joined  # 她会主动说自己手上的事


def test_pairs_keep_her_close_to_him():
    """以他为中心：他一来，她手头的事就可以放下。"""
    frontline = samples.SAMPLES[: samples.DEFAULT_PAIRS]
    answers = [answered for _, answered in frontline]

    assert any("不忙" in item or "我刚好在想你" in item for item in answers)
