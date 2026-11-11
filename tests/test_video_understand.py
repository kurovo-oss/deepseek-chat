# -*- coding: utf-8 -*-
from pathlib import Path
from types import SimpleNamespace

from qqbot import vision, video_understand
from qqbot.store import Store


class FakePage:
    def __init__(self):
        self.calls = []
        self.shots = 0

    def call(self, method, params=None, timeout=20):
        self.calls.append((method, params))
        return {}

    def evaluate(self, expression, timeout=20):
        self.calls.append(("evaluate", expression))
        if "FRAME_INFO_JS" in expression or "duration" in expression and "currentTime" not in expression:
            return {
                "ok": True,
                "duration": 60,
                "rect": {"x": 100, "y": 80, "width": 500, "height": 300},
                "viewport": {"width": 1280, "height": 900},
            }
        return True

    def screenshot(self, clip=None, quality=72, timeout=20):
        self.shots += 1
        self.last_clip = clip
        return b"jpeg-frame-%d" % self.shots


class FakeLLM:
    def __init__(self):
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return SimpleNamespace(text="视频里在演示怎么用 DeepSeek 接入 QQ，画面有配置界面。")


class ProbeLLM(FakeLLM):
    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return SimpleNamespace(text='{"score":0.82,"tags":["编程","教程"],"summary":"屏幕在演示接入流程"}')


class FakeStore:
    def __init__(self):
        self.notes = {}
        self.recent = []

    def get_video_note(self, aweme_id, desc_hash=""):
        return self.notes.get(aweme_id, "")

    def save_video_note(self, aweme_id, summary, desc_hash="", frames=None, now=None):
        self.notes[aweme_id] = summary

    def recent_video_notes(self, since, limit=3):
        return self.recent[:limit]


def test_capture_frames_uses_video_rect_and_seeks():
    page = FakePage()

    frames = video_understand.capture_frames(page)

    assert len(frames) == 4
    assert page.shots == 4
    assert page.last_clip["width"] == 500
    assert page.last_clip["x"] == 100


def test_analyze_page_summarizes_and_caches():
    page = FakePage()
    llm = FakeLLM()
    store = FakeStore()

    first = video_understand.analyze_page(store, llm, page, "123", "标题")
    second = video_understand.analyze_page(store, llm, page, "123", "标题")

    assert first == "视频里在演示怎么用 DeepSeek 接入 QQ，画面有配置界面。"
    assert second == first
    assert page.shots == 4
    assert len(llm.calls) == 1
    assert len(llm.calls[0][0][0]["content"]) == 5  # 文本 + 4 帧


def test_probe_page_scores_one_frame():
    page = FakePage()
    llm = ProbeLLM()

    result = video_understand.probe_page(page, llm, "标题")

    assert result["score"] == 0.82
    assert result["tags"] == ["编程", "教程"]
    assert result["summary"] == "屏幕在演示接入流程"
    assert page.shots == 1
    assert len(llm.calls[0][0][0]["content"]) == 2


def test_context_block_uses_recent_summaries():
    store = FakeStore()
    store.recent = [{"summary": "视频在演示配置界面"}]

    block = video_understand.context_block(store, days=2, limit=2)

    assert "最近看过的视频" in block
    assert "配置界面" in block


def test_no_video_returns_empty():
    class NoVideo(FakePage):
        def evaluate(self, expression, timeout=20):
            return {"ok": False, "reason": "no_video"}

    assert video_understand.capture_frames(NoVideo()) == []


def test_describe_frames_requires_frames():
    assert vision.describe_frames(FakeLLM(), []) == ""
