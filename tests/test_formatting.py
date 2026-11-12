from qqbot.formatting import (
    extract_image_tags,
    extract_voice_tags,
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


def test_extract_voice_tags_removes_marker_and_keeps_content():
    cleaned, parts = extract_voice_tags("我小声说\n{{语音：喂，能听到吗？}}\n这次呢")
    assert cleaned == "我小声说\n\n这次呢"
    assert parts == ["喂，能听到吗？"]


def test_extract_voice_tags_accepts_english_and_caps():
    cleaned, parts = extract_voice_tags("{{VOICE: 晚安呀}}")
    assert cleaned == ""
    assert parts == ["晚安呀"]


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


class FakeRng:
    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value


def test_reshape_merges_bubbles_sometimes():
    from qqbot.formatting import reshape_bubbles

    out = reshape_bubbles(["骂你啥啦", "不是，他凭什么骂你呀"], FakeRng(0.1))

    assert len(out) == 1
    assert "骂你啥啦" in out[0] and "凭什么" in out[0]


def test_reshape_splits_a_long_bubble():
    from qqbot.formatting import reshape_bubbles

    text = "我今天出门忘带钥匙了，在楼下站了快半小时，最后还是房东来给我开的门"
    out = reshape_bubbles([text], FakeRng(0.3))

    assert len(out) == 2
    assert text.startswith(out[0]) and text.endswith(out[1])
    assert not out[0].endswith("，")  # 切开时不留半个逗号


def test_reshape_usually_leaves_it_alone():
    from qqbot.formatting import reshape_bubbles

    assert reshape_bubbles(["好呀", "那你呢"], FakeRng(0.9)) == ["好呀", "那你呢"]


def test_reshape_handles_empty():
    from qqbot.formatting import reshape_bubbles

    assert reshape_bubbles([], FakeRng(0.1)) == []


def test_merge_does_not_double_punctuate():
    from qqbot.formatting import reshape_bubbles

    out = reshape_bubbles(["诶？", "啥功能呀", "你说清楚点嘛"], FakeRng(0.1))

    assert out == ["诶？啥功能呀，你说清楚点嘛"]


def test_never_cuts_a_word_in_half():
    """没有逗号的句子不硬切——"不知道"被劈成"不知/道…"是肉眼可见的假。"""
    from qqbot.formatting import _cut_in_two
    import random

    assert _cut_in_two("你们领导是不是不知道你家里有人在等你呀", random.Random(0)) is None


def test_still_splits_at_a_comma():
    from qqbot.formatting import _cut_in_two
    import random

    pieces = _cut_in_two("你先去吃点东西，我等会儿再找你说话", random.Random(0))

    assert pieces is not None
    assert len(pieces) == 2
