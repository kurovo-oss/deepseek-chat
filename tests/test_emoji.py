"""表情：有节奏地带一个。提示词管不住，这里兜一层。"""

from qqbot import emoji


class FakeRng:
    def __init__(self, roll=0.0, choice_index=0):
        self.roll = roll
        self.choice_index = choice_index

    def random(self):
        return self.roll

    def choice(self, pool):
        return pool[self.choice_index % len(pool)]


def test_emoji_lands_on_the_last_bubble():
    parts = ["好呀", "那你早点睡嘛"]

    out = emoji.maybe_add(parts, {}, FakeRng(roll=0.0), chance=1.0)

    assert out[0] == "好呀"
    assert out[1].startswith("那你早点睡嘛")
    assert emoji.EMOJI_RE.search(out[1])


def test_sometimes_she_does_not_add_one():
    parts = ["好呀", "那你早点睡嘛"]

    out = emoji.maybe_add(parts, {}, FakeRng(roll=0.99), chance=0.25)

    assert out == parts


def test_never_two_emoji_in_one_reply():
    parts = ["好呀 🥺", "那你早点睡嘛"]

    assert emoji.maybe_add(parts, {}, FakeRng(roll=0.0), chance=1.0) == parts


def test_chance_zero_is_off():
    parts = ["好呀"]

    assert emoji.maybe_add(parts, {}, FakeRng(roll=0.0), chance=0) == parts


def test_tired_gets_the_sleepy_face():
    assert emoji.pick({"tired": 0.9}, FakeRng(choice_index=0)) == "😴"


def test_missing_him_gets_the_moon():
    assert emoji.pick({"miss": 0.8}, FakeRng(choice_index=0)) == "🌙"


def test_placeholder_bubbles_are_left_alone():
    assert emoji.maybe_add(["[表情包]"], {}, FakeRng(roll=0.0), chance=1.0) == ["[表情包]"]
