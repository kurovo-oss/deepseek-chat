from __future__ import annotations

import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_launcher_exe_is_built():
    exe = ROOT / "大肥鱼.exe"
    assert exe.is_file()
    assert exe.stat().st_size > 100_000
    assert exe.read_bytes()[:2] == b"MZ"


def test_native_shell_hosts_the_web_console_in_webview2():
    sources = list((ROOT / "scripts" / "native").glob("*.cs"))
    assert sources
    source = "\n".join(path.read_text(encoding="utf-8") for path in sources)
    assert "WebView2" in source
    assert "CoreWebView2Environment.CreateAsync" in source
    assert "webview2" in source
    assert "supervise.ps1" in source
    assert "stop-bot.ps1" in source
    assert "ui-token" in source
    assert "UI_PORT" in source
    assert "X-UI-Token" not in source

    build = (ROOT / "scripts" / "build-exe.ps1").read_text(encoding="utf-8")
    assert "Microsoft.Web.WebView2.Core.dll" in build
    assert "Microsoft.Web.WebView2.WinForms.dll" in build
    assert "WebView2Loader.dll" in build
    assert "/platform:x64" in build


def test_webview2_runtime_files_are_bundled():
    expected = {
        "Microsoft.Web.WebView2.Core.dll": 300_000,
        "Microsoft.Web.WebView2.WinForms.dll": 20_000,
        "WebView2Loader.dll": 100_000,
    }
    for name, minimum in expected.items():
        path = ROOT / name
        assert path.is_file(), name
        assert path.stat().st_size > minimum, name


def test_launcher_icon_and_screenshot_exist():
    assert (ROOT / "qqbot" / "ui" / "assets" / "dafeiyu.ico").stat().st_size > 10_000
    shot = (ROOT / "docs" / "native-ui.png").read_bytes()
    assert len(shot) > 20_000
    assert shot[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", shot[16:24])
    assert width >= 1200
    assert height >= 700


def test_build_script_is_ascii_safe_for_powershell_51():
    """Windows PowerShell 5.1 读无 BOM UTF-8 脚本会按 ANSI，中文会变问号。"""
    data = (ROOT / "scripts" / "build-exe.ps1").read_bytes()
    assert not any(byte > 127 for byte in data)
    assert b"0x5927" in data  # 大
