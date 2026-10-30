"""看图 / 画图：两条链路都依赖外部服务，单元测试里必须全部假掉。"""

from pathlib import Path

from qqbot import draw as draw_mod
from qqbot import vision as vision_mod
from qqbot.formatting import extract_draw_tags

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class FakeResult:
    def __init__(self, text):
        self.text = text


class FakeLLM:
    def __init__(self, shots=None, text="一只趴在窗台上的胖猫"):
        self.shots = list(shots or [])
        self.text = text
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if self.shots:
            return FakeResult(self.shots.pop(0))
        return FakeResult(self.text)


class FakeStore:
    def __init__(self):
        self.notes = {}

    def get_image_note(self, sha):
        return self.notes.get(sha, "")

    def save_image_note(self, sha, note):
        self.notes[sha] = note


def test_describe_strips_and_truncates():
    llm = FakeLLM(text="  一只猫   在晒太阳  " * 30)
    note = vision_mod.describe(llm, PNG)
    assert note.startswith("一只猫 在晒太阳")
    assert len(note) <= vision_mod.MAX_NOTE_CHARS


def test_describe_sends_multimodal_content():
    llm = FakeLLM()
    vision_mod.describe(llm, PNG, sticker=True)

    content = llm.calls[0][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_note_for_is_cached_per_image():
    llm = FakeLLM(text="猫")
    store = FakeStore()

    first = vision_mod.note_for(store, llm, PNG)
    second = vision_mod.note_for(store, llm, PNG)

    assert first == second == "猫"
    assert len(llm.calls) == 1  # 同一张图只问模型一次


def test_sentence_labels_sticker():
    assert "表情包" in vision_mod.sentence("无语", sticker=True)
    assert "图片" in vision_mod.sentence("一只猫")


def test_extract_draw_tags_removes_and_collects():
    cleaned, prompts = extract_draw_tags("我画好啦{{draw:一只猫在打哈欠}}你看")
    assert cleaned == "我画好啦你看"
    assert prompts == ["一只猫在打哈欠"]


def test_extract_draw_tags_no_tag_keeps_text():
    cleaned, prompts = extract_draw_tags("普通一句话")
    assert cleaned == "普通一句话"
    assert prompts == []


def test_translate_falls_back_to_raw_text_on_error():
    class Boom:
        def chat(self, *args, **kwargs):
            raise RuntimeError("x")

    assert draw_mod.translate(Boom(), "一只猫") == "一只猫"


def test_generate_rejects_non_image_payload(monkeypatch):
    class Settings:
        draw_provider = "pollinations"
        draw_size = 256
        draw_timeout = 1.0

    monkeypatch.setattr(draw_mod, "_pollinations", lambda *a, **k: b"not an image")
    assert draw_mod.generate(Settings(), None, "一只猫") is None


def test_save_dedupes_same_bytes(tmp_path):
    first = draw_mod.save(tmp_path, PNG)
    second = draw_mod.save(tmp_path, PNG)
    assert first == second
    assert Path(first).exists()


def test_save_extension_has_single_dot(tmp_path):
    """踩过的坑：sniff_image 返回的 ext 自带点，再补一个就成了 a..png。"""
    first = draw_mod.save(tmp_path, PNG)
    assert ".." not in Path(first).name
    assert Path(first).suffix == ".png"


def test_save_rejects_non_image(tmp_path):
    assert draw_mod.save(tmp_path, b"nope") is None
