from __future__ import annotations

import http.client
import json
import socket
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from qqbot.config import Settings, parse_dotenv
from qqbot.runtime import Runtime
from qqbot.store import Store
from qqbot.webui import (
    WebUI,
    build_status,
    read_persona,
    save_persona,
    save_settings,
    settings_payload,
    tail_log,
)


class FakeMood:
    def snapshot(self, now=None):
        return {"miss": 0.15, "warmth": 0.68, "tired": 0.2}


class FakeGuard:
    def status(self, now=None):
        return {
            "qzone": {"enabled": True, "dry_run": False, "used_today": 1, "daily_quota": 1, "circuit_open": False},
            "douyin": {"enabled": True, "dry_run": False, "used_today": 2, "daily_quota": 5, "circuit_open": False},
        }

    def recent(self, limit=20):
        return [
            {
                "at": int(time.time()),
                "module": "qzone",
                "action": "publish",
                "target": "说说",
                "result": "ok",
                "error": "",
            }
        ]


def test_ui_does_not_show_extra_labels():
    root = Path(__file__).resolve().parents[1]
    html = (root / "qqbot" / "ui" / "index.html").read_text(encoding="utf-8")
    js = (root / "qqbot" / "ui" / "app.js").read_text(encoding="utf-8")
    assert "只属于你的控制台" not in html
    assert "蓝鲸娘" not in html
    assert "hero-pid" not in html
    assert "PID" not in html
    assert "mood-line" not in html
    assert "他说过的话，都在这里" not in js
    assert "hero-pid" not in js
    assert "mood-line" not in js


@pytest.fixture()
def base(tmp_path: Path) -> Path:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "prompts" / "system.md").write_text(
        "# 大肥鱼\n\n" + "你要记住做不到的事就说做不到，不要编造，也不要把话题带到线下。" * 8,
        encoding="utf-8",
    )
    (tmp_path / "logs" / "bot.log").write_text("line-1\nline-2\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "# 注释要保留\nALLOW_USER_ID=10001\nMODEL_API_KEY=sk-old-key-123456\nMODEL_ID=old-model\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def settings(base: Path) -> Settings:
    return Settings(
        allow_user_id=10001,
        model_api_key="sk-old-key-123456",
        base_dir=base,
        db_path=base / "data" / "chat.db",
        system_prompt_file=base / "prompts" / "system.md",
        log_dir=base / "logs",
        model_id="old-model",
        model_base_url="https://example.com/v1",
        ui_port=8090,
    )


@pytest.fixture()
def store(settings: Settings) -> Store:
    store = Store(settings.db_path)
    yield store
    store.close()


@pytest.fixture()
def engine(settings: Settings, store: Store):
    return SimpleNamespace(
        settings=settings,
        store=store,
        mood=FakeMood(),
        guard=FakeGuard(),
        system_prompt="old prompt",
    )


def test_status_reports_mood_today_and_runtime(settings: Settings, store: Store, engine):
    now = int(time.time())
    store.save_message(str(settings.allow_user_id), "user", "在吗", created_at=now)
    store.save_message(str(settings.allow_user_id), "assistant", "在呀", created_at=now, origin="reply")
    runtime = Runtime()
    runtime.set_paused("douyin", True)

    payload = build_status(engine, runtime, now=now)

    assert payload["mood"]["values"]["warmth"] == pytest.approx(0.68)
    assert payload["today"]["user"] == 1
    assert payload["today"]["bot"] == 1
    assert payload["runtime"]["paused"]["douyin"] is True
    assert payload["guard"]["qzone"]["used_today"] == 1
    assert payload["alerts"][0]["title"] == "空间今日额度已用完"


def test_persona_publish_versions_and_hot_reload(settings: Settings, engine):
    text = "# 大肥鱼\n\n" + "做不到的事要直说，不编造，也不线下见面。" * 20
    result = save_persona(settings, engine, text, note="测试")

    assert result["ok"] is True
    assert Path(settings.system_prompt_file).read_text(encoding="utf-8") == text.strip()
    assert engine.system_prompt == text.strip()
    payload = read_persona(settings)
    assert payload["versions"][0]["note"] == "测试"
    assert payload["chars"] == len(text.strip())

    with pytest.raises(ValueError):
        save_persona(settings, engine, "太短")
    with pytest.raises(ValueError, match="安全边界"):
        save_persona(settings, engine, "随便写" * 100)
    with pytest.raises(ValueError, match="安全边界"):
        save_persona(settings, engine, "# 大肥鱼\n\n" + "不要编造，好好说话。" * 30)


def test_settings_payload_never_exposes_key(settings: Settings):
    payload = settings_payload(settings)
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "sk-old-key-123456" not in dumped
    assert payload["model"]["api_key_set"] is True
    assert payload["model"]["api_key_hint"].endswith("3456")


def test_save_settings_preserves_comments_and_backs_up(settings: Settings, base: Path):
    result = save_settings(
        settings,
        {
            "master_qq": "12345678",
            "model_id": "new-model",
            "model_api_key": "sk-new-key-abcdef",
            "proactive_max_per_day": 3,
            "qzone_dry_run": False,
        },
    )

    assert result["restart_required"] is True
    env_text = (base / ".env").read_text(encoding="utf-8")
    assert "# 注释要保留" in env_text
    values = parse_dotenv(env_text)
    assert values["ALLOW_USER_ID"] == "12345678"
    assert values["MODEL_ID"] == "new-model"
    assert values["MODEL_API_KEY"] == "sk-new-key-abcdef"
    assert values["PROACTIVE_MAX_PER_DAY"] == "3"
    assert values["QZONE_DRY_RUN"] == "false"
    assert (base / ".env.bak").exists()


def test_save_settings_rejects_bad_values(settings: Settings):
    with pytest.raises(ValueError):
        save_settings(settings, {"master_qq": "abc"})
    with pytest.raises(ValueError):
        save_settings(settings, {"model_base_url": "file:///etc/passwd"})
    with pytest.raises(ValueError):
        save_settings(settings, {"qzone_daily_max": 999})


def test_tail_log_reads_last_lines(settings: Settings, base: Path):
    (base / "logs" / "bot.log").write_text("a\nb\nc\n", encoding="utf-8")
    assert tail_log(settings, 2) == ["b\n", "c\n"]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_runtime_controls_pause_and_emergency():
    runtime = Runtime()
    assert runtime.enabled("qzone") is True
    runtime.set_paused("qzone", True)
    assert runtime.paused("qzone") is True
    runtime.set_emergency(True)
    assert runtime.paused("proactive") is True
    runtime.resume_all()
    assert runtime.snapshot() == {
        "emergency": False,
        "paused": {"proactive": False, "qzone": False, "douyin": False},
        "proactive_muted": False,
        "proactive_muted_until": 0,
    }
    with pytest.raises(ValueError):
        runtime.set_paused("unknown", True)


def test_ui_shell_is_local_and_uses_mascot():
    root = Path(__file__).resolve().parents[1]
    ui = root / "qqbot" / "ui"
    html = (ui / "index.html").read_text(encoding="utf-8")
    js = (ui / "app.js").read_text(encoding="utf-8")
    assert "/assets/dafeiyu.png" in html
    assert '<script src="http' not in html
    assert 'rel="stylesheet" href="http' not in html
    assert "https://" not in js
    assert (ui / "assets" / "dafeiyu.png").stat().st_size > 100_000


def test_http_auth_and_runtime_toggle(settings: Settings, engine):
    settings = Settings(
        **{
            **settings.__dict__,
            "ui_port": _free_port(),
        }
    )
    app = WebUI(engine, settings, Runtime())
    assert app.start() is not None
    try:
        host, port = "127.0.0.1", settings.ui_port
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", f"/?token={app.token}")
        response = conn.getresponse()
        assert response.status == 302
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        response.read()

        conn.request("GET", "/api/status", headers={"Cookie": cookie})
        response = conn.getresponse()
        assert response.status == 200
        data = json.loads(response.read().decode("utf-8"))
        assert data["master_qq"] == str(settings.allow_user_id)

        conn.request(
            "PUT",
            "/api/runtime",
            body=json.dumps({"module": "qzone", "paused": True}),
            headers={
                "Cookie": cookie,
                "Origin": f"http://{host}:{port}",
                "Content-Type": "application/json",
            },
        )
        response = conn.getresponse()
        assert response.status == 200
        assert json.loads(response.read().decode("utf-8"))["paused"]["qzone"] is True
        conn.close()
    finally:
        app.stop()


def test_native_token_header_is_enough_for_write(settings: Settings, engine):
    settings = Settings(**{**settings.__dict__, "ui_port": _free_port()})
    app = WebUI(engine, settings, Runtime())
    assert app.start() is not None
    try:
        conn = http.client.HTTPConnection("127.0.0.1", settings.ui_port, timeout=5)
        conn.request(
            "PUT",
            "/api/runtime",
            body=json.dumps({"module": "qzone", "paused": True}),
            headers={"X-UI-Token": app.token, "Content-Type": "application/json"},
        )
        response = conn.getresponse()
        assert response.status == 200
        assert json.loads(response.read().decode("utf-8"))["paused"]["qzone"] is True
        conn.close()
    finally:
        app.stop()


def test_http_rejects_unauthorized_and_cross_origin(settings: Settings, engine):
    settings = Settings(**{**settings.__dict__, "ui_port": _free_port()})
    app = WebUI(engine, settings, Runtime())
    assert app.start() is not None
    try:
        host, port = "127.0.0.1", settings.ui_port
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/api/status")
        assert conn.getresponse().status == 401

        conn.request("GET", f"/?token={app.token}")
        response = conn.getresponse()
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        response.read()
        conn.request(
            "PUT",
            "/api/runtime",
            body=json.dumps({"emergency": True}),
            headers={"Cookie": cookie, "Origin": "http://evil.example", "Content-Type": "application/json"},
        )
        assert conn.getresponse().status == 403
        conn.close()
    finally:
        app.stop()
