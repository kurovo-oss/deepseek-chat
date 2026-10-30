"""文档一致性：链接不断、旧设计有指向、新设计关键词齐全。

文档腐烂往往无声无息——改了实现忘了改文档，或者把旧方案当成了现行方案。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_readme_markdown_links_exist():
    text = read("README.md")
    links = re.findall(r"\]\(([^)\s]+\.md)\)", text)
    assert links, "README 里没有任何文档链接"
    missing = [link for link in links if not (ROOT / link).exists()]
    assert not missing, f"README 里存在失效链接：{missing}"


def test_m2_design_doc_exists_and_is_linked():
    assert (ROOT / "docs" / "M2-memory.md").exists()
    assert "docs/M2-memory.md" in read("README.md")


def test_progress_doc_is_linked_and_covers_milestones():
    """进度文档必须覆盖每个里程碑，否则它会慢慢变成谎言。"""
    assert (ROOT / "docs" / "PROGRESS.md").exists()
    assert "docs/PROGRESS.md" in read("README.md")
    text = read("docs/PROGRESS.md")
    for milestone in ("M1", "M2", "M3", "M4", "M5"):
        assert milestone in text, f"进度文档缺少 {milestone}"


@pytest.mark.parametrize(
    "keyword",
    ["卡片", "三重权重", "记忆本", "做梦", "identity", "压缩", "抽取"],
)
def test_m2_doc_covers_core_concepts(keyword):
    assert keyword in read("docs/M2-memory.md"), f"M2 文档缺少核心概念：{keyword}"


def test_m2_doc_states_forgetting_never_deletes():
    text = read("docs/M2-memory.md")
    assert "只影响" in text and "不影响存储" in text


def test_m2_doc_documents_defaults():
    text = read("docs/M2-memory.md")
    for key in [
        "CARD_WEIGHT_THRESHOLD",
        "CONTEXT_SOFT_TOKEN_BUDGET",
        "CONTEXT_HARD_TOKEN_BUDGET",
        "DECAY_HALF_LIFE_DAYS",
        "DREAM_AT",
    ]:
        assert key in text, f"M2 文档缺少配置项：{key}"


def test_mvp_memory_section_points_to_new_design():
    text = read("docs/MVP.md")
    assert "M2-memory.md" in text
    assert "已被" in text and "取代" in text


def test_persona_doc_exists():
    assert (ROOT / "docs" / "persona" / "dafeiyu.md").exists()


def test_license_is_mit_and_linked_from_readme():
    """仓库公开后别人得知道能不能用：LICENSE 在、是 MIT、README 指得到。"""
    assert (ROOT / "LICENSE").exists()
    license_text = read("LICENSE")
    assert "MIT License" in license_text
    assert "Copyright (c) 2026" in license_text
    assert "LICENSE" in read("README.md")
