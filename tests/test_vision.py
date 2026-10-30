"""看图 / 画图：两条链路都依赖外部服务，单元测试里必须全部假掉。"""

import base64
import json
import types
import urllib.error
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


def test_translate_prompt_knows_her_look():
    """她画自己的时候，外观必须由代码补全，不能指望模型每次记住。"""
    assert "whale-themed" in draw_mod.CHARACTER
    assert draw_mod.CHARACTER in draw_mod.PROMPT_SYSTEM
    assert "鲸鱼娘" in draw_mod.PROMPT_SYSTEM


def test_generate_appends_english_style(monkeypatch):
    """踩过的坑：中文风格后缀会把 flux 的输出带崩。"""
    seen = {}

    def fake_pollinations(prompt, size, timeout, model="flux"):
        seen["prompt"] = prompt
        return PNG

    monkeypatch.setattr(draw_mod, "_pollinations", fake_pollinations)

    class Settings:
        draw_provider = "pollinations"
        draw_size = 1024
        draw_timeout = 1.0
        draw_model = "flux"

    assert draw_mod.generate(Settings(), None, "a cat on a windowsill")
    assert "no watermark" in seen["prompt"]
    assert "soft anime illustration" in seen["prompt"]

def test_openai_provider_falls_back_to_free(monkeypatch):
    """自己的 key 挂了还能退回免费版，不能让"画图"整个失灵。"""
    monkeypatch.setattr(draw_mod, "_openai_compatible", lambda *a, **k: None)
    monkeypatch.setattr(draw_mod, "_pollinations", lambda *a, **k: PNG)
    monkeypatch.setattr(draw_mod, "time", types.SimpleNamespace(sleep=lambda _: None))

    class Settings:
        draw_provider = "openai"
        draw_base_url = "https://example.invalid/v4"
        draw_api_key = "k"
        draw_model = "cogview-3-flash"
        draw_size = 512
        draw_timeout = 1.0

    assert draw_mod.generate(Settings(), None, "a cat") == PNG


def test_openai_retries_without_response_format(monkeypatch):
    """智谱不认 response_format，400 时要能自动去掉重试。"""
    bodies = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return json.dumps(self.payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=0):
        body = json.loads(request.data.decode())
        bodies.append(body)
        if len(bodies) == 1:
            raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, None)
        return Response({"data": [{"b64_json": base64.b64encode(PNG).decode()}]})

    monkeypatch.setattr(draw_mod.urllib.request, "urlopen", fake_urlopen)

    class Settings:
        draw_provider = "openai"
        draw_base_url = "https://example.invalid/v4"
        draw_api_key = "k"
        draw_model = "cogview-3-flash"
        draw_size = 512
        draw_timeout = 1.0

    data = draw_mod.generate(Settings(), None, "a cat")

    assert data == PNG
    assert "response_format" in bodies[0]
    assert "response_format" not in bodies[1]

def test_soften_removes_risky_words():
    """智谱审核对 chibi/girl 很敏感，软化要有实际效果。"""
    text = draw_mod.soften("A chibi little girl and a young girl in a bikini")
    lowered = text.lower()
    for word in ("chibi", "girl", "bikini"):
        assert word not in lowered


def test_content_filter_retries_with_softened_prompt(monkeypatch):
    prompts = []

    def fake_openai(prompt, size, timeout, settings):
        prompts.append(prompt)
        if len(prompts) == 1:
            raise draw_mod.ContentFiltered("contentFilter")
        return PNG

    monkeypatch.setattr(draw_mod, "_openai_compatible", fake_openai)
    monkeypatch.setattr(draw_mod, "time", types.SimpleNamespace(sleep=lambda _: None))

    class Settings:
        draw_provider = "openai"
        draw_base_url = "https://example.invalid/v4"
        draw_api_key = "k"
        draw_model = "cogview-3-flash"
        draw_size = 512
        draw_timeout = 1.0

    assert draw_mod.generate(Settings(), None, "a chibi girl on a windowsill") == PNG
    assert len(prompts) == 2
    assert "chibi" in prompts[0].lower()
    assert "chibi" not in prompts[1].lower()