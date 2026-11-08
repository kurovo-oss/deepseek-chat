from pathlib import Path

import pytest

from qqbot.config import (
    ConfigError,
    Settings,
    load_settings,
    load_system_prompt,
    parse_dotenv,
)


def test_missing_required_reports_all():
    with pytest.raises(ConfigError) as exc:
        Settings.from_env({}, Path("."))
    message = str(exc.value)
    assert "ALLOW_USER_ID" in message
    assert "MODEL_API_KEY" in message


def test_parse_dotenv_forms():
    text = '# 注释\nexport A=1\nB="x y"\nC=\'z\'\nD=\nE = 5\n'
    parsed = parse_dotenv(text)
    assert parsed["A"] == "1"
    assert parsed["B"] == "x y"
    assert parsed["C"] == "z"
    assert parsed["D"] == ""
    assert parsed["E"] == "5"


def test_non_numeric_user_id_rejected():
    with pytest.raises(ConfigError):
        Settings.from_env({"ALLOW_USER_ID": "abc", "MODEL_API_KEY": "k"}, Path("."))


def test_defaults(tmp_path):
    settings = Settings.from_env({"ALLOW_USER_ID": "123", "MODEL_API_KEY": "k"}, tmp_path)
    assert settings.allow_user_id == 123
    assert settings.reply_max_chars == 300
    assert settings.segment_delay_ms == 1500
    assert settings.window_char_budget == int(4000 / 0.6)


def test_proactive_quiet_hours_config(tmp_path):
    settings = Settings.from_env(
        {
            "ALLOW_USER_ID": "123",
            "MODEL_API_KEY": "k",
            "PROACTIVE_QUIET_START_HOUR": "22",
            "PROACTIVE_QUIET_END_HOUR": "7",
        },
        tmp_path,
    )
    assert settings.proactive_quiet_start_hour == 22
    assert settings.proactive_quiet_end_hour == 7
    with pytest.raises(ConfigError):
        Settings.from_env(
            {
                "ALLOW_USER_ID": "123",
                "MODEL_API_KEY": "k",
                "PROACTIVE_QUIET_START_HOUR": "8",
                "PROACTIVE_QUIET_END_HOUR": "8",
            },
            tmp_path,
        )


def test_loads_dotenv_file(tmp_path):
    (tmp_path / ".env").write_text("ALLOW_USER_ID=42\nMODEL_API_KEY=kk\n", encoding="utf-8")
    settings = load_settings(base_dir=tmp_path, env={})
    assert settings.allow_user_id == 42


def test_process_env_overrides_dotenv(tmp_path):
    (tmp_path / ".env").write_text("ALLOW_USER_ID=42\nMODEL_API_KEY=kk\n", encoding="utf-8")
    settings = load_settings(base_dir=tmp_path, env={"ALLOW_USER_ID": "7"})
    assert settings.allow_user_id == 7


def test_system_prompt_falls_back_when_file_missing(tmp_path):
    settings = Settings.from_env({"ALLOW_USER_ID": "1", "MODEL_API_KEY": "k"}, tmp_path)
    prompt = load_system_prompt(settings)
    assert prompt.strip()
    assert "大肥鱼" in prompt


def test_parse_dotenv_inline_comment():
    parsed = parse_dotenv('A=1 # 说明\nB=abc #x\nC="ke#ep"\nD=p#ss\n')
    assert parsed["A"] == "1"
    assert parsed["B"] == "abc"
    assert parsed["C"] == "ke#ep"
    assert parsed["D"] == "p#ss"  # 前面没有空格，# 属于值本身


def test_image_defaults(tmp_path):
    settings = Settings.from_env({"ALLOW_USER_ID": "1", "MODEL_API_KEY": "k"}, tmp_path)
    assert settings.image_enabled is True
    assert settings.image_cooldown_messages == 10
    assert settings.image_send_mode == "path"
    assert settings.image_lib_dir == tmp_path / "assets/seed-images"
    assert settings.log_dir == tmp_path / "logs"


def test_invalid_image_send_mode(tmp_path):
    with pytest.raises(ConfigError):
        Settings.from_env(
            {"ALLOW_USER_ID": "1", "MODEL_API_KEY": "k", "IMAGE_SEND_MODE": "ftp"},
            tmp_path,
        )


def test_invalid_bool(tmp_path):
    with pytest.raises(ConfigError):
        Settings.from_env(
            {"ALLOW_USER_ID": "1", "MODEL_API_KEY": "k", "IMAGE_ENABLED": "maybe"},
            tmp_path,
        )


def test_image_cooldown_can_be_zero(tmp_path):
    settings = Settings.from_env(
        {"ALLOW_USER_ID": "1", "MODEL_API_KEY": "k", "IMAGE_COOLDOWN_MESSAGES": "0"},
        tmp_path,
    )
    assert settings.image_cooldown_messages == 0


def test_system_prompt_reads_file(tmp_path):
    prompt_file = tmp_path / "s.md"
    prompt_file.write_text("自定义人格", encoding="utf-8")
    settings = Settings.from_env(
        {"ALLOW_USER_ID": "1", "MODEL_API_KEY": "k", "SYSTEM_PROMPT_FILE": "s.md"},
        tmp_path,
    )
    assert load_system_prompt(settings) == "自定义人格"


def test_she_initiates_more_often_by_default(tmp_path):
    """她要"更黏"：默认允许一天主动说几句，而不是非等三天。"""
    from qqbot.config import Settings

    settings = Settings(allow_user_id=1, model_api_key="k", base_dir=tmp_path,
                        db_path=tmp_path / "d.db", system_prompt_file=tmp_path / "s.md")

    assert settings.proactive_max_per_day >= 4
    assert settings.proactive_idle_hours <= 8
    assert settings.proactive_min_gap_hours <= 3.5

def test_qzone_daily_max_defaults_to_one(monkeypatch):
    from qqbot.config import Settings

    assert Settings.__dataclass_fields__["qzone_daily_max"].default == 1

