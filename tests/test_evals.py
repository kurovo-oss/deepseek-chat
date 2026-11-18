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


def test_evaluate_flags_too_many_questions():
    case = next(c for c in CASES if c.name == "别连问")
    turns = [TurnResult(user="我今天好累", replies=["怎么啦", "是上班吗", "几点睡的"])]

    failures = evaluate(case, turns)

    assert any("查户口" in item for item in failures)


def test_evaluate_counts_forbidden_hits():
    case = next(c for c in CASES if c.name == "别老提画画")
    turns = [
        TurnResult(user="在干嘛", replies=["在画画"]),
        TurnResult(user="今天好累", replies=["画画好烦"]),
        TurnResult(user="晚上吃什么", replies=["随便呀"]),
    ]

    failures = evaluate(case, turns)

    assert any("出现" in item and "> 1" in item for item in failures)


def _fake_result(case, ok):
    from evals.harness import CaseResult

    return CaseResult(case=case, turns=[], failures=[] if ok else ["没过"])


def _stub_settings(monkeypatch, run):
    """评测入口不该读开发者本机的 `.env`。

    CI 上没有这个文件，`load_settings(ROOT)` 会直接抛 ConfigError——
    测试要验的是「三局两胜」的判定逻辑，不是配置加载。
    """
    from types import SimpleNamespace

    fake = SimpleNamespace(
        model_base_url="http://example.invalid/v1",
        model_api_key="sk-test",
        model_id="deepseek-flash",
        request_timeout=1,
    )
    monkeypatch.setattr(run, "load_settings", lambda _root: fake)


def test_majority_vote_lets_one_bad_draw_slide(monkeypatch, capsys):
    """模型偶尔抽风不该判死刑：三局两胜。"""
    from evals import run
    from evals.cases import CASES

    case = CASES[0]
    sequence = [_fake_result(case, False), _fake_result(case, True), _fake_result(case, True)]
    monkeypatch.setattr(run, "run_case", lambda *a, **k: sequence.pop(0))
    _stub_settings(monkeypatch, run)

    code = run.main(["--only", case.name[:2]])

    assert code == 0
    assert "✓" in capsys.readouterr().out


def test_two_bad_draws_still_fail(monkeypatch, capsys):
    from evals import run
    from evals.cases import CASES

    case = CASES[0]
    sequence = [_fake_result(case, True), _fake_result(case, False), _fake_result(case, False)]
    monkeypatch.setattr(run, "run_case", lambda *a, **k: sequence.pop(0))
    _stub_settings(monkeypatch, run)

    run.main(["--only", case.name[:2]])

    assert "✗" in capsys.readouterr().out
