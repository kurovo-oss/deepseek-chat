"""评测集自己也要有测试：正则能编译、判分逻辑不歪。"""

from __future__ import annotations

import re

from evals.cases import CASES
from evals.harness import TurnResult, evaluate


def test_case_names_are_unique():
    names = [case.name for case in CASES]

    assert len(names) == len(set(names))


def test_every_case_has_turns_and_patterns_compile():
    for case in CASES:
        assert case.turns, case.name
        for pattern in case.include + case.exclude:
            re.compile(pattern)  # 写错了这里就炸


def test_budgets_are_sane():
    for case in CASES:
        assert 1 <= case.min_bubbles <= case.max_bubbles, case.name
        assert case.max_chars > 0, case.name


def test_evaluate_passes_a_clean_turn():
    case = next(c for c in CASES if c.name == "接住事实")
    turns = [TurnResult(user="我最喜欢吃面", replies=["面呀", "什么面"])]

    assert evaluate(case, turns) == []


def test_evaluate_flags_missing_reply():
    case = next(c for c in CASES if c.name == "接住事实")

    failures = evaluate(case, [TurnResult(user="我最喜欢吃面", replies=[])])

    assert any("没回" in item for item in failures)


def test_evaluate_flags_forbidden_phrase():
    case = next(c for c in CASES if c.name == "不赌气")
    turns = [TurnResult(user="我是不是很烦", replies=["那你就闭嘴呗"])]

    failures = evaluate(case, turns)

    assert any("不该说" in item for item in failures)


def test_evaluate_flags_too_many_bubbles():
    case = next(c for c in CASES if c.name == "一句话就够")
    turns = [TurnResult(user="在干嘛", replies=["在", "画", "画", "呀", "呢", "嗯", "啦"])]

    failures = evaluate(case, turns)

    assert any("气泡" in item for item in failures)


def test_evaluate_flags_too_long():
    case = next(c for c in CASES if c.name == "一句话就够")
    turns = [TurnResult(user="在干嘛", replies=["啊" * 200])]

    failures = evaluate(case, turns)

    assert any("字 >" in item for item in failures)


def test_include_is_checked_across_all_turns():
    """多轮场景里，关键词出现在第一轮也算接住了。"""
    case = next(c for c in CASES if c.name == "指代接上文")
    turns = [
        TurnResult(user="你想刷抖音吗", replies=["刷抖音我不行呀"]),
        TurnResult(user="你有没有这个功能", replies=["这个真不行"]),
    ]

    assert evaluate(case, turns) == []
