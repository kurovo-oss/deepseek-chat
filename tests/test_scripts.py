"""脚本文件的健壮性检查。

`.bat` 必须是 CRLF：cmd.exe 按 CRLF 计算解析偏移，LF-only 的文件会把命令
截断成 `EXE`、`hon` 这种碎片（真机上踩过）。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def test_bat_files_exist():
    assert list(SCRIPTS.glob("*.bat")), "scripts/ 下没有批处理文件"


def test_bat_files_use_crlf():
    bad = []
    for path in SCRIPTS.glob("*.bat"):
        without_crlf = path.read_bytes().replace(b"\r\n", b"")
        if b"\n" in without_crlf:
            bad.append(path.name)
    assert not bad, f"存在 LF-only 换行的 bat（cmd 会解析错）：{bad}"


def test_gitattributes_pins_bat_to_crlf():
    text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.bat text eol=crlf" in text


def test_start_bot_uses_project_local_python():
    text = (SCRIPTS / "start-bot.bat").read_text(encoding="utf-8", errors="replace")
    assert "tools\\python\\python.exe" in text
    assert "-m qqbot" in text


def test_start_bot_cds_to_project_root():
    text = (SCRIPTS / "start-bot.bat").read_text(encoding="utf-8", errors="replace")
    assert 'cd /d "%~dp0.."' in text


def test_start_all_launches_napcat_then_bot():
    text = (SCRIPTS / "start-all.bat").read_text(encoding="utf-8", errors="replace")
    assert "NapCat-*" in text
    assert "start-bot.bat" in text


def test_install_autostart_is_ascii_only():
    """PowerShell 5.1 按 ANSI 读 .ps1，非 ASCII 会乱码。"""
    for path in SCRIPTS.glob("*.ps1"):
        data = path.read_bytes()
        assert all(byte < 128 for byte in data), f"{path.name} 含非 ASCII 字符"


def test_restart_napcat_scripts_exist():
    assert (SCRIPTS / "restart-napcat.ps1").is_file()
    assert (SCRIPTS / "restart-napcat.bat").is_file()


def test_restart_napcat_self_elevates():
    text = (SCRIPTS / "restart-napcat.ps1").read_text(encoding="ascii")
    assert "RunAs" in text, "需要管理员权限，必须能自提升"


def test_restart_napcat_kills_only_napcat_tree():
    """只按 NapCatWinBootMain 的父子链杀，绝不按名字杀所有 QQ：
    这台机器上还有用户自己的 QQ 在跑。"""
    text = (SCRIPTS / "restart-napcat.ps1").read_text(encoding="ascii")
    assert "NapCatWinBootMain" in text
    assert "Get-ChildProcessId" in text
    assert "Name='QQ.exe'" not in text


def test_supervise_uses_project_local_python():
    text = (SCRIPTS / "supervise.ps1").read_text(encoding="utf-8")
    assert "tools\\python\\python.exe" in text
    assert "-m" in text and "qqbot" in text


def test_supervise_has_a_single_instance_guard():
    text = (SCRIPTS / "supervise.ps1").read_text(encoding="utf-8")
    assert "Mutex" in text


def test_autostart_uses_registry_and_keeps_c_free():
    text = (SCRIPTS / "install-autostart.ps1").read_text(encoding="utf-8")
    assert "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" in text
    assert "CreateShortcut" not in text


def test_stop_script_kills_supervisor_and_bot():
    text = (SCRIPTS / "stop-bot.ps1").read_text(encoding="utf-8")
    assert "supervise" in text
    assert "qqbot" in text


def test_stop_script_does_not_kill_itself():
    """实测踩过：杀掉命令行里带 supervise.ps1 的 powershell，把自己也杀了。"""
    text = (SCRIPTS / "stop-bot.ps1").read_text(encoding="utf-8")
    assert "$PID" in text
    assert 'notmatch "stop-bot"' in text
