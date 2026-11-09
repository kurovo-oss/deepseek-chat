"""AGENTS.md 是 agent 的交付合同：关键约束被删掉时，测试要响。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_agents() -> str:
    return (ROOT / "AGENTS.md").read_text(encoding="utf-8")


def test_agents_md_has_the_operational_contract():
    text = read_agents()

    assert text.startswith("# AGENTS.md")
    for phrase in (
        r"tools\python\python.exe",
        "pytest -q",
        "git commit",
        "data/",
        ".env",
        "PyInstaller",
        r"scripts\build-exe.ps1",
        r"installer\build-installer.ps1",
        "evals/compact.json",
        "2,700 token",
        "UTF-8 with BOM",
        "SFW",
    ):
        assert phrase in text, f"AGENTS.md 缺少关键约束：{phrase}"


def test_agents_md_keeps_the_delivery_checklist():
    text = read_agents()

    assert "## 11. 快速自检" in text
    for item in ("回归测试", "全绿", "git status", "没有提交"):
        assert item in text, f"交付自检缺少：{item}"
