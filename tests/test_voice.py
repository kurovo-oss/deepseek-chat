"""语音：情绪重的时候说一句，合成失败就当没有。"""

from pathlib import Path

from qqbot import voice


class Rng:
    def __init__(self, roll=0.0):
        self.roll = roll

    def random(self):
        return self.roll


def snap(**overrides):
    base = dict(
        voice_enabled=True,
        voice_chance=0.3,
        voice_max_chars=32,
        voice_name="xiaoyi",
        voice_dir=Path("data/voice"),
    )
    base.update(overrides)
    from types import SimpleNamespace

    return SimpleNamespace(**base)


def test_disabled_never_speaks():
    assert not voice.should_speak(["晚安呀"], {}, Rng(), snap(voice_enabled=False))


def test_long_replies_are_not_spoken():
    text = "这" * 40

    assert not voice.should_speak([text], {}, Rng(), snap())


def test_short_line_can_be_spoken():
    assert voice.should_speak(["晚安呀"], {}, Rng(roll=0.0), snap())


def test_tired_makes_voice_more_likely():
    settings = snap(voice_chance=0.3)

    # 0.45 的骰子：平时不发，累了就发
    assert not voice.should_speak(["好累呀"], {}, Rng(roll=0.45), settings)
    assert voice.should_speak(["好累呀"], {"tired": 0.8}, Rng(roll=0.45), settings)


def test_emoji_and_markers_are_not_read_aloud():
    assert voice.clean_text(["好呀 🥺", "[表情包]"]) == "好呀"


def test_nothing_to_say_no_voice():
    assert voice.synthesize("", snap()) is None
    assert voice.synthesize("好呀", snap(voice_enabled=False)) is None


def test_prune_keeps_the_newest(tmp_path):
    for index in range(5):
        (tmp_path / f"{index}.mp3").write_bytes(b"x")

    removed = voice.prune(tmp_path, keep=2)

    assert removed == 3
    assert [p.name for p in sorted(tmp_path.glob("*.mp3"))] == ["3.mp3", "4.mp3"]
