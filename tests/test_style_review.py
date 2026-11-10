# -*- coding: utf-8 -*-
from pathlib import Path
from types import SimpleNamespace

from qqbot import style_review
from qqbot.config import Settings
from qqbot.store import Store

ROOT = Path(__file__).resolve().parents[1]


class FakeLLM:
    def __init__(self, text: str):
        self.text = text
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return SimpleNamespace(text=self.text)


def _env(tmp_path: Path, text: str):
    settings = Settings(
        allow_user_id=123,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "data" / "chat.db",
        system_prompt_file=tmp_path / "prompts" / "system.md",
        style_review_prompt_file=ROOT / "prompts" / "style_review.md",
    )
    store = Store(settings.db_path)
    message_id = store.save_message(str(settings.allow_user_id), "assistant", "今天也要开心呀")
    store.add_feedback(message_id, "bad", note="回复别太长，先问怎么了")
    return settings, store, FakeLLM(text), message_id


def test_review_creates_suggestions_not_preference_cards(tmp_path):
    settings, store, llm, _mid = _env(
        tmp_path,
        '{"rules":["他累的时候先接情绪，不要马上给建议"]}',
    )

    created = style_review.review(store, llm, settings)

    assert len(created) == 1
    assert created[0]["status"] == "suggested"
    assert store.count_cards() == 0


def test_apply_suggestion_creates_confirmed_preference(tmp_path):
    settings, store, llm, _mid = _env(
        tmp_path,
        '{"rules":["他累的时候先接情绪，不要马上给建议"]}',
    )
    suggestion = style_review.review(store, llm, settings)[0]

    result = style_review.apply(store, suggestion["id"])

    card = store.get_card(result["card_id"])
    assert card["kind"] == "preference"
    assert card["summary"] == "他累的时候先接情绪，不要马上给建议"
    assert card["confirmed"] is True
    assert store.list_style_suggestions() == []


def test_dismiss_does_not_create_card(tmp_path):
    settings, store, llm, _mid = _env(tmp_path, '{"rules":["回复别太长"]}')
    suggestion = style_review.review(store, llm, settings)[0]

    style_review.dismiss(store, suggestion["id"])

    assert store.list_style_suggestions() == []
    assert store.count_cards() == 0


def test_style_ui_has_review_and_apply_controls():
    html = (ROOT / "qqbot" / "ui" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "qqbot" / "ui" / "app.js").read_text(encoding="utf-8")

    assert 'id="style-review"' in html
    assert "/api/styles/review" in js
    assert "/api/styles/apply" in js


def test_rules_are_deduped_and_capped(tmp_path):
    settings, store, llm, _mid = _env(
        tmp_path,
        '{"rules":["回复别太长","回复别太长","先问怎么了","晚上不要主动发","第四条"]}',
    )

    created = style_review.review(store, llm, settings)

    assert len(created) == 3
    assert [item["text"] for item in created][0] == "回复别太长"
