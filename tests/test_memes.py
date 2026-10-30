"""网络用语：听得懂，但自己不说。"""

from qqbot import memes


def test_glossary_has_meanings():
    text = memes.block()

    for word, meaning in memes.GLOSSARY:
        assert word in text
        assert meaning in text


def test_block_tells_her_not_to_use_them():
    text = memes.block()

    assert "自己说话别用" in text
    assert "别复读那个词" in text
    assert "多半是在逗你" in text  # 过时词的解释


def test_block_stays_short():
    """每条消息都进 prompt，太长就是在烧钱。"""
    assert len(memes.block()) < 500
