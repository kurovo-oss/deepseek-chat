from __future__ import annotations

import http.client
import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from qqbot import onboarding
from qqbot.config import Settings
from qqbot.webui import WebUI


class FakeSender:
    def __init__(self, info=None):
        self.info = info

    def login_info(self):
        return self.info


def make_settings(base: Path) -> Settings:
    return Settings(
        allow_user_id=10001,
        model_api_key="sk-test-1234567890",
        base_dir=base,
        db_path=base / "data" / "chat.db",
        system_prompt_file=base / "prompts" / "system.md",
        model_id="deepseek-flash",
        model_base_url="https://api.deepseek.com",
        ui_port=8090,
    )


def test_model_state_and_hint_never_expose_key():
    settings = make_settings(Path("."))
    state = onboarding.model_state(settings)
    assert state["configured"] is True
    assert state["master_set"] is True
    assert state["key_set"] is True
    assert "sk-test-1234567890" not in json.dumps(state, ensure_ascii=False)
    assert state["key_hint"].endswith("7890")


def test_test_model_validates_and_can_probe(monkeypatch):
    settings = make_settings(Path("."))

    with pytest.raises(ValueError, match="http"):
        onboarding.test_model(settings, base_url="file:///etc/passwd")
    with pytest.raises(ValueError, match="Key"):
        onboarding.test_model(settings, api_key="")

    class FakeLLM:
        def __init__(self, base_url, api_key, model, timeout, retries):
            assert base_url == "https://api.example/v1"
            assert api_key == "sk-test"
            assert model == "model-x"
            assert retries == 0

        def probe(self):
            return True, "model-x"

    monkeypatch.setattr(onboarding, "LLMClient", FakeLLM)
    result = onboarding.test_model(
        settings,
        base_url="https://api.example/v1/",
        model_id="model-x",
        api_key="sk-test",
    )
    assert result["ok"] is True
    assert result["model_id"] == "model-x"
    assert result["latency_ms"] >= 0


def test_napcat_config_and_login_url(tmp_path: Path):
    napcat = tmp_path / "tools" / "NapCat-v9.9.9" / "napcat" / "config"
    napcat.mkdir(parents=True)
    (napcat / "webui.json").write_text(
        json.dumps({"host": "::", "port": 6099, "token": "abc123"}),
        encoding="utf-8",
    )
    settings = make_settings(tmp_path)

    state = onboarding.napcat_state(settings)
    assert state["found"] is True
    assert state["port"] == 6099
    assert state["login_page_available"] is True
    assert "abc123" not in json.dumps(state, ensure_ascii=False)

    url = onboarding.napcat_login_url(settings)
    assert url.startswith("http://127.0.0.1:6099/webui?")
    assert "token=abc123" in url


def test_open_napcat_login_uses_browser(monkeypatch, tmp_path: Path):
    napcat = tmp_path / "tools" / "NapCat-v9.9.9" / "napcat" / "config"
    napcat.mkdir(parents=True)
    (napcat / "webui.json").write_text(
        json.dumps({"host": "::", "port": 6099, "token": "abc123"}),
        encoding="utf-8",
    )
    settings = make_settings(tmp_path)
    opened = []
    monkeypatch.setattr(onboarding.webbrowser, "open", lambda url, new=0: opened.append(url) or True)

    result = onboarding.open_napcat_login(settings)
    assert result["ok"] is True
    assert opened and "token=abc123" in opened[0]


def test_start_napcat_is_noop_when_port_is_open(monkeypatch, tmp_path: Path):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(onboarding, "napcat_state", lambda _settings: {"running": True})
    called = []
    monkeypatch.setattr(onboarding.subprocess, "Popen", lambda *a, **k: called.append((a, k)))
    result = onboarding.start_napcat(settings)
    assert result["started"] is False
    assert called == []


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_start_napcat_launches_launcher(monkeypatch, tmp_path: Path):
    root = tmp_path / "tools" / "NapCat-v9.9.9"
    root.mkdir(parents=True)
    launcher = root / "launcher.bat"
    launcher.write_text("echo hi", encoding="ascii")
    settings = make_settings(tmp_path)
    monkeypatch.setattr(onboarding, "napcat_state", lambda _settings: {"running": False})
    calls = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(onboarding.subprocess, "Popen", FakePopen)
    result = onboarding.start_napcat(settings)
    assert result["started"] is True
    assert calls
    assert calls[0][0][:2] == ["cmd.exe", "/c"]
    assert Path(calls[0][1]["cwd"]) == root


def test_build_state_needs_onboarding_until_finished(tmp_path: Path):
    settings = make_settings(tmp_path)
    assert onboarding.build_state(settings, FakeSender(None))["needed"] is True
    assert onboarding.build_state(settings, FakeSender({"nickname": "鱼", "user_id": 1}))["needed"] is False

    onboarding.finish(settings)
    state = onboarding.build_state(settings, FakeSender(None))
    assert state["finished"] is True
    assert state["needed"] is False
    assert (tmp_path / "data" / "onboarding.done").is_file()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_http_onboarding_finish_requires_auth_and_marks_file(tmp_path: Path):
    settings = make_settings(tmp_path)
    settings = Settings(**{**settings.__dict__, "ui_port": _free_port()})
    engine = SimpleNamespace(sender=FakeSender({"nickname": "鱼", "user_id": 1}), guard=None)
    app = WebUI(engine, settings)
    assert app.start() is not None
    try:
        conn = http.client.HTTPConnection("127.0.0.1", settings.ui_port, timeout=5)
        conn.request("GET", "/api/onboarding")
        assert conn.getresponse().status == 401

        conn.request(
            "POST",
            "/api/onboarding/finish",
            body="{}",
            headers={"X-UI-Token": app.token, "Content-Type": "application/json"},
        )
        response = conn.getresponse()
        assert response.status == 200
        assert onboarding.is_finished(settings) is True
        conn.close()
    finally:
        app.stop()


def test_ui_has_first_run_onboarding():
    root = Path(__file__).resolve().parents[1]
    html = (root / "qqbot" / "ui" / "index.html").read_text(encoding="utf-8")
    js = (root / "qqbot" / "ui" / "app.js").read_text(encoding="utf-8")
    css = (root / "qqbot" / "ui" / "app.css").read_text(encoding="utf-8")
    assert 'id="onboarding"' in html
    assert "第一次打开" in html
    assert "/api/onboarding/test-model" in js
    assert "/api/onboarding/open-napcat" in js
    assert "账号被限制" in js
    assert ".onboarding-panel" in css
