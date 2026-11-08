from __future__ import annotations

import http.client
import json
import socket
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from qqbot.config import Settings
from qqbot import persona_templates, proactive_mute
from qqbot.runtime import Runtime
from qqbot.store import Store
from qqbot.webui import (
    WebUI,
    build_status,
    diagnostics_payload,
    export_diagnostics,
    save_settings,
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def base(tmp_path: Path) -> Path:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "prompts" / "system.md").write_text(
        "# 大肥鱼\n\n" + "你要记住做不到的事就说做不到，不要编造，也不要把话题带到线下。" * 8,
        encoding="utf-8",
    )
    (tmp_path / "logs" / "bot.log").write_text("line-1\nline-2\n", encoding="utf-8")
    (tmp_path / ".env").write_text("ALLOW_USER_ID=10001\nMODEL_API_KEY=sk-secret-value\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def settings(base: Path) -> Settings:
    return Settings(
        allow_user_id=10001,
        model_api_key="sk-secret-value-123456",
        base_dir=base,
        db_path=base / "data" / "chat.db",
        system_prompt_file=base / "prompts" / "system.md",
        log_dir=base / "logs",
        model_id="deepseek-flash",
        model_base_url="https://api.example/v1",
        ui_port=_free_port(),
        listen_port=_free_port(),
    )


@pytest.fixture()
def engine(settings: Settings):
    store = Store(settings.db_path)
    engine = SimpleNamespace(
        settings=settings,
        store=store,
        sender=None,
        guard=None,
        mood=None,
        system_prompt="old",
    )
    yield engine
    store.close()


def test_status_reports_real_adapter_and_last_times(settings: Settings, engine):
    now = int(time.time())
    engine.store.save_message(str(settings.allow_user_id), "user", "在吗", created_at=now - 60)
    engine.store.save_message(str(settings.allow_user_id), "assistant", "在呀", created_at=now, origin="reply")
    payload = build_status(
        engine,
        Runtime(),
        now=now,
        adapter={"online": True, "nickname": "鱼", "user_id": "2680359191", "checked": True},
    )
    assert payload["adapter"]["online"] is True
    assert payload["adapter"]["nickname"] == "鱼"
    assert payload["last"]["bot_at"] == now
    assert payload["last"]["user_at"] == now - 60


def test_memory_page_crud_and_correct(settings: Settings, engine):
    card_id = engine.store.add_card("fact", "他讨厌香菜", detail="上次吃火锅时说的", importance=0.8)
    app = WebUI(engine, settings, Runtime())
    listed = app.api_memory("香菜")
    assert listed["total"] == 1
    assert listed["items"][0]["summary"] == "他讨厌香菜"

    updated = app.api_memory_update({"id": card_id, "summary": "他其实不讨厌香菜", "detail": "记错了"})
    assert updated["item"]["summary"] == "他其实不讨厌香菜"
    assert updated["item"]["detail"] == "记错了"

    corrected = app.api_memory_correct({"id": card_id, "summary": "他对香菜无所谓"})
    assert corrected["item"]["summary"] == "他对香菜无所谓"
    assert corrected["item"]["detail"] == ""

    assert app.api_memory_delete({"id": card_id})["ok"] is True
    assert app.api_memory()["total"] == 0
    with pytest.raises(ValueError):
        app.api_memory_delete({"id": card_id})


def test_persona_templates_preview_apply_restore(settings: Settings, engine):
    app = WebUI(engine, settings, Runtime())
    items = app.api_persona_templates()["items"]
    assert {item["id"] for item in items} == {"default", "clingy", "quiet"}

    preview = app.api_persona_preview({"template_id": "clingy"})
    assert "更黏人" in preview["text"]
    assert preview["chars"] > 200

    applied = app.api_persona_apply({"template_id": "clingy"})
    assert applied["template"]["name"] == "更黏人一点"
    assert "更黏人" in Path(settings.system_prompt_file).read_text(encoding="utf-8")

    restored = app.api_persona_restore()
    assert restored["ok"] is True
    default = persona_templates.default_text(settings)
    assert Path(settings.system_prompt_file).read_text(encoding="utf-8").strip() == default.strip()


def test_proactive_quiet_hours_wrap_midnight(settings: Settings):
    def at(hour: int, minute: int = 0) -> int:
        return int(time.mktime((2026, 11, 8, hour, minute, 0, 0, 0, -1)))

    settings = replace(
        settings,
        proactive_quiet_start_hour=23,
        proactive_quiet_end_hour=8,
    )
    assert settings.in_quiet_hours(at(23, 30)) is True
    assert settings.in_quiet_hours(at(7, 59)) is True
    assert settings.in_quiet_hours(at(8, 0)) is False
    assert settings.in_quiet_hours(at(12, 0)) is False


def test_today_mute_persists_and_expires(settings: Settings):
    now = int(time.time())
    runtime = Runtime()
    assert proactive_mute.set_today(settings, now)["date"]
    assert proactive_mute.is_muted(settings, now) is True
    assert proactive_mute.restore(settings, runtime, now) is True
    assert runtime.enabled("proactive") is False
    assert runtime.snapshot()["proactive_muted"] is True

    runtime.set_proactive_mute(0)
    assert runtime.enabled("proactive") is True
    assert proactive_mute.clear(settings)["ok"] is True
    assert proactive_mute.is_muted(settings, now) is False


def test_diagnostics_payload_and_export_hides_key(settings: Settings, engine):
    payload = diagnostics_payload(settings)
    assert {item["name"] for item in payload["ports"]} >= {"控制台", "OneBot API", "NapCat WebUI"}
    assert payload["model"]["configured"] is True
    assert payload["log"]["exists"] is True

    export = export_diagnostics(settings, engine.guard)
    assert export["filename"].endswith(".txt")
    assert "line-1" in export["content"]
    assert "sk-secret-value-123456" not in export["content"]


def test_settings_save_proactive_slider_and_quiet(settings: Settings):
    result = save_settings(
        settings,
        {
            "proactive_enabled": True,
            "proactive_max_per_day": 8,
            "proactive_quiet_start_hour": 22,
            "proactive_quiet_end_hour": 7,
        },
    )
    assert result["ok"] is True
    env = (settings.base_dir / ".env").read_text(encoding="utf-8")
    assert "PROACTIVE_MAX_PER_DAY=8" in env
    assert "PROACTIVE_QUIET_START_HOUR=22" in env
    assert "PROACTIVE_QUIET_END_HOUR=7" in env
    with pytest.raises(ValueError):
        save_settings(
            settings,
            {"proactive_quiet_start_hour": 8, "proactive_quiet_end_hour": 8},
        )


def test_http_memory_templates_diagnostics(settings: Settings, engine):
    card_id = engine.store.add_card("fact", "他喜欢猫")
    app = WebUI(engine, settings, Runtime())
    assert app.start() is not None
    try:
        conn = http.client.HTTPConnection("127.0.0.1", settings.ui_port, timeout=5)
        conn.request("GET", f"/?token={app.token}")
        response = conn.getresponse()
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        response.read()
        headers = {"Cookie": cookie, "Origin": f"http://127.0.0.1:{settings.ui_port}", "Content-Type": "application/json"}

        conn.request("GET", "/api/memory", headers=headers)
        data = json.loads(conn.getresponse().read())
        assert data["items"][0]["id"] == card_id

        conn.request(
            "POST",
            "/api/memory/correct",
            body=json.dumps({"id": card_id, "summary": "他喜欢狗"}),
            headers=headers,
        )
        assert conn.getresponse().status == 200

        conn.request("GET", "/api/persona/templates", headers=headers)
        assert len(json.loads(conn.getresponse().read())["items"]) == 3

        conn.request("GET", "/api/diagnostics", headers=headers)
        assert json.loads(conn.getresponse().read())["model"]["configured"] is True
        conn.close()
    finally:
        app.stop()


def test_repo_default_persona_keeps_safety_bounds():
    root = Path(__file__).resolve().parents[1]
    text = (root / "defaults" / "prompts" / "system.md").read_text(encoding="utf-8")
    assert text.startswith("你是「大肥鱼」")
    assert "浣犳" not in text
    assert "不编造" in text
    assert "线下" in text
    assert "做不到" in text
    assert "安全边界" in text


def test_ui_has_new_pages_and_guide_title():
    root = Path(__file__).resolve().parents[1]
    html = (root / "qqbot" / "ui" / "index.html").read_text(encoding="utf-8")
    js = (root / "qqbot" / "ui" / "app.js").read_text(encoding="utf-8")
    css = (root / "qqbot" / "ui" / "app.css").read_text(encoding="utf-8")
    assert "她今天怎么样" in html
    assert 'data-page="memory"' in html
    assert 'id="page-memory"' in html
    assert 'data-page="diagnostics"' in html
    assert 'id="page-diagnostics"' in html
    assert "人格模板" in html
    assert "今天别找我了" in html
    assert "免打扰" in html
    assert "把两件事连上" not in html
    assert 'id="onboarding-title">引导<' in html
    assert "welcome-mascot.png" in html
    assert 'class="onboarding-welcome"' in html
    assert "DAFEIYU" in html
    overview = html.split('id="page-overview"', 1)[1].split("</section>", 1)[0]
    assert "主动消息" not in overview
    assert "今天别找我了" not in overview
    assert "mute-today" not in overview
    assert 'name="proactive_enabled"' in html
    assert 'class="ghost-btn mute-today"' in html
    for color in ("--brand-ink", "--brand-ocean", "--brand-cyan", "--brand-coral", "--brand-cream"):
        assert color in css
    assert "onboarding-mascot" in css
    assert "loadMemory" in js
    assert "exportDiagnostics" in js
