"""token 校准：拿接口报的真实用量，修正我们自己的估算。"""

from qqbot import usage


def settings_for(tmp_path):
    return type("S", (), {"base_dir": tmp_path})()


def test_ratio_is_neutral_without_enough_samples(tmp_path):
    s = settings_for(tmp_path)

    assert usage.ratio(s) == 1.0
    usage.record(s, 100, 999)
    assert usage.ratio(s) == 1.0  # 一个样本还不够


def test_ratio_uses_median(tmp_path):
    s = settings_for(tmp_path)
    for actual in (120, 130, 140):
        usage.record(s, 100, actual)

    assert 1.2 <= usage.ratio(s) <= 1.4


def test_ratio_is_clamped(tmp_path):
    s = settings_for(tmp_path)
    for _ in range(5):
        usage.record(s, 100, 300)  # 3 倍，在合理范围内，但要被夹到 2.0

    assert usage.ratio(s) == usage.RATIO_MAX


def test_record_ignores_missing_usage(tmp_path):
    s = settings_for(tmp_path)

    usage.record(s, 100, 0)
    usage.record(s, 0, 100)

    assert usage.samples(s) == []


def test_samples_are_persisted_and_capped(tmp_path):
    s = settings_for(tmp_path)
    for index in range(usage.SAMPLE_LIMIT + 10):
        usage.record(s, 100, 100 + index)

    assert len(usage.samples(s)) == usage.SAMPLE_LIMIT


def test_describe_says_estimate_until_samples_are_enough(tmp_path):
    s = settings_for(tmp_path)

    assert "估算" in usage.describe(s)
    for _ in range(3):
        usage.record(s, 100, 120)
    assert "样本" in usage.describe(s)
