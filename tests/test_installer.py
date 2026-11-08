from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_installer_script_is_a_per_user_windows_installer():
    data = (ROOT / "installer" / "Dafeiyu.nsi").read_bytes()
    assert data[:3] == b"\xef\xbb\xbf", "NSIS Unicode 脚本必须带 UTF-8 BOM"
    script = data.decode("utf-8-sig")
    assert "Unicode true" in script
    assert "RequestExecutionLevel user" in script
    assert "$LOCALAPPDATA\\Dafeiyu" in script
    assert "MUI_PAGE_LICENSE" in script
    assert "WebView2" in script
    assert "WriteConfig" in script
    assert "setup.env" in script
    assert "ALLOW_USER_ID" in script
    assert "MODEL_API_KEY" in script
    assert "GetOptions" in script
    assert "Uninstall.exe" in script
    assert 'RMDir /r "$INSTDIR\\assets"' in script
    assert 'RMDir /r "$INSTDIR\\docs"' in script
    assert "IfSilent keep_data" in script
    assert 'Delete "$INSTDIR\\SAFETY.md"' in script
    assert 'Delete "$INSTDIR\\THIRD-PARTY-NOTICES.md"' in script


def test_installer_build_is_reproducible():
    script = (ROOT / "installer" / "build-installer.ps1").read_text(encoding="utf-8")
    assert "makensis.exe" in script
    assert "build\\installer" in script
    assert "payload" in script
    assert "setup.env" in script
    assert "SAFETY.md" in script
    assert "THIRD-PARTY-NOTICES.md" in script
    assert "Dafeiyu-Setup-1.0.0.exe" in script
    assert "tools\\python" in script
    assert "NapCat-" in script
    assert not any(byte > 127 for byte in (ROOT / "installer" / "build-installer.ps1").read_bytes())


def test_native_shell_title_matches_home_page():
    source = (ROOT / "scripts" / "native" / "WebShell.cs").read_text(encoding="utf-8")
    assert 'Text = "大肥鱼 · 她今天怎么样"' in source
    assert 'Text = "大肥鱼 · 控制台"' not in source


def test_installer_ships_safe_default_persona():
    build = (ROOT / "installer" / "build-installer.ps1").read_text(encoding="utf-8")
    assert "defaults\\prompts" in build
    default = ROOT / "defaults" / "prompts" / "system.md"
    text = default.read_text(encoding="utf-8")
    assert "不编造" in text
    assert "线下" in text
    assert "做不到" in text


def test_installer_does_not_ship_local_data_or_secrets():
    build = (ROOT / "installer" / "build-installer.ps1").read_text(encoding="utf-8")
    # 安装包只复制白名单目录；data、logs、.env 不在复制列表中。
    assert '"data"' not in build
    assert '"logs"' not in build
    assert '"tests"' not in build
    assert "Copy-File \".env\"" not in build


def test_env_example_has_one_setting_per_line():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert "\\n" not in line, f".env.example 第 {number} 行把换行写成了字面量"
        assert "=" in line, f".env.example 第 {number} 行不是 KEY=VALUE"
