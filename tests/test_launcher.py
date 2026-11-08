from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_launcher_exe_is_built():
    exe = ROOT / "大肥鱼.exe"
    assert exe.is_file()
    assert exe.stat().st_size > 50_000
    assert exe.read_bytes()[:2] == b"MZ"


def test_launcher_icon_and_sources_exist():
    assert (ROOT / "qqbot" / "ui" / "assets" / "dafeiyu.ico").stat().st_size > 10_000
    source = (ROOT / "scripts" / "launcher" / "Launcher.cs").read_text(encoding="utf-8")
    assert "supervise.ps1" in source
    assert "ui-token" in source
    assert "UI_PORT" in source
    assert "stop-bot.ps1" in source


def test_build_script_is_ascii_safe_for_powershell_51():
    """Windows PowerShell 5.1 读无 BOM UTF-8 脚本会按 ANSI，中文会变问号。"""
    data = (ROOT / "scripts" / "build-exe.ps1").read_bytes()
    assert not any(byte > 127 for byte in data)
    assert b"0x5927" in data  # 大
