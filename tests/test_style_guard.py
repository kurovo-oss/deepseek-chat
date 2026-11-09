"""风格守卫：只删客服腔，不误伤正常回复。"""

from qqbot import style_guard


def test_removes_assistant_sentence():
    text = "听起来你最近很累。那你先躺会儿。"

    assert style_guard.soften(text) == "那你先躺会儿。"


def test_keeps_normal_reply():
    text = "骂你啥啦\n不是，他凭什么骂你呀"

    assert style_guard.soften(text) == text


def test_keeps_original_if_everything_is_removed():
    text = "我能理解你的感受。"

    assert style_guard.soften(text) == text


def test_hits_reports_phrases():
    assert "首先" in style_guard.hits("首先，你要早点睡")


def test_empty_text_is_safe():
    assert style_guard.soften("") == ""
    assert style_guard.hits(None) == []
