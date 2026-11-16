"""文档一致性：链接不断、索引不漏、旧设计有指向、新设计关键词齐全。

文档腐烂往往无声无息——改了实现忘了改文档，或者把旧方案当成了现行方案。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

REL_LINK = re.compile(r"\]\(([^)\s]+)\)")
HTML_LINK = re.compile(r'(?:src|href)="([^"]+)"')


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_readme_markdown_links_exist():
    text = read("README.md")
    links = re.findall(r"\]\(([^)\s]+\.md)\)", text)
    assert links, "README 里没有任何文档链接"
    missing = [link for link in links if not (ROOT / link).exists()]
    assert not missing, f"README 里存在失效链接：{missing}"


def test_docs_index_lists_every_doc():
    """`docs/README.md` 是索引：新增文档不登记，等于没人找得到它。"""
    index = read("docs/README.md")
    missing = []
    for path in sorted(DOCS.rglob("*.md")):
        if path == DOCS / "README.md":
            continue
        rel = path.relative_to(DOCS).as_posix()
        if f"]({rel})" not in index:
            missing.append(rel)
    assert not missing, f"docs/README.md 没有登记这些文档：{missing}"


def test_all_relative_links_in_docs_resolve():
    """文档里的相对链接必须真能点开——搬目录最容易漏掉的就是这步。"""
    broken = []
    for path in list(DOCS.rglob("*.md")) + list(DOCS.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        targets = set(REL_LINK.findall(text))
        if path.suffix == ".html":
            targets |= set(HTML_LINK.findall(text))
        for target in targets:
            if re.match(r"^(https?:|mailto:|#|data:)", target):
                continue
            target_path = (path.parent / target.split("#")[0]).resolve()
            if not target_path.exists():
                broken.append(f"{path.relative_to(ROOT).as_posix()} -> {target}")
    assert not broken, f"文档里存在失效的相对链接：{broken}"


def test_repo_root_has_standard_project_files():
    """根目录该有的都在这儿：别人 clone 下来先看的就是这些。"""
    for name in (
        "README.md",
        "LICENSE",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "SAFETY.md",
        "THIRD-PARTY-NOTICES.md",
        "pyproject.toml",
        ".editorconfig",
        ".gitignore",
        ".env.example",
    ):
        assert (ROOT / name).exists(), f"根目录缺少 {name}"


def test_m2_design_doc_exists_and_is_linked():
    assert (DOCS / "design" / "M2-memory.md").exists()
    assert "docs/design/M2-memory.md" in read("README.md")


def test_progress_doc_is_linked_and_covers_milestones():
    """进度文档必须覆盖每个里程碑，否则它会慢慢变成谎言。"""
    assert (DOCS / "ops" / "PROGRESS.md").exists()
    assert "docs/ops/PROGRESS.md" in read("README.md")
    text = read("docs/ops/PROGRESS.md")
    for milestone in ("M1", "M2", "M3", "M4", "M5"):
        assert milestone in text, f"进度文档缺少 {milestone}"


@pytest.mark.parametrize(
    "keyword",
    ["卡片", "三重权重", "记忆本", "做梦", "identity", "压缩", "抽取"],
)
def test_m2_doc_covers_core_concepts(keyword):
    assert keyword in read("docs/design/M2-memory.md"), f"M2 文档缺少核心概念：{keyword}"


def test_m2_doc_states_forgetting_never_deletes():
    text = read("docs/design/M2-memory.md")
    assert "只影响" in text and "不影响存储" in text


def test_m2_doc_documents_defaults():
    text = read("docs/design/M2-memory.md")
    for key in [
        "CARD_WEIGHT_THRESHOLD",
        "CONTEXT_SOFT_TOKEN_BUDGET",
        "CONTEXT_HARD_TOKEN_BUDGET",
        "DECAY_HALF_LIFE_DAYS",
        "DREAM_AT",
    ]:
        assert key in text, f"M2 文档缺少配置项：{key}"


def test_mvp_memory_section_points_to_new_design():
    text = read("docs/design/MVP.md")
    assert "M2-memory.md" in text
    assert "已被" in text and "取代" in text


def test_persona_doc_exists():
    assert (DOCS / "persona" / "dafeiyu.md").exists()


def test_license_is_mit_and_linked_from_readme():
    """仓库公开后别人得知道能不能用：LICENSE 在、是 MIT、README 指得到。"""
    assert (ROOT / "LICENSE").exists()
    license_text = read("LICENSE")
    assert "MIT License" in license_text
    assert "Copyright (c) 2026" in license_text
    assert "LICENSE" in read("README.md")


def test_companion_v2_design_is_linked_and_covers_every_module():
    """下一版设计必须能回答：记忆、梗库、三个渠道、可靠性、分期。"""
    assert (DOCS / "design" / "companion-v2.md").exists()
    assert "docs/design/companion-v2.md" in read("README.md")
    text = read("docs/design/companion-v2.md")
    for keyword in ("记忆 v2", "表情包", "梗百科", "QQ 空间", "B 站", "抖音", "Guard", "审计", "分期", "风险"):
        assert keyword in text, f"设计方案缺少：{keyword}"
