"""会拒绝，但是撒娇那种。"""

import random

from qqbot import growth, refusal
from qqbot.config import Settings
from qqbot.store import Store


class Rng:
    def __init__(self, roll=0.0, choice_index=0):
        self.roll = roll
        self.choice_index = choice_index
        self.used = 0

    def random(self):
        self.used += 1
        return self.roll

    def choice(self, options):
        return options[self.choice_index % len(options)]


def make_store(tmp_path):
    return Store(tmp_path / "r.db")


def settings(tmp_path, **overrides):
    base = dict(
        allow_user_id=1,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "s.db",
        system_prompt_file=tmp_path / "s.md",
        refusal_enabled=True,
        refusal_chance=1.0,
    )
    base.update(overrides)
    return Settings(**base)


def test_request_detection():
    assert refusal.is_request("帮我写个总结")
    assert refusal.is_request("查一下明天天气")
    assert not refusal.is_request("你吃饭了吗")
    assert not refusal.is_request("/status")


def test_he_is_down_so_she_never_refuses(tmp_path):
    store = make_store(tmp_path)
    plan = refusal.choose(
        store, settings(tmp_path), {}, "帮我写个总结，我今天好累想哭", Rng(roll=0.0)
    )
    assert plan is None


def test_plan_is_petulant_not_aggressive(tmp_path):
    store = make_store(tmp_path)
    plan = refusal.choose(store, settings(tmp_path), {}, "帮我写个总结", Rng(roll=0.0))

    assert plan is not None
    assert "撒娇" in plan["plan"]
    assert "别凶" in plan["plan"]
    assert plan["ask"] == "帮我写个总结"


def test_tired_raises_the_chance(tmp_path):
    store = make_store(tmp_path)
    base = settings(tmp_path, refusal_chance=0.2)

    calm = refusal.choose(store, base, {"tired": 0.0}, "帮我做个表", Rng(roll=0.35))
    tired = refusal.choose(store, base, {"tired": 0.9}, "帮我做个表", Rng(roll=0.35))

    assert calm is None
    assert tired is not None


def test_recent_rejection_lowers_the_chance(tmp_path):
    store = make_store(tmp_path)
    rid = growth.open_refusal(store, "帮我写个总结")
    growth.close_refusal(store, rid, "rejected", "他不太高兴")

    assert refusal.choose(store, settings(tmp_path, refusal_chance=0.28), {}, "帮我写个总结", Rng(roll=0.25)) is None
    assert refusal.choose(store, settings(tmp_path, refusal_chance=0.4), {}, "帮我写个总结", Rng(roll=0.25)) is not None


def test_disabled_refusal_never_fires(tmp_path):
    store = make_store(tmp_path)
    assert refusal.choose(store, settings(tmp_path, refusal_enabled=False), {}, "帮我写个总结", Rng()) is None


def test_settle_rejected_writes_a_reflection(tmp_path):
    store = make_store(tmp_path)
    growth.open_refusal(store, "帮我写个总结")

    result = refusal.settle(store, settings(tmp_path), "算了，我自己来")

    assert result["outcome"] == "rejected"
    notes = [row["text"] for row in growth.recent(store, kind="reflect")]
    assert notes and "不太高兴" in notes[0]
    assert growth.last_open_refusal(store) is None


def test_settle_accepted_stays_light(tmp_path):
    store = make_store(tmp_path)
    growth.open_refusal(store, "帮我写个总结")

    result = refusal.settle(store, settings(tmp_path), "哈哈好吧，那你休息")

    assert result["outcome"] == "accepted"
    row = growth.recent(store, kind="reflect")[0]
    assert row["salience"] < 0.6


def test_settle_without_open_refusal_is_noop(tmp_path):
    store = make_store(tmp_path)
    assert refusal.settle(store, settings(tmp_path), "在吗") is None


def test_open_note_surfaces_the_pain(tmp_path):
    store = make_store(tmp_path)
    growth.record(store, "reflect", "我拒绝了他（帮我写个总结），他好像不太高兴。", salience=0.7)

    note = refusal.open_note(store)

    assert "你还没想通的一件事" in note
    assert "不太高兴" in note


def test_open_note_stays_quiet_when_nothing_hurts(tmp_path):
    store = make_store(tmp_path)
    growth.record(store, "reflect", "我拒绝了他，他没生气。", salience=0.45)

    assert refusal.open_note(store) == ""
