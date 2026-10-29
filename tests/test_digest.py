"""日摘要：按天压成一篇，好答"上周发生了什么"。"""

from datetime import date, datetime, timedelta

import pytest

from qqbot import digest
from qqbot.config import Settings
from qqbot.llm import ChatResult
from qqbot.store import Store


class FakeLLM:
    def __init__(self, text="他今天加班到十点，说很累。"):
        self.text = text
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return ChatResult(self.text, 10, 20, "fake-model")


def make_settings(tmp_path, **overrides):
    kwargs = dict(
        allow_user_id=100,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "d.db",
        system_prompt_file=tmp_path / "s.md",
        digest_dir=tmp_path / "daily",
        digest_prompt_file=tmp_path / "digest.md",
        export_dir=tmp_path / "exports",
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


@pytest.fixture
def store(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(settings.db_path)
    yield store
    store.close()


def at(day: int, hour: int = 12, minute: int = 0) -> int:
    return int(datetime(2026, 10, day, hour, minute).timestamp())


# ---------------------------------------------------------------- 时间
def test_day_bounds_are_left_closed_right_open():
    start, end = digest.bounds(date(2026, 10, 29))
    assert datetime.fromtimestamp(start).hour == 0
    assert end - start == 86400


def test_day_of_uses_local_calendar():
    assert digest.day_of(at(29, 23)) == date(2026, 10, 29)


# ---------------------------------------------------------------- 落盘
def test_write_then_read(tmp_path):
    settings = make_settings(tmp_path)

    path = digest.write(settings, date(2026, 10, 29), "这一天", "他去了医院。")

    assert path.exists()
    text = digest.read(settings, date(2026, 10, 29))
    assert "他去了医院。" in text
    assert "2026-10-29" in text


def test_read_missing_day_is_none(tmp_path):
    settings = make_settings(tmp_path)
    assert digest.read(settings, date(2026, 1, 1)) is None


def test_write_is_atomic_no_tmp_left(tmp_path):
    settings = make_settings(tmp_path)
    digest.write(settings, date(2026, 10, 29), "这一天", "ok")
    assert list((tmp_path / "daily").glob("*.tmp")) == []


# ---------------------------------------------------------------- 素材
def test_format_messages_skips_commands(store):
    store.save_message("100", "user", "/status", created_at=at(29, 10))
    store.save_message("100", "user", "今天面试", created_at=at(29, 11))
    store.save_message("100", "assistant", "怎么样呀", created_at=at(29, 11, 1))

    rows = store.messages_between(*digest.bounds(date(2026, 10, 29)))
    body = digest.format_messages(rows)

    assert "/status" not in body
    assert "他：今天面试" in body
    assert "她：怎么样呀" in body


# ---------------------------------------------------------------- 生成
def test_generate_without_messages_skips_model(tmp_path, store):
    settings = make_settings(tmp_path)
    llm = FakeLLM()

    assert digest.generate(store, llm, settings, date(2026, 10, 29)) is None
    assert llm.calls == []


def test_generate_writes_page_and_calls_model_once(tmp_path, store):
    settings = make_settings(tmp_path)
    (tmp_path / "digest.md").write_text(
        "日期 {{DATE}}\n对话 {{MESSAGES}}", encoding="utf-8"
    )
    store.save_message("100", "user", "今天面试完了", created_at=at(29, 18))
    llm = FakeLLM("他面完试，说还行。")

    text = digest.generate(store, llm, settings, date(2026, 10, 29))

    assert text == "他面完试，说还行。"
    assert len(llm.calls) == 1
    prompt = llm.calls[0][-1]["content"]
    assert "2026-10-29" in prompt and "今天面试完了" in prompt
    assert digest.read(settings, date(2026, 10, 29)) is not None


def test_generate_truncates_long_output(tmp_path, store):
    settings = make_settings(tmp_path)
    store.save_message("100", "user", "聊了很多", created_at=at(29, 18))
    llm = FakeLLM("啊" * 5000)

    text = digest.generate(store, llm, settings, date(2026, 10, 29))

    assert len(text) == digest.MAX_SUMMARY_CHARS


def test_empty_model_output_is_not_saved(tmp_path, store):
    settings = make_settings(tmp_path)
    store.save_message("100", "user", "聊了很多", created_at=at(29, 18))

    assert digest.generate(store, FakeLLM(""), settings, date(2026, 10, 29)) is None
    assert digest.read(settings, date(2026, 10, 29)) is None


# ---------------------------------------------------------------- 补生成
def test_ensure_recent_only_fills_missing_days(tmp_path, store):
    settings = make_settings(tmp_path, digest_catchup_days=3)
    today = date(2026, 10, 29)
    digest.write(settings, today, "这一天", "今天已有")
    for day in (28, 27):
        store.save_message("100", "user", "说了一句", created_at=at(day, 12))
    llm = FakeLLM()

    made = digest.ensure_recent(store, llm, settings, clock=lambda: at(29, 23))

    assert made == 2
    assert len(llm.calls) == 2  # 今天已有，不重复生成
    assert digest.read(settings, date(2026, 10, 28)) is not None


# ---------------------------------------------------------------- 进 prompt
def test_recent_text_lists_days(tmp_path):
    settings = make_settings(tmp_path)
    digest.write(settings, date(2026, 10, 29), "这一天", "他去加班了。")
    digest.write(settings, date(2026, 10, 28), "这一天", "他说面试还行。")

    text = digest.recent_text(settings, today=date(2026, 10, 29))

    assert digest.RECENT_TITLE in text
    assert "10-29" in text and "他去加班了。" in text
    assert "10-28" in text


def test_recent_text_respects_budget(tmp_path):
    settings = make_settings(tmp_path, digest_in_prompt_chars=20)
    digest.write(settings, date(2026, 10, 29), "这一天", "啊" * 200)

    text = digest.recent_text(settings, today=date(2026, 10, 29))

    assert len(text) < 120


def test_recent_text_empty_when_disabled_or_missing(tmp_path):
    settings = make_settings(tmp_path, digest_enabled=False)
    assert digest.recent_text(settings, today=date(2026, 10, 29)) == ""

    enabled = make_settings(tmp_path, digest_enabled=True)
    assert digest.recent_text(enabled, today=date(2026, 10, 29)) == ""


def test_week_text_joins_pages(tmp_path):
    settings = make_settings(tmp_path)
    digest.write(settings, date(2026, 10, 29), "这一天", "今天的事。")

    assert "今天的事。" in digest.week_text(settings, today=date(2026, 10, 29))


def test_week_text_without_pages(tmp_path):
    settings = make_settings(tmp_path)
    assert "还没" in digest.week_text(settings, today=date(2026, 10, 29))
