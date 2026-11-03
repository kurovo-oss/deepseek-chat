"""只说她真做过的事：系统做不到的，嘴上不能有。"""

from types import SimpleNamespace

from qqbot import capability


def snap(**overrides):
    flags = dict(
        douyin_enabled=False,
        web_enabled=False,
        video_enabled=False,
        moments_enabled=False,
        camera_enabled=False,
    )
    flags.update(overrides)
    return SimpleNamespace(**flags)


def test_disabled_things_are_spelled_out():
    text = capability.block(snap())

    assert "刷抖音" in text
    assert "上网" in text
    assert "做不到" in text


def test_fake_claim_is_cut_but_the_rest_stays():
    out = capability.soften("我刚刷到一个超好笑的视频。你今天忙吗。", snap())

    assert "刷到" not in out
    assert "你今天忙吗" in out


def test_if_everything_was_fake_she_says_she_cannot():
    assert capability.soften("我刚刷到一个视频，超好笑。", snap()) == "我这边刷不了抖音诶"


def test_internet_claim_is_cut():
    out = capability.soften("我在网上看到有人说这个很好吃。", snap())

    assert "网上看到" not in out


def test_making_a_video_is_cut():
    assert capability.soften("我给你剪了个视频，你看看。", snap()) == "我还不会剪视频"


def test_photographing_her_drawing_is_cut():
    """11-02 真事：她说"我给你拍我的画哈"——没有相机。"""
    out = capability.soften("我给你拍我的画哈。", snap())

    assert "我给你拍" not in out


def test_honest_denial_passes_through():
    """她说"我刷不了抖音"是真话，别拦。"""
    assert capability.soften("我刷不了抖音呀。", snap()) == "我刷不了抖音呀。"


def test_after_the_feature_really_exists_she_can_talk_about_it():
    settings = snap(douyin_enabled=True)

    assert capability.soften("我刚刷到一个视频。", settings) == "我刚刷到一个视频。"
    assert "刷抖音" not in capability.block(settings)  # 提示词里不再说做不到
    assert any("刷抖音" in label for label in capability.can(settings))


def test_block_shrinks_as_capabilities_arrive():
    before = capability.block(snap())
    after = capability.block(snap(douyin_enabled=True, web_enabled=True))

    assert len(after) < len(before)
