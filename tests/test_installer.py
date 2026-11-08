from __future__ import annotations

from pathlib import Path

from PIL import Image

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


def test_installer_has_branded_welcome_and_finish_pages():
    data = (ROOT / "installer" / "Dafeiyu.nsi").read_bytes()
    assert data[:3] == b"\xef\xbb\xbf", "NSIS Unicode 脚本必须带 UTF-8 BOM"
    script = data.decode("utf-8-sig")
    assert '!define MUI_BGCOLOR "061B2C"' in script
    assert '!define MUI_TEXTCOLOR "EAF6FF"' in script
    assert "brand-panel.bmp" in script
    assert '!define MUI_WELCOMEPAGE_TITLE "把大肥鱼接回家"' in script
    assert '!define MUI_FINISHPAGE_TITLE "大肥鱼已经住进你的电脑"' in script
    assert '!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\\README.md"' in script
    assert '!define MUI_FINISHPAGE_RUN_TEXT "现在就叫她出来"' in script


def test_brand_mascot_and_installer_assets_exist():
    welcome = ROOT / "qqbot" / "ui" / "assets" / "welcome-mascot.png"
    icon = ROOT / "qqbot" / "ui" / "assets" / "dafeiyu.ico"
    source = ROOT / "qqbot" / "ui" / "assets" / "dafeiyu.png"
    panel = ROOT / "installer" / "assets" / "brand-panel.bmp"

    for path in (welcome, source, icon, panel):
        assert path.stat().st_size > 20_000, path

    with Image.open(welcome) as image:
        assert image.size == (900, 900)
        assert image.mode == "RGBA"
    with Image.open(panel) as image:
        assert image.size == (164, 314)
        assert image.mode == "RGB"
    with Image.open(icon) as image:
        assert (256, 256) in image.ico.sizes()
        assert (16, 16) in image.ico.sizes()

    generator = (ROOT / "scripts" / "make-brand-assets.py").read_text(encoding="utf-8")
    assert "make_welcome" in generator
    assert "make_brand_panel" in generator
    assert 'SOURCE = UI_ASSETS / "dafeiyu.png"' in generator
    assert "draw_whale" not in generator
    assert not (ROOT / "qqbot" / "ui" / "assets" / "dafeiyu-fish.ico").exists()


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
