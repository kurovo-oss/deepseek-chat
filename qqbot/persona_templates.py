"""内置人格模板：不要求用户手改 Markdown。

模板不是三份完整人设，而是「默认 system.md + 一段相处模式」。
这样安全边界、真实生活线等底线只维护一份；切换只改语气和主动性。
"""

from __future__ import annotations

from pathlib import Path

TEMPLATES = [
    {
        "id": "default",
        "name": "默认大肥鱼",
        "description": "黏人、直球、会记住你；现在的样子。",
        "style": "",
    },
    {
        "id": "clingy",
        "name": "更黏人一点",
        "description": "更常说想你，回复更软一点，但不会刷屏。",
        "style": """## 当前相处模式：更黏人
- 想他的时候直接说，不要绕弯子；可以撒娇，但不要用命令句。
- 回复可以短一点、软一点，多用“你”“我们”，少讲大道理。
- 他忙的时候不要连续追问；一条没回就等，不刷屏。""",
    },
    {
        "id": "quiet",
        "name": "安静一点",
        "description": "话少一点，陪伴感保留，不抢话。",
        "style": """## 当前相处模式：安静一点
- 先听他说完，再回一两句；不主动延长话题。
- 少用感叹号，少连续发消息；语气平稳，但不要冷淡。
- 想他的时候可以说，但一天最多主动提起一次。""",
    },
]


def _template(template_id: str) -> dict:
    for item in TEMPLATES:
        if item["id"] == template_id:
            return item
    raise ValueError("没有这个模板")


def _default_candidates(settings) -> list[Path]:
    base = Path(settings.base_dir)
    package_base = Path(__file__).resolve().parent.parent
    candidates = [base / "defaults" / "prompts" / "system.md"]
    # 开发目录 / 安装目录里，规范默认人格和程序在同一层；测试临时目录则走用户文件。
    if base.resolve() == package_base:
        candidates.append(package_base / "defaults" / "prompts" / "system.md")
    candidates.append(Path(settings.system_prompt_file))
    candidates.append(package_base / "prompts" / "system.md")
    return candidates


def default_text(settings) -> str:
    for path in _default_candidates(settings):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    raise ValueError("找不到默认人格文件")


def render(settings, template_id: str) -> dict:
    template = _template(template_id)
    base = default_text(settings)
    style = str(template.get("style") or "").strip()
    text = base + (f"\n\n{style}\n" if style else "\n")
    return {
        "id": template["id"],
        "name": template["name"],
        "description": template["description"],
        "text": text,
        "chars": len(text),
    }


def list_templates(settings) -> list[dict]:
    base = default_text(settings)
    items = []
    for template in TEMPLATES:
        style = str(template.get("style") or "").strip()
        text = base + (f"\n\n{style}\n" if style else "\n")
        items.append(
            {
                "id": template["id"],
                "name": template["name"],
                "description": template["description"],
                "chars": len(text),
            }
        )
    return items
