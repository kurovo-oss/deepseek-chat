"""本轮计划：只挑当场最重要的几条，不额外调模型。"""

from qqbot.turnplan import make_plan


def test_comfort_plan_does_not_ask_for_details():
    plan = make_plan("今天被领导骂了，好累")

    assert plan.intent == "在难受"
    assert "追问细节" in plan.avoid
    assert "给方案" in plan.avoid
    assert plan.bubbles == 1


def test_question_plan_answers_first():
    plan = make_plan("你在干嘛呀")

    assert plan.intent == "在问事"
    assert "先答他那件事" in plan.stance


def test_playful_plan_is_not_mean():
    plan = make_plan("哈哈你好好笑")

    assert plan.intent == "在逗你"
    assert "刻薄" in plan.stance


def test_too_many_recent_questions_are_suppressed():
    history = [
        {"role": "user", "content": "嗯"},
        {"role": "assistant", "content": "你吃了吗"},
        {"role": "user", "content": "嗯"},
        {"role": "assistant", "content": "今天忙不忙"},
        {"role": "user", "content": "还好"},
        {"role": "assistant", "content": "那你想我吗"},
    ]
    plan = make_plan("还行", history)

    assert "反问" in plan.avoid


def test_repeated_openings_are_named():
    history = [
        {"role": "assistant", "content": "嗯嗯，好呀"},
        {"role": "assistant", "content": "嗯嗯，知道啦"},
        {"role": "assistant", "content": "嗯嗯，等你"},
    ]
    plan = make_plan("在吗", history)

    assert plan.opening_avoid == "嗯嗯"
    assert "嗯嗯" in plan.render()


def test_tired_mood_shortens_the_stance():
    plan = make_plan("今天怎么样", [], {"tired": 0.9})

    assert "有点累" in plan.stance


def test_render_is_compact():
    text = make_plan("今天加班到十点").render()

    assert "【这一轮】" in text
    assert len(text) < 220

def test_pronoun_plan_names_the_previous_topic():
    history = [
        {"role": "user", "content": "你想刷抖音吗"},
        {"role": "assistant", "content": "想呀"},
        {"role": "user", "content": "你有没有这个功能"},
    ]

    plan = make_plan("你有没有这个功能", history)

    assert plan.reference == "你想刷抖音吗"
    assert "这个/那个" in plan.render()


def test_no_reference_line_without_pronoun():
    plan = make_plan("今天吃什么", [{"role": "user", "content": "今天吃什么"}])

    assert plan.reference == ""
