"""睡前反省：一天一次、最多两条、短的。"""

import time

from qqbot import growth, reflect
from qqbot.config import Settings
from qqbot.llm import ChatResult
from qqbot.store import Store


class FakeLLM:
    def __init__(self, text=""):
        self.text = text
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return ChatResult(self.text, 10, 10, "fake")


def settings(tmp_path, **overrides):
    base = dict(
        allow_user_id=1,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "r.db",
        system_prompt_file=tmp_path / "s.md",
        reflect_enabled=True,
    )
    base.update(overrides)
    return Settings(**base)


def test_parse_plain_json():
    assert reflect._parse('{"thoughts": ["他其实没生气", "我太急了"]}') == [
        "他其实没生气",
        "我太急了",
    ]


def test_parse_fenced_json():
    raw = '```json\n{"thoughts": ["想通了"]}\n```'
    assert reflect._parse(raw) == ["想通了"]


def test_parse_caps_count_and_length():
    long_text = "很" * 200
    raw = '{"thoughts": ["第一条想法", "%s", "第三条想法"]}' % long_text

    thoughts = reflect._parse(raw)

    assert len(thoughts) == 2
    assert all(len(item) <= reflect.MAX_CHARS for item in thoughts)


def test_parse_junk_returns_empty():
    assert reflect._parse("我想了想，没什么") == []
    assert reflect._parse("") == []


def test_due_is_daily(tmp_path):
    store = Store(tmp_path / "d.db")
    cfg = settings(tmp_path)
    now = 1_700_000_000

    assert reflect.due(store, cfg, now)
    store.set_cursor(reflect.CURSOR_KEY, str(now))
    assert not reflect.due(store, cfg, now + 3600)
    assert reflect.due(store, cfg, now + 21 * 3600)


def test_reflect_records_thoughts(tmp_path):
    store = Store(tmp_path / "r2.db")
    growth.record(store, "reflect", "我拒绝了他，他不太高兴", salience=0.7)
    store.save_message("100", "user", "今天好累啊")
    llm = FakeLLM('{"thoughts": ["他累的时候我该软一点", "拒绝也要看时候"]}')

    thoughts = reflect.reflect(store, llm, settings(tmp_path), now=1_700_000_000)

    assert thoughts == ["他累的时候我该软一点", "拒绝也要看时候"]
    saved = [row["text"] for row in growth.recent(store, kind="self")]
    assert saved == ["拒绝也要看时候", "他累的时候我该软一点"]
    assert store.get_cursor(reflect.CURSOR_KEY, "0") == "1700000000"


def test_reflect_without_anything_is_noop(tmp_path):
    store = Store(tmp_path / "r3.db")
    llm = FakeLLM('{"thoughts": ["无中生有"]}')

    assert reflect.reflect(store, llm, settings(tmp_path), now=1_700_000_000) == []
    assert llm.calls == []


def test_reflect_runs_on_his_words_alone(tmp_path):
    store = Store(tmp_path / "r5.db")
    store.save_message("100", "user", "我今天被领导骂了")
    llm = FakeLLM('{"thoughts": ["他今天不好受，我该多陪陪"]}')

    thoughts = reflect.reflect(store, llm, settings(tmp_path), now=time.time())

    assert thoughts == ["他今天不好受，我该多陪陪"]
    assert llm.calls


def test_reflect_disabled(tmp_path):
    store = Store(tmp_path / "r4.db")
    growth.record(store, "said", "他说想我")

    assert reflect.reflect(store, FakeLLM(), settings(tmp_path, reflect_enabled=False)) == []
    assert not reflect.due(store, settings(tmp_path, reflect_enabled=False))
