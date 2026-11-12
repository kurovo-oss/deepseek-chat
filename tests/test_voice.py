"""语音：情绪重的时候说一句，合成失败就当没有。"""

import json
from pathlib import Path
from types import SimpleNamespace

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


def test_gpt_sovits_uses_configured_reference(tmp_path, monkeypatch):
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF")
    captured = {}

    class Response:
        headers = {"Content-Type": "audio/wav"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"RIFF" + b"\x00" * 100

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(voice.urllib.request, "urlopen", fake_urlopen)
    settings = SimpleNamespace(
        voice_enabled=True,
        voice_provider="gpt_sovits",
        voice_dir=tmp_path,
        base_dir=tmp_path,
        gpt_sovits_url="http://127.0.0.1:9880",
        gpt_sovits_ref_audio=ref,
        gpt_sovits_prompt_text="晚安呀。",
        gpt_sovits_prompt_lang="zh",
        gpt_sovits_text_lang="zh",
        gpt_sovits_timeout=9,
        gpt_sovits_top_k=12,
        gpt_sovits_temperature=0.8,
        gpt_sovits_text_split_method="cut5",
        asr_ffmpeg=tmp_path / "missing-ffmpeg.exe",
        voice_name="xiaoyi",
        voice_rate="+5%",
        voice_pitch="+15Hz",
    )

    path = voice.synthesize("晚安呀", settings)

    assert path is not None and path.suffix == ".wav"
    assert captured["url"] == "http://127.0.0.1:9880/tts"
    assert captured["body"]["ref_audio_path"] == str(ref)
    assert captured["body"]["prompt_text"] == "晚安呀。"
    assert captured["body"]["text"] == "晚安呀"
    assert captured["timeout"] == 9


def test_cosyvoice_posts_reference_and_instruct(tmp_path, monkeypatch):
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF")
    captured = {}

    class Response:
        headers = {"Content-Type": "audio/wav"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"RIFF" + b"\x00" * 100

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(voice.urllib.request, "urlopen", fake_urlopen)
    settings = SimpleNamespace(
        voice_enabled=True,
        voice_provider="cosyvoice",
        voice_dir=tmp_path,
        base_dir=tmp_path,
        cosyvoice_url="http://127.0.0.1:50000",
        cosyvoice_ref_audio=ref,
        cosyvoice_prompt_text="希望你能改变心意",
        cosyvoice_instruct="用轻柔、害羞的语气说这句话<|endofprompt|>",
        cosyvoice_timeout=45,
        asr_ffmpeg=tmp_path / "missing-ffmpeg.exe",
        voice_name="xiaoyi",
        voice_rate="+5%",
        voice_pitch="+15Hz",
    )

    path = voice.synthesize("晚安呀", settings)

    assert path is not None and path.suffix == ".wav"
    assert captured["url"] == "http://127.0.0.1:50000/tts"
    assert captured["body"]["ref_audio"] == str(ref)
    assert captured["body"]["prompt_text"] == "希望你能改变心意"
    assert "<|endofprompt|>" in captured["body"]["instruct"]
    assert captured["timeout"] == 45


def test_cosyvoice_requires_reference(tmp_path):
    settings = SimpleNamespace(
        voice_enabled=True,
        voice_provider="cosyvoice",
        voice_dir=tmp_path,
        base_dir=tmp_path,
        cosyvoice_ref_audio=tmp_path / "missing.wav",
        cosyvoice_url="http://127.0.0.1:50000",
    )

    assert voice.synthesize("晚安呀", settings) is None


def test_gpt_sovits_requires_reference(tmp_path):
    settings = SimpleNamespace(
        voice_enabled=True,
        voice_provider="gpt_sovits",
        voice_dir=tmp_path,
        base_dir=tmp_path,
        gpt_sovits_ref_audio=tmp_path / "missing.wav",
        gpt_sovits_url="http://127.0.0.1:9880",
    )

    assert voice.synthesize("晚安呀", settings) is None


def test_prune_keeps_the_newest(tmp_path):
    for index in range(5):
        (tmp_path / f"{index}.mp3").write_bytes(b"x")

    removed = voice.prune(tmp_path, keep=2)

    assert removed == 3
    assert [p.name for p in sorted(tmp_path.glob("*.mp3"))] == ["3.mp3", "4.mp3"]
