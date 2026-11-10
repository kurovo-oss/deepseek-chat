# -*- coding: utf-8 -*-
from pathlib import Path
from types import SimpleNamespace

import pytest

from qqbot.config import Settings
from qqbot.memory import Memory
from qqbot.store import Store
from qqbot.webui import WebUI


@pytest.fixture()
def env(tmp_path: Path):
    settings = Settings(
        allow_user_id=123,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "data" / "chat.db",
        system_prompt_file=tmp_path / "prompts" / "system.md",
    )
    store = Store(settings.db_path)
    engine = SimpleNamespace(settings=settings, store=store)
    ui = WebUI(engine, settings)
    yield ui, store, settings
    store.close()


def test_feedback_bad_note_creates_confirmed_preference_card(env):
    ui, store, settings = env
    message_id = store.save_message(str(settings.allow_user_id), "assistant", "今天也要开心呀")

    result = ui.api_feedback(
        {"message_id": message_id, "rating": "bad", "note": "回复别太长，先问怎么了"}
    )

    assert result["ok"] is True
    assert result["feedback_id"] > 0
    card = store.get_card(result["card_id"])
    assert card["kind"] == "preference"
    assert card["summary"] == "回复别太长，先问怎么了"
    assert card["confirmed"] is True
    assert card["source_from"] == message_id
    assert "回复别太长" in Memory(settings, store, None).block()


def test_feedback_good_only_records_signal(env):
    ui, store, settings = env
    message_id = store.save_message(str(settings.allow_user_id), "assistant", "好呀")

    result = ui.api_feedback({"message_id": message_id, "rating": "good"})

    assert result["card_id"] is None
    assert store.count_cards() == 0
    with store._lock:  # 测试里直接确认落库字段
        row = store._conn.execute("SELECT * FROM feedback WHERE id = ?", (result["feedback_id"],)).fetchone()
    assert row["rating"] == "good"
    assert row["message_id"] == message_id


def test_feedback_rejects_user_message_and_bad_rating(env):
    ui, store, settings = env
    user_id = store.save_message(str(settings.allow_user_id), "user", "在吗")
    with pytest.raises(ValueError, match="只能对她说的话"):
        ui.api_feedback({"message_id": user_id, "rating": "good"})

    bot_id = store.save_message(str(settings.allow_user_id), "assistant", "在呀")
    with pytest.raises(ValueError, match="good 或 bad"):
        ui.api_feedback({"message_id": bot_id, "rating": "meh"})


def test_manual_preference_is_confirmed_and_visible(env):
    ui, store, _settings = env
    result = ui.api_memory_preference({"summary": "先听我说完，再给建议"})

    card = store.get_card(result["card_id"])
    assert card["kind"] == "preference"
    assert card["summary"] == "先听我说完，再给建议"
    assert card["confirmed"] is True
    assert card["importance"] == pytest.approx(0.8)


def test_ui_exposes_feedback_and_preference_entry():
    root = Path(__file__).resolve().parents[1] / "qqbot" / "ui"
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "app.js").read_text(encoding="utf-8")
    css = (root / "app.css").read_text(encoding="utf-8")

    assert 'id="add-preference"' in html
    assert "/api/feedback" in js
    assert "feedbackBar" in js
    assert ".chat-feedback" in css
