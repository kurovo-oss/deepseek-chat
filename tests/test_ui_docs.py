"""控制台设计稿的最低完整性检查：文档在、原型能打开、页面没漏。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PAGES = ("overview", "chat", "persona", "model", "qq", "behavior", "memory", "logs", "settings")


def test_mockup_is_self_contained_and_has_all_pages():
    html = (ROOT / "docs" / "ui-mockup.html").read_text(encoding="utf-8")

    assert html.lstrip().startswith("<!doctype html>")
    for page in PAGES:
        assert f'id="page-{page}"' in html
    assert html.count('<section class="page') == len(PAGES)
    # 不引 CDN / 外部脚本，双击就能看
    assert "<script src=" not in html
    assert 'rel="stylesheet"' not in html
    assert "https://cdn" not in html
    # 直接用表情包库里的形象，不依赖外部图片
    assert 'src="assets/dafeiyu.png"' in html


def test_design_doc_has_the_important_sections():
    text = (ROOT / "docs" / "ui-design.md").read_text(encoding="utf-8")

    for heading in ("信息架构", "视觉系统", "页面设计", "技术方案", "分阶段落地", "验收清单"):
        assert heading in text
    assert "ui-mockup.html" in text
    assert "127.0.0.1" in text
