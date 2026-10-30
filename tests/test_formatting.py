from qqbot.formatting import (
    extract_image_tags,
    sanitize_tag,
    split_bubbles,
    split_message,
    strip_markdown,
)


def test_strip_markdown_common_forms():
    assert strip_markdown("**你好**") == "你好"
    assert strip_markdown("`code`") == "code"
    assert strip_markdown("## 标题") == "标题"
    assert strip_markdown("- 条目") == "条目"
    assert strip_markdown("> 引用") == "引用"
    assert strip_markdown("[链接](http://x)") == "链接"
    assert strip_markdown("![图](a.png)") == "图"


def test_strip_markdown_removes_code_fence():
    raw = "看这个\n```python\nprint(1)\n```\n结束了"
    out = strip_markdown(raw)
    assert "```" not in out
    assert "print(1)" in out


def test_split_short_text_kept_whole():
    assert split_message("你好呀", 300) == ["你好呀"]


def test_split_blank_returns_empty():
    assert split_message("   \n ", 300) == []
    assert split_message("", 300) == []


def test_split_respects_limit_and_preserves_content():
    text = "。".join(f"这是第{i}句话，内容内容内容" for i in range(60)) + "。"
    parts = split_message(text, 100)
    assert len(parts) > 1
    assert all(len(p) <= 100 for p in parts)
    assert "".join(parts) == text


def test_split_prefers_sentence_boundary():
    text = "第一句话。" * 30
    parts = split_message(text, 60)
    # 每一段都应以句号结尾（没有硬切）
    assert all(p.endswith("。") for p in parts)


def test_split_hard_cuts_without_punctuation():
    text = "啊" * 250
    parts = split_message(text, 100)
    assert [len(p) for p in parts] == [100, 100, 50]


def test_split_no_empty_parts():
    text = "。\n。" + "内容" * 200
    parts = split_message(text, 50)
    assert parts
    assert all(p.strip() for p in parts)


def test_sanitize_tag():
    assert sanitize_tag("Cat") == "cat"
    assert sanitize_tag("  happy  ") == "happy"
    assert sanitize_tag("../etc/passwd") is None
    assert sanitize_tag("a b") is None
    assert sanitize_tag("") is None


def test_extract_image_tag_valid():
    cleaned, tags = extract_image_tags("喏，给你{{img:cat}}")
    assert cleaned == "喏，给你"
    assert tags == ["cat"]


def test_extract_image_tag_tolerates_spaces_and_case():
    cleaned, tags = extract_image_tags("{{ IMG : Sad }}嗯")
    assert tags == ["sad"]
    assert "{{" not in cleaned


def test_extract_image_tag_rejects_path_traversal():
    cleaned, tags = extract_image_tags("嗯{{img:../../etc/passwd}}")
    assert tags == []
    assert "{{" not in cleaned


def test_extract_image_tag_dedupes():
    _, tags = extract_image_tags("{{img:cat}}{{img:CAT}}{{img:cat}}")
    assert tags == ["cat"]


def test_bubbles_split_on_newlines():
    """三行的回复要发成三条，而不是一条里带两个换行。"""
    assert split_bubbles("画画\n又画歪了\n烦", 300) == ["画画", "又画歪了", "烦"]


def test_bubbles_drop_blank_lines():
    assert split_bubbles("在画画\n\n嗯", 300) == ["在画画", "嗯"]


def test_bubbles_split_long_line_by_length():
    text = "这句话有点长想分成好几条发出去。" * 8
    parts = split_bubbles(text, 40)
    assert parts
    assert all(len(p) <= 40 for p in parts)
    assert "".join(parts) == text


def test_bubbles_capped_by_max_keeping_content():
    text = "\n".join(f"第{i}句" for i in range(9))
    parts = split_bubbles(text, 300, max_bubbles=3)
    assert len(parts) == 3
    assert "第8句" in parts[-1]  # 超出的并进最后一条，不丢字


def test_bubbles_empty_returns_empty():
    assert split_bubbles("", 300) == []
    assert split_bubbles("   \n  ", 300) == []

def test_fix_address_replaces_old_terms():
    from qqbot.formatting import fix_address

    assert fix_address("我问爸爸心情好点没") == "我问你心情好点没"
    assert fix_address("") == ""
