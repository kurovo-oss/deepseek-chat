from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_launcher_exe_is_built():
    exe = ROOT / "大肥鱼.exe"
    assert exe.is_file()
    assert exe.stat().st_size > 100_000
    assert exe.read_bytes()[:2] == b"MZ"


def test_native_sources_exist_and_talk_to_local_api():
    sources = list((ROOT / "scripts" / "native").glob("*.cs"))
    assert sources
    source = "\n".join(path.read_text(encoding="utf-8") for path in sources)
    assert "supervise.ps1" in source
    assert "stop-bot.ps1" in source
    assert "ui-token" in source
    assert "UI_PORT" in source
    assert "X-UI-Token" in source
    assert "System.Web.Extensions" in (ROOT / "scripts" / "build-exe.ps1").read_text(encoding="utf-8")


def test_launcher_icon_exists():
    assert (ROOT / "qqbot" / "ui" / "assets" / "dafeiyu.ico").stat().st_size > 10_000
    assert (ROOT / "docs" / "native-ui.png").stat().st_size > 20_000


def test_build_script_is_ascii_safe_for_powershell_51():
    """Windows PowerShell 5.1 读无 BOM UTF-8 脚本会按 ANSI，中文会变问号。"""
    data = (ROOT / "scripts" / "build-exe.ps1").read_bytes()
    assert not any(byte > 127 for byte in data)
    assert b"0x5927" in data  # 大
