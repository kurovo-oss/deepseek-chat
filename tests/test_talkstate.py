"""对话状态：她知道自己的问题还悬着没有。"""

from qqbot import talkstate


def msg(role, content):
    return {"role": role, "content": content}


def test_asks_question():
    assert talkstate.asks_question("你吃了吗")
    assert talkstate.asks_question("干嘛呢？")
    assert talkstate.asks_question("什么时候回来")
    assert not talkstate.asks_question("我在画画")


def test_is_filler():
    assert talkstate.is_filler("嗯")
    assert talkstate.is_filler("哦哦")
    assert talkstate.is_filler("……")
    assert not talkstate.is_filler("刚洗完澡")


def test_phrase_trims_and_shortens():
    assert talkstate.phrase("，晚饭吃什么？") == "晚饭吃什么"
    long = talkstate.phrase("今天在公司被领导当众说了两句特别难听的话")
    assert len(long) <= 16
    assert long.endswith("…")


def test_note_says_he_already_answered():
    messages = [
        msg("assistant", "你吃了吗"),
        msg("user", "刚吃完，面条"),
    ]

    text = talkstate.note(messages)

    assert "你吃了吗" in text
    assert "已经答了" in text


def test_note_says_he_did_not_answer():
    messages = [
        msg("assistant", "你吃了吗"),
        msg("user", "嗯"),
    ]

    text = talkstate.note(messages)

    assert "没正面答" in text


def test_note_warns_about_asking_too_many_questions():
    messages = [
        msg("user", "我今天好累"),
        msg("assistant", "怎么啦？"),
        msg("user", "加班"),
        msg("assistant", "加到几点呀"),
        msg("user", "十点"),
    ]

    text = talkstate.note(messages)

    assert "连着问" in text


def test_note_lists_recent_topics():
    messages = [
        msg("user", "今天加班到十点"),
        msg("assistant", "这么晚"),
        msg("user", "领导又不高兴"),
        msg("assistant", "他怎么了"),
    ]

    text = talkstate.note(messages)

    assert "刚才在聊" in text
    assert "加班" in text


def test_note_is_empty_without_context():
    assert talkstate.note([]) == ""
    assert talkstate.note([msg("user", "嗯")]) == ""


def test_repeated_topic_flags_the_obsession():
    messages = [
        msg("assistant", "在画画呢，尾巴又描歪了"),
        msg("user", "哦"),
        msg("assistant", "画画好难呀"),
        msg("user", "嗯"),
        msg("assistant", "又在画画了，画得烦"),
    ]

    assert talkstate.repeated_topic(messages) == "画画"


def test_repeated_topic_needs_enough_messages():
    messages = [msg("assistant", "画画"), msg("assistant", "画画")]

    assert talkstate.repeated_topic(messages) == ""


def test_repeated_topic_ignores_common_words():
    messages = [msg("assistant", "这个时候我什么都不知道"), msg("assistant", "这个时候真的什么都不知道"), msg("assistant", "这个时候什么都不知道")]

    assert talkstate.repeated_topic(messages) == ""


def test_note_warns_about_the_same_topic():
    messages = [
        msg("assistant", "在画画呢"),
        msg("user", "哦"),
        msg("assistant", "画画好难呀"),
        msg("user", "嗯"),
        msg("assistant", "还在画画呢"),
    ]

    text = talkstate.note(messages)

    assert "老是提" in text
    assert "画画" in text
