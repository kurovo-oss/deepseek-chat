"""她此刻在干嘛：忙的时候几条才回一条。"""

from qqbot import presence


class FakeRng:
    def __init__(self, *values):
        self.values = list(values)

    def random(self):
        return self.values.pop(0) if self.values else 0.5

    def uniform(self, low, high):
        return (low + high) / 2


def test_busy_chance_is_higher_at_night():
    assert presence.busy_chance(3) > presence.busy_chance(12)
    assert presence.busy_chance(23) < presence.busy_chance(3)


def test_night_activity_is_sleeping():
    assert presence.pick_activity(0.1, 3) == "在睡觉"


def test_pick_activity_returns_known_activity():
    names = {name for name, _ in presence.ACTIVITIES} | {"在睡觉"}

    assert presence.pick_activity(0.0, 14) in names
    assert presence.pick_activity(0.999, 14) in names


def test_current_persists_and_reuses_state(tmp_path):
    path = tmp_path / "p.json"

    first = presence.current(path, 1000.0, FakeRng(0.5, 0.9))
    again = presence.current(path, 1100.0, FakeRng(0.1, 0.1))

    assert path.exists()
    assert again == first  # 没过期就沿用，不会每条消息都变


def test_expired_state_is_regenerated(tmp_path):
    path = tmp_path / "p.json"
    first = presence.current(path, 1000.0, FakeRng(0.5, 0.9))

    later = presence.current(path, 1000.0 + 4000, FakeRng(0.9, 0.9))

    assert later["until"] > first["until"]


def test_drawing_is_not_a_daily_activity():
    """他被"天天画画"烦过一次了：这条线里彻底不要画画。"""
    names = [name for name, _ in presence.ACTIVITIES]
    weights = dict(presence.ACTIVITIES)

    assert not any("画" in name for name in names)
    assert len(weights) >= 8
    assert abs(sum(weights.values()) - 1.0) < 0.001
