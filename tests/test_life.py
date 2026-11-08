"""生活线：只记今天真实发生过的事，没素材就不写。"""

import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from qqbot import life
from qqbot.llm import ChatResult
from qqbot.store import Store


class FakeLLM:
    def __init__(self, text="他说今天有点累，我问他怎么了\n他说没事，就是想歇会儿"):
        self.text = text
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return ChatResult(self.text, 10, 20, "fake-model")


class SeqLLM:
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return ChatResult(self.texts.pop(0) if self.texts else "", 1, 1, "fake")


def make_settings(tmp_path, **overrides):
    base = dict(
        life_enabled=True,
        base_dir=tmp_path,
        life_dir=tmp_path / "life",
        life_keep_days=3,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _say(store, text="今天有点累", at=None):
    store.save_message("100", "user", text, created_at=int(time.time()) if at is None else at)


def test_without_material_does_not_generate(tmp_path):
    """今天他一句话没说、也没发图：不写，不编。"""
    settings = make_settings(tmp_path)
    llm = FakeLLM()

    assert life.today(settings, llm) == ""
    assert llm.calls == []
    assert not list(Path(settings.life_dir).glob("*.md"))


def test_with_real_message_writes_a_grounded_note(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(tmp_path / "chat.db")
    _say(store, "今天好累，想早点睡")
    llm = FakeLLM()

    text = life.today(settings, llm, store=store)

    assert "今天有点累" in text or "累" in text
    prompt = llm.calls[0][0][1]["content"]
    assert "今天真实发生" in prompt
    assert "今天好累" in prompt
    assert Path(settings.life_dir, f"{date.today().isoformat()}.md").exists()
    store.close()


def test_today_caches_the_note(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(tmp_path / "chat.db")
    _say(store)
    llm = FakeLLM()

    text = life.today(settings, llm, store=store)

    assert life.today(settings, llm, store=store) == text
    assert len(llm.calls) == 1
    store.close()


def test_today_without_llm_is_empty(tmp_path):
    settings = make_settings(tmp_path)
    assert life.today(settings, None) == ""


def test_empty_result_is_not_cached(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(tmp_path / "chat.db")
    _say(store)

    assert life.today(settings, FakeLLM("   "), store=store) == ""
    assert not list(Path(settings.life_dir).glob("*.md"))
    store.close()


def test_one_line_is_not_a_note(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(tmp_path / "chat.db")
    _say(store)

    assert life.today(settings, FakeLLM("他说累"), store=store) == ""
    store.close()


def test_invented_prop_is_rejected(tmp_path):
    """他没提过房间，小记里就不许出现房间。"""
    settings = make_settings(tmp_path)
    store = Store(tmp_path / "chat.db")
    _say(store, "今天好累")

    text = life.today(
        settings,
        FakeLLM("我把房间收拾了一遍\n累得不想动"),
        store=store,
    )

    assert text == ""
    assert not list(Path(settings.life_dir).glob("*.md"))
    store.close()


def test_invented_prop_gets_one_rewrite(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(tmp_path / "chat.db")
    _say(store, "今天好累")
    llm = SeqLLM([
        "我把房间收拾了一遍\n累得不想动",
        "他说今天很累\n我让他早点睡",
    ])

    text = life.today(settings, llm, store=store)

    assert "房间" not in text
    assert "早点睡" in text
    assert "上一版写了今天没发生的事" in llm.calls[1][0][1]["content"]
    store.close()


def test_prop_he_mentioned_is_allowed(tmp_path):
    """他自己说了房间，她才能写房间。"""
    settings = make_settings(tmp_path)
    store = Store(tmp_path / "chat.db")
    _say(store, "你房间收拾得怎么样了")
    llm = FakeLLM("他问我房间收拾得怎么样\n我说还乱着")

    text = life.today(settings, llm, store=store)

    assert "房间" in text
    store.close()


def test_claim_she_cannot_do_is_rejected(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(tmp_path / "chat.db")
    _say(store, "今天好累")

    text = life.today(
        settings,
        FakeLLM("我刷到一个超好笑的视频\n笑到肚子疼"),
        store=store,
    )

    assert text == ""
    store.close()


def test_prompt_tells_her_what_she_cannot_do(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(tmp_path / "chat.db")
    _say(store)
    llm = FakeLLM()

    life.today(settings, llm, store=store)

    prompt = llm.calls[0][0][1]["content"]
    assert "你现在做不到" in prompt
    assert "刷抖音" in prompt
    store.close()


def test_life_call_has_enough_tokens(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(tmp_path / "chat.db")
    _say(store)
    llm = FakeLLM()

    life.today(settings, llm, store=store)

    assert llm.calls[0][1]["max_tokens"] >= 700
    store.close()


def test_snippet_is_empty_before_generation(tmp_path):
    settings = make_settings(tmp_path)
    assert life.snippet(settings) == ""


def test_snippet_wraps_today(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(tmp_path / "chat.db")
    _say(store)
    life.today(settings, FakeLLM(), store=store)

    text = life.snippet(settings)

    assert text.startswith("【你今天】")
    assert "累" in text
    store.close()


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


def test_recent_days_feed_back(tmp_path):
    settings = type("S", (), {"life_dir": tmp_path})()
    (tmp_path / "2026-11-01.md").write_text("昨天他说累", encoding="utf-8")
    (tmp_path / "2026-11-02.md").write_text("今天他说困", encoding="utf-8")

    text = life.recent(settings, date(2026, 11, 2), days=1)

    assert "昨天他说累" in text


def test_keywords_come_from_todays_note(tmp_path):
    settings = make_settings(tmp_path)
    folder = settings.life_dir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{date.today().isoformat()}.md").write_text(
        "他说今天很累\n我让他早点睡\n", encoding="utf-8"
    )

    words = life.keywords(settings)

    assert "很累" in words
    assert "今天" not in words


def test_mentions_today_counts_her_talk(tmp_path):
    settings = make_settings(tmp_path)
    folder = settings.life_dir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{date.today().isoformat()}.md").write_text(
        "他说今天很累\n我让他早点睡\n", encoding="utf-8"
    )
    store = Store(tmp_path / "chat.db")
    store.save_message("100", "assistant", "很累就早点睡呀")
    store.save_message("100", "assistant", "今天好热")
    store.save_message("100", "user", "很累")

    assert life.mentions_today(store, "100", settings) == 1
    store.close()


def test_snippet_presses_when_she_repeats_herself(tmp_path):
    settings = make_settings(tmp_path)
    folder = settings.life_dir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{date.today().isoformat()}.md").write_text(
        "他说今天很累\n我让他早点睡\n", encoding="utf-8"
    )

    note = life.snippet(settings, repeat=2)

    assert "已经说过 2 次" in note
    assert "别提它" in note


def test_after_saying_it_too_often_the_note_is_withheld(tmp_path):
    settings = make_settings(tmp_path)
    folder = settings.life_dir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{date.today().isoformat()}.md").write_text(
        "他说今天很累\n我让他早点睡\n", encoding="utf-8"
    )

    assert life.snippet(settings, repeat=3) == ""


def test_snippet_is_quiet_the_first_couple_of_times(tmp_path):
    settings = make_settings(tmp_path)
    folder = settings.life_dir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{date.today().isoformat()}.md").write_text(
        "他说今天很累\n我让他早点睡\n", encoding="utf-8"
    )

    assert "已经说过" not in life.snippet(settings, repeat=1)
