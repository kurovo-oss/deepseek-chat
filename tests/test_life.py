"""生活线：她自己的日子。没这条线，她每句话都挂在他身上。"""

import time
from pathlib import Path
from types import SimpleNamespace

from qqbot import life
from qqbot.llm import ChatResult


class FakeLLM:
    def __init__(self, text="洗了窗帘\n削笔刀找不到了\n泡了第二杯茶\n楼下新开了面包店"):
        self.text = text
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return ChatResult(self.text, 10, 20, "fake-model")


def make_settings(tmp_path, **overrides):
    base = dict(
        life_enabled=True,
        life_dir=tmp_path / "life",
        life_keep_days=3,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_today_generates_and_caches(tmp_path):
    settings = make_settings(tmp_path)
    llm = FakeLLM()

    text = life.today(settings, llm)

    assert "洗了窗帘" in text
    assert len(llm.calls) == 1
    # 同一天不重复花钱
    assert life.today(settings, llm) == text
    assert len(llm.calls) == 1


def test_today_strips_numbering_and_bullets(tmp_path):
    settings = make_settings(tmp_path)
    llm = FakeLLM("1. 洗了窗帘\n2) 笔找不到\n- 泡了茶\n\n4. 面包店")

    text = life.today(settings, llm)

    assert not any(line[:1].isdigit() for line in text.splitlines())
    assert "洗了窗帘" in text


def test_today_without_llm_is_empty(tmp_path):
    settings = make_settings(tmp_path)
    assert life.today(settings, None) == ""


def test_empty_result_is_not_cached(tmp_path):
    settings = make_settings(tmp_path)
    llm = FakeLLM("   ")

    assert life.today(settings, llm) == ""
    assert not list(Path(settings.life_dir).glob("*.md"))


def test_snippet_is_empty_before_generation(tmp_path):
    settings = make_settings(tmp_path)
    assert life.snippet(settings) == ""


def test_snippet_wraps_today(tmp_path):
    settings = make_settings(tmp_path)
    life.today(settings, FakeLLM())

    text = life.snippet(settings)

    assert text.startswith("【你今天】")
    assert "洗了窗帘" in text


def test_prune_keeps_recent_files(tmp_path):
    settings = make_settings(tmp_path)
    folder = Path(settings.life_dir)
    folder.mkdir(parents=True)
    for name in ("2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"):
        (folder / f"{name}.md").write_text("x", encoding="utf-8")

    removed = life.prune(settings)

    assert removed == 2
    assert sorted(p.name for p in folder.glob("*.md")) == [
        "2026-10-03.md",
        "2026-10-04.md",
        "2026-10-05.md",
    ]


def test_chat_like_reply_is_rejected(tmp_path):
    """模型有时候会像聊天一样回一句"好呀。"，这不是日子，不能落盘。"""
    settings = make_settings(tmp_path)
    llm = FakeLLM("好呀。")

    assert life.today(settings, llm) == ""
    assert not list(Path(settings.life_dir).glob("*.md"))


def test_busy_things_rotate_by_day():
    from datetime import date

    first = life.busy_things(date(2026, 11, 2))
    again = life.busy_things(date(2026, 11, 2))
    other = life.busy_things(date(2026, 11, 3))

    assert first == again
    assert first != other
    assert len(first.split("、")) == 2


def test_recent_days_feed_back(tmp_path, monkeypatch):
    from datetime import date

    settings = type("S", (), {"life_dir": tmp_path})()
    (tmp_path / "2026-11-01.md").write_text("昨天在收拾屋子", encoding="utf-8")
    (tmp_path / "2026-11-02.md").write_text("今天在学做菜", encoding="utf-8")

    text = life.recent(settings, date(2026, 11, 2), days=1)

    assert "昨天在收拾屋子" in text
