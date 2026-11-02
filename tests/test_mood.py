"""情绪 / 驱力状态：她有自己的"此刻"，而且会随时间自己变。"""

import types
from datetime import datetime

import pytest

from qqbot.config import Settings
from qqbot.mood import Mood, drift, is_night, mood_line, targets
from qqbot.proactive import FOLLOWUP, ProactiveScheduler
from qqbot.store import Store


def at(hour, minute=0, day=29):
    return int(datetime(2026, 10, day, hour, minute).timestamp())


def make_settings(tmp_path, **overrides):
    kwargs = dict(
        allow_user_id=100,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "d.db",
        system_prompt_file=tmp_path / "s.md",
        proactive_idle_hours=8,
        proactive_quiet_minutes=30,
        proactive_min_gap_hours=4,
        proactive_max_per_day=3,
        proactive_weekly_max=1,
        proactive_start_hour=9,
        proactive_end_hour=23,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


@pytest.fixture
def mood(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(settings.db_path)
    yield Mood(store, settings, clock=lambda: at(12))
    store.close()


# ---------------------------------------------------------------- 数学
def test_drift_walks_half_way_in_one_half_life():
    assert drift(0.0, 1.0, 3.0, 3 * 3600) == pytest.approx(0.5)
    assert drift(1.0, 0.0, 3.0, 3 * 3600) == pytest.approx(0.5)


def test_drift_no_time_no_move():
    assert drift(0.4, 1.0, 3.0, 0) == pytest.approx(0.4)


def test_drift_clamps_into_range():
    assert 0.0 <= drift(0.99, 1.0, 3.0, 60) <= 1.0
    assert drift(0.2, 0.9, 0.0, 10) == pytest.approx(0.9)  # 半衰期为 0 = 立刻到位


def test_is_night_window():
    assert is_night(at(23)) is True
    assert is_night(at(2)) is True
    assert is_night(at(12)) is False


# ---------------------------------------------------------------- 目标
def test_miss_target_grows_with_idle_time():
    now = at(12)
    assert targets(now, now)["miss"] == pytest.approx(0.15)  # 刚聊过
    assert targets(now, now - 6 * 3600)["miss"] == pytest.approx(0.575)
    assert targets(now, now - 12 * 3600)["miss"] == pytest.approx(1.0)


def test_tired_target_is_night_biased():
    assert targets(at(23), None)["tired"] == pytest.approx(0.8)
    assert targets(at(12), None)["tired"] == pytest.approx(0.2)


def test_night_tired_reaches_the_gate():
    """值只会逼近目标，所以夜间目标必须高过阈值，否则闸门永远不触发。"""
    from qqbot.mood import TIRED_LIMIT

    assert targets(at(23), None)["tired"] > TIRED_LIMIT


# ---------------------------------------------------------------- 状态机
def test_snapshot_starts_from_targets(mood):
    values = mood.snapshot(at(12))
    assert values["miss"] == pytest.approx(0.15)
    assert values["warmth"] == pytest.approx(0.35)
    assert values["tired"] == pytest.approx(0.2)


def test_state_drifts_after_save(mood):
    mood.save({"miss": 0.9, "warmth": 0.3, "tired": 0.2}, at(12))

    later = mood.snapshot(at(12) + 3 * 3600)

    assert later["miss"] < 0.9  # 朝目标（0.15）回落


def test_note_talk_cools_miss_and_warms(mood):
    mood.save({"miss": 0.9, "warmth": 0.3, "tired": 0.2}, at(12))

    values = mood.note_talk(at(12) + 60)

    assert values["miss"] == pytest.approx(0.15)
    # 涨得越接近顶越慢：0.3 往上一次只涨 0.08*(1-0.3)
    assert values["warmth"] == pytest.approx(0.356, abs=0.01)


def test_snapshot_is_stable_without_events(mood):
    mood.note_talk(at(12))
    first = mood.snapshot(at(12) + 60)
    second = mood.snapshot(at(12) + 60)
    assert first == pytest.approx(second)


# ---------------------------------------------------------------- 共情（读他的情绪）
def test_note_message_applies_tone_effect(mood):
    before = mood.snapshot(at(12))

    values = mood.note_message("今天加班到十点，好累", at(12))

    assert mood.current_tone(at(12)) == "tired"
    assert values["tired"] > before["tired"]


def test_happy_message_wakes_her_up(mood):
    mood.save({"miss": 0.3, "warmth": 0.3, "tired": 0.5}, at(12))

    values = mood.note_message("拿到 offer 了，太开心了", at(12))

    assert values["tired"] < 0.5  # 一起高兴，疲惫被冲淡
    assert values["warmth"] > 0.38


def test_tone_hint_enters_the_prompt(mood):
    mood.note_message("今天真的好难过", at(12))

    text = mood.describe(at(12))

    assert "先陪着" in text


def test_tone_expires_after_half_an_hour(mood):
    mood.note_message("今天真的好难过", at(12))

    assert mood.current_tone(at(12) + 1801) is None
    assert "先陪着" not in mood.describe(at(12) + 1801)


def test_commands_do_not_set_a_tone(mood):
    mood.note_message("/status", at(12))
    assert mood.current_tone(at(12)) is None


def test_line_mentions_his_tone(mood):
    mood.note_message("一个人待着有点孤单", at(12))
    assert "孤单" in mood.line(at(12))


# ---------------------------------------------------------------- 人话
def test_mood_line_describes_extremes():
    line = mood_line({"miss": 0.9, "warmth": 0.8, "tired": 0.9})
    assert "很想他" in line and "暖暖的" in line and "有点累" in line


def test_mood_line_when_calm():
    line = mood_line({"miss": 0.2, "warmth": 0.4, "tired": 0.1})
    assert "刚聊过" in line and "精神还不错" in line


def test_describe_speaks_human_not_numbers(mood):
    mood.save({"miss": 0.9, "warmth": 0.4, "tired": 0.9}, at(12))

    text = mood.describe(at(12))

    assert "# 此刻的你" in text
    assert "很想他" in text
    assert "0." not in text  # 数字她读不懂，0.42 和 0.48 对她没区别
    assert "更短" in text  # 累了要说清"短到什么程度"


def test_describe_empty_when_disabled(tmp_path):
    settings = make_settings(tmp_path, mood_enabled=False)
    store = Store(settings.db_path)
    assert Mood(store, settings).describe(at(12)) == ""
    store.close()


def test_line_for_command(mood):
    assert "我现在：" in mood.line(at(12))


# ---------------------------------------------------------------- 接主动消息
def _scheduler(store, settings, mood_values):
    mood = Mood(store, settings, clock=lambda: at(12))
    if mood_values:
        mood.save(mood_values, at(12) - 60)
    engine = types.SimpleNamespace(mood=mood)
    return ProactiveScheduler(engine, store, settings, clock=lambda: at(12))


@pytest.fixture
def store(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(settings.db_path)
    yield store
    store.close()


def test_tired_blocks_proactive(store, tmp_path):
    settings = make_settings(tmp_path)
    store.save_message("100", "user", "在吗", created_at=at(12) - 9 * 3600)
    scheduler = _scheduler(store, settings, {"miss": 1.0, "warmth": 0.4, "tired": 0.95})

    assert scheduler.gate(at(12)) is None


def test_high_miss_lowers_the_idle_bar(store, tmp_path):
    settings = make_settings(tmp_path)
    store.save_message("100", "user", "在吗", created_at=at(12) - 5 * 3600)

    calm = _scheduler(store, settings, {"miss": 0.2, "warmth": 0.4, "tired": 0.2})
    assert calm.gate(at(12)) is None  # 5 小时 < 8 小时，不该开口

    lonely = _scheduler(store, settings, {"miss": 1.0, "warmth": 0.4, "tired": 0.2})
    assert lonely.gate(at(12)) == FOLLOWUP  # 很想他：门槛降到 4.8 小时


def test_no_mood_still_works(store, tmp_path):
    """没装情绪模块时，闸门行为不变。"""
    settings = make_settings(tmp_path)
    store.save_message("100", "user", "在吗", created_at=at(12) - 9 * 3600)
    engine = types.SimpleNamespace()
    scheduler = ProactiveScheduler(engine, store, settings, clock=lambda: at(12))

    assert scheduler.gate(at(12)) == FOLLOWUP


def test_style_shrinks_when_tired():
    from qqbot import mood as mood_mod

    tired = mood_mod.style({"miss": 0.3, "warmth": 0.3, "tired": 0.9})
    calm = mood_mod.style({"miss": 0.2, "warmth": 0.4, "tired": 0.1})

    assert tired["max_bubbles"] == 2
    assert tired["ask_ok"] is False
    assert calm["max_bubbles"] == 3
    assert calm["ask_ok"] is True


def test_style_samples_follow_mood():
    from qqbot import mood as mood_mod

    assert mood_mod.style_samples({"miss": 0.2, "warmth": 0.3, "tired": 0.9})[0][0] == "在吗"
    assert mood_mod.style_samples({"miss": 0.9, "warmth": 0.3, "tired": 0.2})[0][0] == "今天忙不忙"
    assert mood_mod.style_samples({"miss": 0.2, "warmth": 0.8, "tired": 0.2})[0][0] == "今天运气不错"
    assert mood_mod.style_samples({"miss": 0.2, "warmth": 0.4, "tired": 0.2}) == ()


def test_tired_hint_stops_the_caretaking(mood):
    """累了不是"更温柔地照顾他"，是她自己也没力气应付。"""
    mood.save({"miss": 0.2, "warmth": 0.3, "tired": 0.95}, at(12))

    text = mood.describe(at(12))

    assert "不想照顾谁" in text


def test_warmth_does_not_saturate(mood):
    """亲近值不能聊一天就焊死在 1.00，不然"心情好"成了永久状态。"""
    for index in range(60):
        mood.note_talk(at(12) + index * 60)

    assert mood.snapshot(at(12) + 60 * 60)["warmth"] <= 0.92
    assert mood.snapshot(at(12) + 60 * 60)["warmth"] > 0.5


def test_stored_warmth_is_capped_on_read(mood):
    mood.save({"miss": 0.2, "warmth": 1.0, "tired": 0.2}, at(12))

    assert mood.snapshot(at(12))["warmth"] <= 0.92
