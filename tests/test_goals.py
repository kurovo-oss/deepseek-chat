# -*- coding: utf-8 -*-
from pathlib import Path
from types import SimpleNamespace

from qqbot import goals
from qqbot.config import Settings
from qqbot.store import Store
from qqbot.webui import WebUI

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
        goals_enabled=True,
        goals_prompt_file=ROOT / "prompts" / "goals.md",
    )
    store = Store(settings.db_path)
    card_id = store.add_card("preference", "他最近在准备面试", importance=0.9)
    llm = FakeLLM(text)
    return settings, store, llm, card_id


def _reply(card_id: int, title: str = "问问他面试结果"):
    return (
        '{"goals":[{"kind":"shared","title":"%s",'
        '"why":"他提过这周有面试，之后没再说",'
        '"horizon":"week","priority":0.7,"evidence":[%d]}]}'
        % (title, card_id)
    )


def test_review_creates_candidate_goal_from_real_evidence(tmp_path):
    settings, store, llm, card_id = _env(tmp_path, _reply(12))
    # 用真实卡号，避免测试依赖固定自增值
    reply = _reply(card_id)
    llm.text = reply

    created = goals.review(store, llm, settings)

    assert len(created) == 1
    goal = created[0]
    assert goal["status"] == "candidate"
    assert goal["title"] == "问问他面试结果"
    assert goal["evidence"] == [card_id]
    assert store.list_goal_actions() == []
    assert store.get_cursor(goals.CURSOR_KEY)


def test_review_rejects_goal_without_valid_evidence(tmp_path):
    settings, store, llm, card_id = _env(tmp_path, _reply(999))
    llm.text = _reply(999)

    assert goals.review(store, llm, settings) == []


def test_review_dedupes_existing_title(tmp_path):
    settings, store, llm, card_id = _env(tmp_path, "{}")
    llm.text = _reply(card_id)

    assert len(goals.review(store, llm, settings)) == 1
    llm.text = _reply(card_id, "问问他面试结果")
    assert goals.review(store, llm, settings) == []


def test_review_limits_candidates_to_two(tmp_path):
    settings, store, llm, card_id = _env(tmp_path, "{}")
    raw = (
        '{"goals":['
        '{"kind":"shared","title":"目标一","horizon":"week","priority":0.5,"evidence":[%d]},'
        '{"kind":"shared","title":"目标二","horizon":"week","priority":0.4,"evidence":[%d]},'
        '{"kind":"shared","title":"目标三","horizon":"week","priority":0.3,"evidence":[%d]}'
        "]}"
    ) % (card_id, card_id, card_id)
    llm.text = raw

    assert len(goals.review(store, llm, settings)) == 2


def test_goal_api_lists_and_updates_candidate(tmp_path):
    settings, store, llm, card_id = _env(tmp_path, "{}")
    llm.text = _reply(card_id)
    engine = SimpleNamespace(settings=settings, store=store, llm=llm)
    ui = WebUI(engine, settings)

    reviewed = ui.api_goal_review({})
    assert reviewed["ok"] is True
    assert len(reviewed["created"]) == 1

    payload = ui.api_goals()
    assert payload["counts"]["candidate"] == 1
    goal_id = payload["items"][0]["id"]

    updated = ui.api_goal_update({"id": goal_id, "status": "active"})
    assert updated["ok"] is True
    assert updated["item"]["status"] == "active"


def test_goals_nav_and_page_exist():
    html = (ROOT / "qqbot" / "ui" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "qqbot" / "ui" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "qqbot" / "ui" / "app.css").read_text(encoding="utf-8")

    assert 'data-page="goals"' in html
    assert 'id="page-goals"' in html
    assert 'id="goal-review"' in html
    assert "/api/goals/review" in js
    assert "renderGoal" in js
    assert ".goal-card" in css
